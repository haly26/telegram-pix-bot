import os
import base64
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
PUSHINPAY_TOKEN = os.environ.get("PUSHINPAY_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
PUSHINPAY_API = "https://api.pushinpay.com.br/api/pix/cashIn"

PRODUCT_VALUE = 2490


def telegram(method, data):

    url = f"{TELEGRAM_API}/{method}"

    response = requests.post(
        url,
        json=data,
        timeout=30
    )

    print("TELEGRAM:", method, response.status_code, response.text)

    return response.json()


def criar_pix():

    headers = {
        "Authorization": f"Bearer {PUSHINPAY_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "value": PRODUCT_VALUE,
        "webhook_url": "https://telegram-pix-bot-hbii.onrender.com/pushinpay",
        "split_rules": []
    }

    print("CRIANDO PIX...")

    response = requests.post(
        PUSHINPAY_API,
        headers=headers,
        json=payload,
        timeout=30
    )

    print("PUSHINPAY:", response.status_code, response.text)

    if not response.ok:
        return None

    return response.json()


@app.route("/", methods=["GET"])
def home():

    return "BOT ONLINE - PUSHINPAY"


@app.route("/telegram", methods=["POST"])
def telegram_webhook():

    print("========== RECEBI TELEGRAM ==========")

    data = request.get_json(silent=True) or {}

    print("JSON:", data)

    # Mensagem normal
    if "message" in data:

        message = data["message"]

        chat_id = message["chat"]["id"]

        text = message.get("text", "")

        if text == "/start":

            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "💳 COMPRAR — R$ 24,90",
                            "callback_data": "comprar"
                        }
                    ]
                ]
            }

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "🔥 ACESSO PREMIUM\n\n"
                        "Tenha acesso ao nosso conteúdo exclusivo.\n\n"
                        "💰 Valor: R$ 24,90\n\n"
                        "Clique abaixo para realizar o pagamento:"
                    ),
                    "reply_markup": keyboard
                }
            )

    # Clique no botão
    if "callback_query" in data:

        callback = data["callback_query"]

        callback_id = callback["id"]

        chat_id = callback["message"]["chat"]["id"]

        callback_data = callback.get("data")

        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id
            }
        )

        if callback_data == "comprar":

            pix = criar_pix()

            if pix is None:

                telegram(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": (
                            "❌ Não foi possível gerar o PIX agora.\n\n"
                            "Tente novamente em alguns instantes."
                        )
                    }
                )

            else:

                print("PIX GERADO:", pix)

                qr_code = pix.get("qr_code", "")

                transaction_id = pix.get("id", "")

                mensagem = (
                    "💳 PAGAMENTO\n\n"
                    "Valor: R$ 24,90\n\n"
                    "PIX copia e cola:\n\n"
                    f"{qr_code}\n\n"
                    "Após o pagamento, aguarde a confirmação.\n\n"
                    f"ID da transação: {transaction_id}"
                )

                telegram(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": mensagem
                    }
                )

    return jsonify({"ok": True})


@app.route("/pushinpay", methods=["POST"])
def pushinpay_webhook():

    data = request.get_json(silent=True) or {}

    print("========== WEBHOOK PUSHINPAY ==========")
    print("DADOS:", data)

    return jsonify({"ok": True})


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
