import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
PUSHINPAY_TOKEN = os.environ.get("PUSHINPAY_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1004395341778"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def telegram(method, data=None):
    url = f"{TELEGRAM_API}/{method}"
    response = requests.post(url, json=data or {})
    return response.json()


@app.route("/", methods=["GET"])
def home():
    return "Bot online!"


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}

    print("Webhook recebido:", data)

    return jsonify({"ok": True})


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}

    print("Telegram:", data)

    if "message" in data:
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text == "/start":
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "👋 Bem-vindo ao ACESSO PREMIUM!\n\n"
                        "🔥 Acesso Premium\n"
                        "💰 R$ 24,90\n\n"
                        "Clique abaixo para comprar:"
                    ),
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "💳 COMPRAR — R$ 24,90",
                                    "callback_data": "comprar"
                                }
                            ]
                        ]
                    }
                }
            )

    if "callback_query" in data:
        callback = data["callback_query"]
        chat_id = callback["message"]["chat"]["id"]
        callback_id = callback["id"]

        if callback["data"] == "comprar":

            telegram(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id
                }
            )

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "🛒 ACESSO PREMIUM\n\n"
                        "💰 Valor: R$ 24,90\n\n"
                        "Em breve seu PIX será gerado."
                    )
                }
            )

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
