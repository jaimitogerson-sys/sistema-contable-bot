import os
import requests
import json
import time
import threading
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)  # CORRECTO

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FOLDER_ID = os.getenv("FOLDER_ID")
GOOGLE_CREDENTIALS = json.loads(os.getenv("GOOGLE_CREDENTIALS"))

def enviar_mensaje(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": texto}
    requests.post(url, data=data)

# ---------------- GOOGLE DRIVE ----------------

creds = service_account.Credentials.from_service_account_info(
    GOOGLE_CREDENTIALS,
    scopes=["https://www.googleapis.com/auth/drive"]
)

drive_service = build("drive", "v3", credentials=creds)
archivos_vistos = set()

# ---------------- MINI SCRIPT DE PRUEBA ----------------
def prueba_drive():
    try:
        resultados = drive_service.files().list(
            q=f"'{FOLDER_ID}' in parents",
            fields="files(id, name)"
        ).execute()
        archivos = resultados.get("files", [])
        if archivos:
            print("Archivos en la carpeta:")
            for archivo in archivos:
                print(f"- {archivo['name']}")
        else:
            print("La carpeta está vacía o el bot no tiene acceso.")
    except Exception as e:
        print("Error al probar Drive:", e)

prueba_drive()  # Ejecutar al iniciar

# ---------------- HILO DE REVISIÓN ----------------
def revisar_drive():
    while True:
        try:
            resultados = drive_service.files().list(
                q=f"'{FOLDER_ID}' in parents",
                fields="files(id, name)"
            ).execute()

            archivos = resultados.get("files", [])
            for archivo in archivos:
                if archivo["id"] not in archivos_vistos:
                    archivos_vistos.add(archivo["id"])
                    enviar_mensaje(f"📂 Nuevo archivo detectado: {archivo['name']}")
            time.sleep(20)
        except Exception as e:
            print("Error revisando Drive:", e)
            time.sleep(30)

hilo_drive = threading.Thread(target=revisar_drive)
hilo_drive.daemon = True
hilo_drive.start()

# ---------------- FLASK ----------------

@app.route("/", methods=["GET"])
def home():
    return "Bot funcionando"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if data and "message" in data:
        texto = data["message"].get("text", "")
        if texto:
            enviar_mensaje(f"Recibido: {texto}")
    return "ok"

if name == "main":  # CORRECTO
    app.run(host="0.0.0.0", port=10000)
