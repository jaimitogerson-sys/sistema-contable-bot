import os
import requests
import json
import time
import threading
import psycopg2
from datetime import datetime, timedelta
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import pandas as pd

app = Flask(__name__)

# ================== VARIABLES ==================
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
FOLDER_ID = os.getenv("FOLDER_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
GOOGLE_CREDENTIALS = json.loads(os.getenv("GOOGLE_CREDENTIALS"))

# ================== VARIABLES GLOBALES ==================
archivos_vistos = set()
archivos_pendientes = []
instruccion_pendiente = None

# ================== CONEXIÓN DB ==================
def get_db():
    return psycopg2.connect(DATABASE_URL)

def crear_tablas():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            chat_id TEXT UNIQUE,
            estado TEXT DEFAULT 'ACTIVO',
            vencimiento DATE
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

crear_tablas()

# ================== MENSAJES ==================
def enviar_mensaje(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown"
    }
    requests.post(url, data=data)

# ================== GOOGLE DRIVE ==================
creds = service_account.Credentials.from_service_account_info(
    GOOGLE_CREDENTIALS,
    scopes=["https://www.googleapis.com/auth/drive"]
)
drive_service = build("drive", "v3", credentials=creds)

# ================== VERIFICAR ESTADO CLIENTE ==================
def verificar_estado():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT estado, vencimiento FROM clientes WHERE chat_id = %s", (CHAT_ID,))
    resultado = cur.fetchone()

    if not resultado:
        vencimiento = datetime.now().date() + timedelta(days=30)
        cur.execute(
            "INSERT INTO clientes (chat_id, estado, vencimiento) VALUES (%s, %s, %s)",
            (CHAT_ID, "ACTIVO", vencimiento)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True

    estado, vencimiento = resultado
    hoy = datetime.now().date()

    if estado != "ACTIVO" or hoy > vencimiento:
        enviar_mensaje(
            "🚫 *SERVICIO SUSPENDIDO*\n\n"
            "Tu licencia ha vencido.\n"
            "💳 Realiza tu pago para reactivar."
        )
        cur.close()
        conn.close()
        return False

    cur.close()
    conn.close()
    return True

# ================== AGENTE PROCESADOR ==================
def agente_procesador(archivos, instruccion):
    inicio = time.time()
    os.makedirs("./resultados", exist_ok=True)

    datos = []
    for archivo in archivos:
        datos.append({
            "Archivo": archivo["name"],
            "Instrucción": instruccion,
            "Procesado": "Sí",
            "Fecha": datetime.now()
        })

    df = pd.DataFrame(datos)
    ruta = f"./resultados/Resultado_{int(time.time())}.xlsx"
    df.to_excel(ruta, index=False)

    tiempo_total = round(time.time() - inicio, 2)

    return ruta, tiempo_total

# ================== EJECUTAR PROCESO ==================
def ejecutar_proceso():
    global archivos_pendientes
    global instruccion_pendiente

    enviar_mensaje("🚀 Ejecutando instrucción...")

    ruta, tiempo_total = agente_procesador(
        archivos_pendientes,
        instruccion_pendiente
    )

    enviar_mensaje(
        f"✅ *FINALIZADO*\n\n"
        f"⏱ Tiempo total: {tiempo_total} segundos\n"
        f"📁 Ubicación: {ruta}"
    )

    archivos_pendientes = []
    instruccion_pendiente = None

# ================== MONITOREO DRIVE ==================
def revisar_drive():
    global archivos_pendientes

    while True:
        try:
            if not verificar_estado():
                time.sleep(60)
                continue

            resultados = drive_service.files().list(
                q=f"'{FOLDER_ID}' in parents",
                fields="files(id, name, createdTime)"
            ).execute()

            archivos = resultados.get("files", [])
            nuevos = [a for a in archivos if a["id"] not in archivos_vistos]

            if nuevos:
                for a in nuevos:
                    archivos_vistos.add(a["id"])

                archivos_pendientes = nuevos

                nombres = "\n".join([f"📄 {a['name']}" for a in nuevos])

                enviar_mensaje(
                    f"📥 *NUEVO ARCHIVO DETECTADO*\n\n"
                    f"{nombres}\n\n"
                    f"📂 Total: {len(nuevos)} archivo(s)\n\n"
                    f"🤖 ¿Qué deseas que haga con estos archivos?"
                )

            time.sleep(20)

        except Exception as e:
            print("Error:", e)
            time.sleep(30)

# ================== WEBHOOK TELEGRAM ==================
@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def telegram_webhook():
    global instruccion_pendiente

    data = request.json

    if "message" in data:
        texto = data["message"].get("text", "")

        if texto.lower() in ["si", "confirmar", "confirmo"]:
            if archivos_pendientes:
                ejecutar_proceso()
            return "ok"

        if archivos_pendientes:
            instruccion_pendiente = texto
            enviar_mensaje(
                f"⚠️ Vas a ejecutar:\n\n"
                f"📝 {texto}\n\n"
                f"📂 Sobre {len(archivos_pendientes)} archivo(s)\n\n"
                f"Escribe *SI* para confirmar."
            )
        else:
            enviar_mensaje("No hay archivos pendientes.")

    return "ok"

# ================== RUTA PRINCIPAL ==================
@app.route("/")
def home():
    return "Sistema activo"

# ================== INICIAR HILO ==================
hilo = threading.Thread(target=revisar_drive)
hilo.daemon = True
hilo.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
