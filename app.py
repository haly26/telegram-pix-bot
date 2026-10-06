import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1004395341778"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def telegram(method, data=None):
    url = f"{TELEGRAM_API}/{method}"

    response = requests.post(
        url,
        json=data or {},
        timeout=20
    )

    print(
        "Telegram API:",
        method,
        response.status_code,
        response.text
    )

    return response.json()


@app.route("/", methods=["GET"])
def home():
    return "Bot online - VERSAO 4!"


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "✅ TESTE FUNCIONOU! O bot recebeu sua mensagem."
            }
        )

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
