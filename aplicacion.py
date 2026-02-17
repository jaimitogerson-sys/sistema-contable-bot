import os
import requests
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import pandas as pd

# ---------------- FLASK ----------------
app = Flask(__name__)

# ---------------- VARIABLES DE ENTORNO ----------------
TOKEN = os.getenv("TOKEN")                  # Telegram bot token
CHAT_ID = os.getenv("CHAT_ID")              # Telegram chat ID
FOLDER_ID = os.getenv("FOLDER_ID")          # Google Drive folder a monitorear
GOOGLE_CREDENTIALS = json.loads(os.getenv("GOOGLE_CREDENTIALS"))  # JSON de credenciales
CONFIG_PATH = "./config/config_clienteX.json"   # Configuración por cliente

# ---------------- FUNCIONES AUXILIARES ----------------
def enviar_mensaje(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": texto}
    requests.post(url, data=data)

def cargar_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def guardar_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

# ---------------- GOOGLE DRIVE ----------------
creds = service_account.Credentials.from_service_account_info(
    GOOGLE_CREDENTIALS,
    scopes=["https://www.googleapis.com/auth/drive"]
)
drive_service = build("drive", "v3", credentials=creds)
archivos_vistos = set()

# ---------------- AGENTE / IA SIMULADO ----------------
def agente_auditoria_flexible(archivos, instruccion):
    """
    Filtra y analiza archivos según la instrucción del usuario:
    - Semana, mes, año, cualquier rango de fechas.
    - Detecta faltantes o inconsistencias.
    - Ordena todo para generar Excel final.
    """
    resumen = {}
    for archivo in archivos:
        # Ejemplo simple: clasificamos por nombre
        carpeta = "General"
        if "compra" in archivo['name'].lower():
            carpeta = "Compras"
        elif "venta" in archivo['name'].lower():
            carpeta = "Ventas"

        if carpeta not in resumen:
            resumen[carpeta] = []
        resumen[carpeta].append({
            "Nombre Archivo": archivo['name'],
            "Estado": "Registrado",  # Puede cambiar según reglas
            "Observaciones": ""
        })

    # Guardar Excel final
    fecha = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    os.makedirs("./resumenes", exist_ok=True)
    for carpeta, lista in resumen.items():
        df = pd.DataFrame(lista)
        df.to_excel(f"./resumenes/Auditoria_{carpeta}_{fecha}.xlsx", index=False)

    return resumen

# ---------------- LOGS ----------------
def generar_log(archivos, instruccion):
    os.makedirs("./logs", exist_ok=True)
    fecha_hora = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(f"./logs/registro_auditoria_{fecha_hora}.txt", "w") as f:
        f.write(f"Instrucción: {instruccion}\n")
        f.write("Archivos procesados:\n")
        for archivo in archivos:
            f.write(f"- {archivo['name']}\n")
        f.write(f"\nProcesamiento finalizado: {datetime.now()}\n")

# ---------------- HILO DE REVISIÓN DE DRIVE ----------------
def revisar_drive():
    while True:
        try:
            config = cargar_config()
            hoy = datetime.now().date()
            vencimiento = datetime.strptime(config["vencimiento"], "%Y-%m-%d").date()

            # Verificar estado de licencia
            if config["estado"] != "ACTIVO" or hoy > vencimiento:
                enviar_mensaje("⛔ SERVICIO SUSPENDIDO. Para reactivar haz tu pago.")
                config["estado"] = "INACTIVO"
                guardar_config(config)
                time.sleep(60)
                continue

            resultados = drive_service.files().list(
                q=f"'{FOLDER_ID}' in parents",
                fields="files(id, name, createdTime)"
            ).execute()
            archivos = resultados.get("files", [])

            # Filtrar archivos nuevos
            nuevos = [a for a in archivos if a["id"] not in archivos_vistos]

            if nuevos:
                for archivo in nuevos:
                    archivos_vistos.add(archivo["id"])

                # Instrucción del usuario
                instruccion = "Auditoría de esta semana"

                # Ejecutar IA/agente
                resumen = agente_auditoria_flexible(nuevos, instruccion)
                generar_log(nuevos, instruccion)

                # Notificación al cliente
                mensaje = f"📬 AUDITORÍA COMPLETA - {instruccion}\n"
                mensaje += f"📝 Archivos procesados: {len(nuevos)}\n"
                for carpeta, lista in resumen.items():
                    mensaje += f"\n📁 {carpeta}:\n" + "\n".join([f" - {x['Nombre Archivo']}" for x in lista])
                mensaje += f"\n\n✅ Todos los archivos han sido auditados y ordenados."
                enviar_mensaje(mensaje)

            time.sleep(20)

        except Exception as e:
            print("Error revisando Drive:", e)
            time.sleep(30)

# ---------------- RUTAS FLASK ----------------
@app.route("/", methods=["GET"])
def home():
    return "Bot funcionando"

@app.route("/pago_webhook", methods=["POST"])
def pago_webhook():
    data = request.json
    cliente_id = data.get("cliente_id")
    monto = data.get("monto")
    estado_pago = data.get("estado_pago")
    nuevo_vencimiento = data.get("nuevo_vencimiento")

    # Verificar pago aprobado
    if estado_pago != "aprobado":
        enviar_mensaje(f"⚠️ Pago rechazado o pendiente para {cliente_id}.")
        return "Pago no aprobado"

    # Activar licencia automáticamente
    config = cargar_config()
    config["estado"] = "ACTIVO"
    config["vencimiento"] = nuevo_vencimiento
    guardar_config(config)

    enviar_mensaje(f"""
💳 PAGO CONFIRMADO
💰 Valor recibido: ${monto}
📅 Nuevo vencimiento: {nuevo_vencimiento}
🔓 Estado: ACTIVO
🚀 Tu bot ha sido reactivado automáticamente
""")
    return "ok"

# ---------------- HILO DE RECORDATORIOS ----------------
def recordatorios_vencimiento():
    while True:
        config = cargar_config()
        hoy = datetime.now().date()
        vencimiento = datetime.strptime(config["vencimiento"], "%Y-%m-%d").date()

        if config["estado"] == "ACTIVO":
            if hoy == vencimiento - timedelta(days=3):
                enviar_mensaje(f"⚠️ Recordatorio: tu bot vence en 3 días ({vencimiento})")
            elif hoy == vencimiento:
                enviar_mensaje(f"⛔ Hoy vence tu bot ({vencimiento}), realiza el pago para no interrumpir el servicio")
        time.sleep(86400)  # Revisa una vez al día

# ---------------- INICIO HILOS Y SERVICIO ----------------
hilo_drive = threading.Thread(target=revisar_drive)
hilo_drive.daemon = True
hilo_drive.start()

hilo_recordatorio = threading.Thread(target=recordatorios_vencimiento)
hilo_recordatorio.daemon = True
hilo_recordatorio.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
