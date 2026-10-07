import os
import requests
from flask import Flask, request, jsonify
from datetime import date

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.environ.get("ASAAS_WEBHOOK_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
ASAAS_API = "https://api-sandbox.asaas.com/v3"

PRODUCT_VALUE = 24.90

# Nome EXATO do cliente criado no Asaas Sandbox
ASAAS_CUSTOMER_NAME = "Teste bot pix"

# ID do canal privado
CHANNEL_ID = "-1004395341778"


def telegram(method, data):

    url = f"{TELEGRAM_API}/{method}"

    response = requests.post(
        url,
        json=data,
        timeout=30
    )

    print(
        "TELEGRAM:",
        method,
        response.status_code,
        response.text
    )

    return response.json()


def asaas_headers():

    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def encontrar_cliente():

    print("PROCURANDO CLIENTE NO ASAAS...")

    response = requests.get(
        f"{ASAAS_API}/customers",
        headers=asaas_headers(),
        params={
            "name": ASAAS_CUSTOMER_NAME,
            "limit": 100
        },
        timeout=30
    )

    print(
        "ASAAS LIST CUSTOMERS:",
        response.status_code,
        response.text
    )

    if not response.ok:

        return None

    data = response.json()

    clientes = data.get("data", [])

    if not clientes:

        print("CLIENTE NÃO ENCONTRADO")

        return None

    cliente = clientes[0]

    print(
        "CLIENTE ENCONTRADO:",
        cliente.get("id")
    )

    return cliente.get("id")


def criar_cobranca_pix(chat_id):

    customer_id = encontrar_cliente()

    if not customer_id:

        return None

    print("CRIANDO COBRANÇA ASAAS...")

    payload = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": PRODUCT_VALUE,
        "description": "Acesso Premium",
        "dueDate": date.today().isoformat(),

        # Guarda o usuário Telegram relacionado à cobrança
        "externalReference": str(chat_id)
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


def criar_link_convite():

    print("CRIANDO LINK DE CONVITE...")

    response = telegram(
        "createChatInviteLink",
        {
            "chat_id": CHANNEL_ID,

            # Link pode ser usado somente uma vez
            "member_limit": 1,

            # Nome interno para identificação
            "name": "Compra Premium"
        }
    )

    if not response.get("ok"):

        print(
            "ERRO AO CRIAR LINK:",
            response
        )

        return None

    resultado = response.get("result", {})

    invite_link = resultado.get("invite_link")

    print(
        "LINK DE CONVITE CRIADO:",
        invite_link
    )

    return invite_link


@app.route("/", methods=["GET"])
def home():

    return "BOT ONLINE - ASAAS"


@app.route("/telegram", methods=["POST"])
def telegram_webhook():

    print("========== RECEBI TELEGRAM ==========")

    data = request.get_json(silent=True) or {}

    print("JSON:", data)

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

            cobranca = criar_cobranca_pix(chat_id)

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

                print(
                    "PAGAMENTO CRIADO:",
                    payment_id
                )

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
                        "aguarde a confirmação automática."
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

    print("========== WEBHOOK ASAAS ==========")

    # Verifica o token de segurança enviado pelo Asaas
    token_recebido = request.headers.get("asaas-access-token")

    if not ASAAS_WEBHOOK_TOKEN:

        print("ERRO: ASAAS_WEBHOOK_TOKEN NÃO CONFIGURADO")

        return jsonify({"ok": False}), 500

    if token_recebido != ASAAS_WEBHOOK_TOKEN:

        print("WEBHOOK ASAAS: TOKEN INVÁLIDO")

        return jsonify({"ok": False}), 401

    data = request.get_json(silent=True) or {}

    print(
        "DADOS WEBHOOK ASAAS:",
        data
    )

    evento = data.get("event")

    print(
        "EVENTO ASAAS:",
        evento
    )

    # Só libera acesso quando o pagamento foi realmente recebido
    if evento == "PAYMENT_RECEIVED":

        payment = data.get("payment", {})

        payment_id = payment.get("id")

        telegram_chat_id = payment.get("externalReference")

        print(
            "PAGAMENTO RECEBIDO:",
            payment_id
        )

        print(
            "TELEGRAM CHAT ID:",
            telegram_chat_id
        )

        if not telegram_chat_id:

            print(
                "ERRO: PAGAMENTO SEM TELEGRAM CHAT ID"
            )

            return jsonify({"ok": True})

        # Cria um link individual com limite de 1 pessoa
        invite_link = criar_link_convite()

        if not invite_link:

            print(
                "ERRO: NÃO FOI POSSÍVEL CRIAR LINK"
            )

            return jsonify({"ok": True})

        # Envia o link para o comprador
        telegram(
            "sendMessage",
            {
                "chat_id": int(telegram_chat_id),

                "text": (
                    "✅ PAGAMENTO CONFIRMADO!\n\n"
                    "Seu pagamento de R$ 24,90 foi recebido.\n\n"
                    "🔓 SEU ACESSO PREMIUM:\n\n"
                    f"{invite_link}\n\n"
                    "⚠️ Este link é individual e pode ser usado "
                    "para uma única entrada no canal."
                )
            }
        )

        print(
            "ACESSO ENVIADO PARA:",
            telegram_chat_id
        )

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
