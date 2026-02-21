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
import openai
from PyPDF2 import PdfReader
import docx

app = Flask(__name__)

# ================== VARIABLES ==================
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GOOGLE_CREDENTIALS = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
OPENAI_KEY = os.getenv("OPENAI_KEY")
openai.api_key = OPENAI_KEY

# ================== TELEGRAM ==================
def enviar_mensaje(chat_id, texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"}
    requests.post(url, data=data)

# ================== BASE DE DATOS ==================
def get_db():
    return psycopg2.connect(DATABASE_URL)

def crear_tablas():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            chat_id TEXT UNIQUE,
            folder_id TEXT,
            estado TEXT DEFAULT 'ACTIVO',
            vencimiento DATE
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

crear_tablas()

# ================== GOOGLE DRIVE ==================
creds = service_account.Credentials.from_service_account_info(
    GOOGLE_CREDENTIALS,
    scopes=["https://www.googleapis.com/auth/drive"]
)
drive_service = build("drive", "v3", credentials=creds)
archivos_vistos = {}

# ================== VERIFICAR CLIENTE ==================
def verificar_estado(chat_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT estado, vencimiento FROM clientes WHERE chat_id=%s", (chat_id,))
    resultado = cur.fetchone()

    if not resultado:
        vencimiento = datetime.now().date() + timedelta(days=30)
        cur.execute("INSERT INTO clientes (chat_id, vencimiento) VALUES (%s, %s)", (chat_id, vencimiento))
        conn.commit()
        cur.close()
        conn.close()
        return True

    estado, vencimiento = resultado
    hoy = datetime.now().date()
    if estado != "ACTIVO" or hoy > vencimiento:
        enviar_mensaje(chat_id, "🚫 *SERVICIO SUSPENDIDO*\nTu licencia ha vencido.\n💳 [Pagar ahora](https://tulinkdepago.com)")
        cur.close()
        conn.close()
        return False

    cur.close()
    conn.close()
    return True

# ================== DRIVE FUNCIONES ==================
def buscar_carpeta(nombre, parent_id):
    query = f"mimeType='application/vnd.google-apps.folder' and name='{nombre}' and '{parent_id}' in parents and trashed=false"
    resultado = drive_service.files().list(q=query, fields="files(id, name)").execute()
    carpetas = resultado.get("files", [])
    return carpetas[0]["id"] if carpetas else None

def crear_carpeta_si_no_existe(nombre, parent_id):
    carpeta = buscar_carpeta(nombre, parent_id)
    if carpeta:
        return carpeta
    metadata = {"name": nombre, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    carpeta = drive_service.files().create(body=metadata, fields="id").execute()
    return carpeta["id"]

def crear_estructura_carpetas(ruta, folder_base):
    partes = ruta.split("/")
    parent_actual = folder_base
    for nombre in partes:
        parent_actual = crear_carpeta_si_no_existe(nombre, parent_actual)
    return parent_actual

def mover_archivo(file_id, carpeta_destino):
    archivo = drive_service.files().get(fileId=file_id, fields="parents").execute()
    padres_anteriores = ",".join(archivo.get("parents"))
    drive_service.files().update(fileId=file_id, addParents=carpeta_destino, removeParents=padres_anteriores, fields="id, parents").execute()

# ================== LECTURA DE ARCHIVOS ==================
def leer_archivo(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        reader = PdfReader(file_path)
        return "\n".join([p.extract_text() for p in reader.pages])
    elif ext in [".docx", ".doc"]:
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    elif ext in [".xlsx", ".xls"]:
        df = pd.read_excel(file_path)
        return df.to_string()
    else:
        with open(file_path, "r", errors="ignore") as f:
            return f.read()

# ================== IA ==================
def ejecutar_instruccion_ia(prompt):
    respuesta = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role":"user","content":prompt}],
        temperature=0.3
    )
    return respuesta['choices'][0]['message']['content']

# ================== MONITOREO ==================
def revisar_drive_cliente(chat_id, folder_id):
    if chat_id not in archivos_vistos:
        archivos_vistos[chat_id] = set()

    while True:
        try:
            if not verificar_estado(chat_id):
                time.sleep(60)
                continue

            resultados = drive_service.files().list(q=f"'{folder_id}' in parents and trashed=false", fields="files(id,name)").execute()
            archivos = resultados.get("files", [])
            nuevos = [a for a in archivos if a["id"] not in archivos_vistos[chat_id]]

            if nuevos:
                for a in nuevos:
                    archivos_vistos[chat_id].add(a["id"])

                nombres = "\n".join([f"📄 {a['name']}" for a in nuevos])
                enviar_mensaje(chat_id, f"📥 *NUEVO ARCHIVO DETECTADO*\n\n{nombres}\n\n📂 Total: {len(nuevos)} archivo(s)\n\n🤖 ¿Qué deseas que haga con estos archivos?")

            time.sleep(20)

        except Exception as e:
            print("Error:", e)
            time.sleep(30)

# ================== WEBHOOK TELEGRAM ==================
@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.json
    if "message" in data:
        chat_id = str(data["message"]["chat"]["id"])
        texto = data["message"].get("text","")
        if not verificar_estado(chat_id):
            return "ok"

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT folder_id FROM clientes WHERE chat_id=%s", (chat_id,))
        resultado = cur.fetchone()
        cur.close()
        conn.close()

        if not resultado or not resultado[0]:
            enviar_mensaje(chat_id, "⚠ No tienes carpeta Drive vinculada.")
            return "ok"

        folder_id = resultado[0]
        inicio = time.time()

        # Confirmación antes de ejecutar
        if "crear carpeta" in texto.lower() or "mover archivo" in texto.lower() or "ejecutar" in texto.lower():
            enviar_mensaje(chat_id, f"⚠️ *Confirmación requerida*\nDeseas que ejecute: `{texto}`?\nResponde 'SI' para continuar o 'NO' para cancelar.")
            return "ok"

        if texto.lower() == "si":
            # Aquí IA ejecuta la instrucción real
            resultado_ia = ejecutar_instruccion_ia(texto)
            tiempo = round(time.time()-inicio,2)
            enviar_mensaje(chat_id, f"✅ *INSTRUCCIÓN EJECUTADA*\n\n{resultado_ia}\n⏱ Tiempo: {tiempo} segundos")

    return "ok"

# ================== WEBHOOK PAGO ==================
@app.route("/pago_webhook", methods=["POST"])
def pago_webhook():
    data = request.json
    chat_id = data.get("chat_id")
    nuevo_vencimiento = data.get("nuevo_vencimiento")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE clientes SET estado='ACTIVO', vencimiento=%s WHERE chat_id=%s", (nuevo_vencimiento, chat_id))
    conn.commit()
    cur.close()
    conn.close()
    enviar_mensaje(chat_id, "💳 *PAGO CONFIRMADO*\n🔓 Servicio reactivado.")
    return "ok"

@app.route("/")
def home():
    return "Sistema activo"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
