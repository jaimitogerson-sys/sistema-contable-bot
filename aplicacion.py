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

# ================== MENSAJES BONITOS ==================
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

archivos_vistos = set()

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
            "💳 Realiza tu pago para reactivar.\n"
            "👉 [Pagar ahora](https://tulinkdepago.com)"
        )
        cur.close()
        conn.close()
        return False

    cur.close()
    conn.close()
    return True

# ================== AGENTE INTELIGENTE ==================
def agente_procesador(archivos, instruccion):
    inicio = time.time()
    os.makedirs("./resultados", exist_ok=True)

    datos = []
    for archivo in archivos:
        datos.append({
            "Archivo": archivo["name"],
            "Procesado": "Sí",
            "Fecha": datetime.now()
        })

    df = pd.DataFrame(datos)
    ruta = f"./resultados/Resultado_{int(time.time())}.xlsx"
    df.to_excel(ruta, index=False)

    tiempo_total = round(time.time() - inicio, 2)

    return ruta, tiempo_total

# ================== MONITOREO DRIVE ==================
def revisar_drive():
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

                instruccion = "Procesamiento automático"

                # 🔥 MOSTRAR NOMBRES
                nombres = "\n".join([f"📄 `{a['name']}`" for a in nuevos])

                enviar_mensaje(
                    f"📥 *Nuevos archivos detectados*\n\n"
                    f"{nombres}\n\n"
                    f"📂 Total: {len(nuevos)} archivo(s)\n"
                    f"⚙ Iniciando procesamiento..."
                )

                ruta, tiempo_total = agente_procesador(nuevos, instruccion)

                enviar_mensaje(
                    f"✅ *FINALIZADO*\n\n"
                    f"⏱ Tiempo total: {tiempo_total} segundos\n"
                    f"📁 Ubicación: `{ruta}`"
                )

            time.sleep(20)

        except Exception as e:
            print("Error:", e)
            time.sleep(30)

# ================== WEBHOOK PAGO ==================
@app.route("/pago_webhook", methods=["POST"])
def pago_webhook():
    data = request.json
    estado_pago = data.get("estado_pago")
    nuevo_vencimiento = data.get("nuevo_vencimiento")

    if estado_pago != "aprobado":
        return "Pago no aprobado"

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE clientes SET estado='ACTIVO', vencimiento=%s WHERE chat_id=%s",
        (nuevo_vencimiento, CHAT_ID)
    )
    conn.commit()
    cur.close()
    conn.close()

    enviar_mensaje(
        "💳 *PAGO CONFIRMADO*\n"
        "🔓 Servicio reactivado correctamente.\n"
        f"📅 Nuevo vencimiento: {nuevo_vencimiento}"
    )

    return "ok"

# ================== INICIO ==================
hilo = threading.Thread(target=revisar_drive)
hilo.daemon = True
hilo.start()

@app.route("/")
def home():
    return "Sistema activo"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
