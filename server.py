import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

import anthropic
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel
from pydantic import BaseModel

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
WHISPER_SIZE = os.getenv("WHISPER_MODEL", "small")
MAX_MB = int(os.getenv("MAX_MB", "1000"))
DATA = Path("data")
DATA.mkdir(exist_ok=True)

YT = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/))([\w-]{11})")
PROMPT = (
    "Tu reçois la transcription d'une vidéo YouTube, ligne par ligne, au format [début-fin] texte "
    "(secondes). Choisis les 3 à 5 meilleurs passages pour des shorts verticaux : une accroche forte "
    "dès la première phrase, une idée complète, une fin nette. Chaque passage dure entre 25 et 60 "
    "secondes et commence et finit sur des limites de phrases. Réponds uniquement par un tableau JSON "
    "d'objets avec les clés : start (nombre), end (nombre), title (8 mots maximum, dans la langue de "
    "la vidéo), score (0 à 100, potentiel viral), reason (une phrase en français)."
)

app = FastAPI()
client = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY
jobs: dict = {}
slot = threading.Semaphore(1)  # un traitement à la fois
_whisper = None


def whisper():
    global _whisper
    if _whisper is None:
        _whisper = WhisperModel(WHISPER_SIZE, compute_type="int8")
    return _whisper


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def pick(transcript: str, total: float):
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=PROMPT,
        messages=[{"role": "user", "content": transcript}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text")
    data = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
    clips = []
    for c in data:
        s, e = max(0.0, float(c["start"])), min(total, float(c["end"]))
        if 15 <= e - s <= 90:
            clips.append({"start": s, "end": e, "title": str(c["title"]),
                          "score": int(c["score"]), "reason": str(c["reason"])})
    return clips[:5]


def pipeline(jid: str, url):
    job = jobs[jid]
    d = DATA / jid
    d.mkdir(exist_ok=True)
    with slot:
        try:
            src = d / "source.mp4"
            if url:
                job["step"] = "Téléchargement de la vidéo"
                run(["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]",
                     "--merge-output-format", "mp4", "-o", str(d / "source.%(ext)s"), url])

            job["step"] = "Transcription"
            segments, _ = whisper().transcribe(str(src), vad_filter=True)
            lines, total = [], 0.0
            for s in segments:
                lines.append(f"[{int(s.start)}-{int(s.end)}] {s.text.strip()}")
                total = s.end
            if not lines:
                raise RuntimeError("Aucune parole détectée dans cette vidéo.")

            job["step"] = "Choix des moments forts"
            clips = pick("\n".join(lines), total)
            if not clips:
                raise RuntimeError("Aucun passage adapté n'a été trouvé.")

            for i, c in enumerate(clips, 1):
                job["step"] = f"Découpe du short {i}/{len(clips)}"
                out = d / f"short{i}.mp4"
                run(["ffmpeg", "-y", "-ss", str(c["start"]), "-t", str(c["end"] - c["start"]),
                     "-i", str(src), "-vf", "crop='min(iw,ih*9/16)':ih,scale=1080:1920",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                     "-c:a", "aac", "-movflags", "+faststart", str(out)])
                c["url"] = f"/clips/{jid}/short{i}.mp4"
            src.unlink(missing_ok=True)
            job["clips"] = clips
            job["status"] = "done"
        except Exception as ex:  # noqa: BLE001
            job["status"] = "error"
            if isinstance(ex, RuntimeError):
                job["error"] = str(ex)
            elif url:
                job["error"] = ("YouTube a peut-être bloqué le téléchargement depuis le serveur. "
                                "Envoie ta vidéo directement avec le bouton de fichier.")
            else:
                job["error"] = "Le traitement a échoué. Vérifie ta vidéo et réessaie."


class Req(BaseModel):
    url: str


@app.post("/api/jobs")
def create(r: Req):
    if not YT.search(r.url):
        raise HTTPException(400, "Lien YouTube non reconnu.")
    jid = uuid.uuid4().hex[:10]
    jobs[jid] = {"status": "running", "step": "En file d'attente", "clips": []}
    threading.Thread(target=pipeline, args=(jid, r.url.strip()), daemon=True).start()
    return {"id": jid}


@app.get("/api/jobs/{jid}")
def status(jid: str):
    if jid not in jobs:
        raise HTTPException(404, "Tâche introuvable.")
    return jobs[jid]


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    jid = uuid.uuid4().hex[:10]
    d = DATA / jid
    d.mkdir(exist_ok=True)
    size, too_big = 0, False
    with open(d / "source.mp4", "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_MB * 1024 * 1024:
                too_big = True
                break
            out.write(chunk)
    if too_big:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(413, f"Fichier trop gros (maximum {MAX_MB} Mo).")
    jobs[jid] = {"status": "running", "step": "En file d'attente", "clips": []}
    threading.Thread(target=pipeline, args=(jid, None), daemon=True).start()
    return {"id": jid}


app.mount("/clips", StaticFiles(directory=DATA), name="clips")
app.mount("/", StaticFiles(directory="static", html=True), name="site")
