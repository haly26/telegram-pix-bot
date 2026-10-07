import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
ASAAS_API = "https://api-sandbox.asaas.com/v3"

PRODUCT_VALUE = 24.90


def telegram(method, data):
    url = f"{TELEGRAM_API}/{method}"

    response = requests.post(
        url,
        json=data,
        timeout=30
    )

    print("TELEGRAM:", method, response.status_code, response.text)

    return response.json()


def asaas_headers():
    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def criar_cobranca_pix():
    print("CRIANDO COBRANÇA ASAAS...")

    payload = {
        "billingType": "PIX",
        "value": PRODUCT_VALUE,
        "description": "Acesso Premium"
    }

    response = requests.post(
        f"{ASAAS_API}/payments",
        headers=asaas_headers(),
        json=payload,
        timeout=30
    )

    print(
        "ASAAS CREATE PAYMENT:",
        response.status_code,
        response.text
    )

    if not response.ok:
        return None

    return response.json()


def obter_pix(payment_id):
    print("BUSCANDO QR CODE PIX...")

    response = requests.get(
        f"{ASAAS_API}/payments/{payment_id}/pixQrCode",
        headers=asaas_headers(),
        timeout=30
    )

    print(
        "ASAAS PIX QR CODE:",
        response.status_code,
        response.text
    )

    if not response.ok:
        return None

    return response.json()


@app.route("/", methods=["GET"])
def home():
    return "BOT ONLINE - ASAAS"


@app.route("/telegram", methods=["POST"])
def telegram_webhook():

    print("========== RECEBI TELEGRAM ==========")

    data = request.get_json(silent=True) or {}

    print("JSON:", data)

    # =========================
    # MENSAGEM NORMAL
    # =========================

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

    # =========================
    # BOTÃO COMPRAR
    # =========================

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

            cobranca = criar_cobranca_pix()

            if cobranca is None:

                telegram(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": (
                            "❌ Não foi possível gerar o PIX.\n\n"
                            "Tente novamente em alguns instantes."
                        )
                    }
                )

            else:

                payment_id = cobranca.get("id")

                pix = obter_pix(payment_id)

                if pix is None:

                    telegram(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": (
                                "❌ A cobrança foi criada, "
                                "mas não conseguimos obter o PIX."
                            )
                        }
                    )

                else:

                    qr_code = pix.get("payload", "")

                    mensagem = (
                        "💳 PAGAMENTO\n\n"
                        "Produto: ACESSO PREMIUM\n"
                        "Valor: R$ 24,90\n\n"
                        "📱 PIX COPIA E COLA:\n\n"
                        f"{qr_code}\n\n"
                        "Após realizar o pagamento, "
                        "aguarde a confirmação."
                    )

                    telegram(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": mensagem
                        }
                    )

    return jsonify({"ok": True})


@app.route("/asaas", methods=["POST"])
def asaas_webhook():

    data = request.get_json(silent=True) or {}

    print("========== WEBHOOK ASAAS ==========")

    print("DADOS:", data)

    return jsonify({"ok": True})


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
