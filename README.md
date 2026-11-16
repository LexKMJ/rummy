# Rummi

Juego tipo Rummikub en línea: salas con link para compartir, 2-4 jugadores y bots opcionales. Hecho con Flask.

## Ejecutar en local
    pip install -r requirements.txt
    python app.py   # abre http://127.0.0.1:5000

## Desplegar en Render
New → Blueprint → selecciona este repositorio (usa `render.yaml`).
El estado de las partidas vive en memoria, por eso se usa un solo worker.
