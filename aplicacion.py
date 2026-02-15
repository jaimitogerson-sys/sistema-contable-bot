import os
import requests
from flask import Flask, request

app = Flask(__name__)

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def enviar_mensaje(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": texto
    }
    requests.post(url, data=data)

@app.route("/", methods=["GET"])
def home():
    return "Bot funcionando"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if "message" in data:
        texto = data["message"].get("text", "")
        if texto:
            enviar_mensaje(f"Recibido: {texto}")
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
