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

    print("Telegram API:", method, response.status_code, response.text)

    return response.json()


@app.route("/", methods=["GET"])
def home():
    return "Bot online - VERSAO 2!"


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    return "TESTE TELEGRAM", 201
    data = request.get_json(silent=True) or {}

    print("================================")
    print("ATUALIZAÇÃO RECEBIDA:")
    print(data)
    print("================================")

    # Mensagem normal
    if "message" in data:

        message = data["message"]

        chat = message.get("chat", {})
        chat_id = chat.get("id")

        text = message.get("text", "")

        print("CHAT ID:", chat_id)
        print("TEXTO:", text)

        if text.strip() == "/start":

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "👋 Bem-vindo ao ACESSO PREMIUM!\n\n"
                        "🔥 ACESSO PREMIUM\n"
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

    # Clique no botão
    if "callback_query" in data:

        callback = data["callback_query"]

        callback_id = callback.get("id")

        message = callback.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")

        callback_data = callback.get("data")

        print("BOTÃO:", callback_data)

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id
            }
        )

        if callback_data == "comprar":

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "🛒 ACESSO PREMIUM\n\n"
                        "💰 Valor: R$ 24,90\n\n"
                        "PIX será gerado em seguida."
                    )
                }
            )

    return jsonify({"ok": True})


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
