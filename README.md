# IAZENIX

Colle un lien YouTube : le serveur télécharge la vidéo, la transcrit (Whisper),
demande à Claude les meilleurs passages, puis découpe chaque short en 9:16.

## Lancer en local
1. Installe Python 3.10+ et ffmpeg.
2. `pip install -r requirements.txt`
3. `export ANTHROPIC_API_KEY=ta_clé` (Windows : `set ANTHROPIC_API_KEY=ta_clé`)
4. `uvicorn server:app --port 8000`
5. Ouvre http://localhost:8000

## Mettre en ligne
Le `Dockerfile` fonctionne tel quel sur Railway, Render, Fly.io ou un VPS.
Ajoute la variable d'environnement `ANTHROPIC_API_KEY`. Prévois 2 Go de RAM minimum
et du disque : les shorts sont gardés dans `data/`.

## Réglages
- `WHISPER_MODEL` : `tiny`, `base`, `small` (défaut), `medium`. Plus gros = plus précis, plus lent.
- `CLAUDE_MODEL` : modèle utilisé pour choisir les moments forts.

## À savoir
- Le bouton d'envoi de fichier est le plus fiable : YouTube bloque souvent les téléchargements faits depuis un serveur. Taille maximale réglable avec `MAX_MB` (1000 par défaut).
- Utilise uniquement des vidéos dont tu as les droits.
- Un seul traitement à la fois. Les tâches sont en mémoire : elles disparaissent au redémarrage.
- Le recadrage est centré. Il ne suit pas les visages.
