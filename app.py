import os
import requests
from flask import Flask, request, jsonify
from datetime import date, datetime, timedelta, timezone

app = Flask(__name__)

# ============================================================
# CONFIGURAÇÕES
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.environ.get("ASAAS_WEBHOOK_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Sandbox do Asaas
ASAAS_API = "https://api-sandbox.asaas.com/v3"

# Valor do produto
PRODUCT_VALUE = 24.90

# Nome EXATO do cliente criado no Asaas Sandbox
ASAAS_CUSTOMER_NAME = "Teste bot pix"

# ID do canal privado
CHANNEL_ID = "-1004395341778"


# ============================================================
# PROTEÇÃO CONTRA DUPLICIDADE
# ============================================================

# Eventos Asaas já processados nesta execução do servidor
processed_events = set()

# Cobranças que já receberam acesso nesta execução do servidor
fulfilled_payments = set()


# ============================================================
# TELEGRAM
# ============================================================

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


# ============================================================
# ASAAS
# ============================================================

def asaas_headers():

    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


# ============================================================
# ENCONTRAR CLIENTE
# ============================================================

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

        print("ERRO AO BUSCAR CLIENTE")

        return None

    data = response.json()

    clientes = data.get("data", [])

    if not clientes:

        print("CLIENTE NÃO ENCONTRADO")

        return None

    cliente = clientes[0]

    cliente_id = cliente.get("id")

    print(
        "CLIENTE ENCONTRADO:",
        cliente_id
    )

    return cliente_id


# ============================================================
# CRIAR COBRANÇA PIX
# ============================================================

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

        # Identifica qual usuário Telegram gerou a cobrança
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


# ============================================================
# OBTER PIX
# ============================================================

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


# ============================================================
# CRIAR LINK DE CONVITE DO TELEGRAM
# ============================================================

def criar_link_convite():

    print("CRIANDO LINK DE CONVITE...")

    # Link válido por 24 horas
    expiracao = int(
        (
            datetime.now(timezone.utc)
            + timedelta(hours=24)
        ).timestamp()
    )

    response = telegram(
        "createChatInviteLink",
        {
            "chat_id": CHANNEL_ID,

            # Apenas uma pessoa pode entrar usando este link
            "member_limit": 1,

            # Expira depois de 24 horas
            "expire_date": expiracao,

            # Nome interno do link
            "name": "Compra Premium"
        }
    )

    if not response.get("ok"):

        print(
            "ERRO AO CRIAR LINK:",
            response
        )

        return None

    resultado = response.get(
        "result",
        {}
    )

    invite_link = resultado.get(
        "invite_link"
    )

    print(
        "LINK DE CONVITE CRIADO:",
        invite_link
    )

    return invite_link


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return "BOT ONLINE - ASAAS"


# ============================================================
# WEBHOOK TELEGRAM
# ============================================================

@app.route("/telegram", methods=["POST"])
def telegram_webhook():

    print(
        "========== RECEBI TELEGRAM =========="
    )

    data = request.get_json(
        silent=True
    ) or {}

    print(
        "JSON:",
        data
    )

    # ========================================================
    # MENSAGEM NORMAL
    # ========================================================

    if "message" in data:

        message = data["message"]

        chat_id = message["chat"]["id"]

        text = message.get(
            "text",
            ""
        )

        # ====================================================
        # COMANDO /START
        # ====================================================

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

                        "Tenha acesso ao nosso "
                        "conteúdo exclusivo.\n\n"

                        "💰 Valor: R$ 24,90\n\n"

                        "Clique abaixo para "
                        "realizar o pagamento:"
                    ),

                    "reply_markup": keyboard
                }
            )


    # ========================================================
    # CLIQUE NO BOTÃO COMPRAR
    # ========================================================

    if "callback_query" in data:

        callback = data[
            "callback_query"
        ]

        callback_id = callback[
            "id"
        ]

        chat_id = callback[
            "message"
        ][
            "chat"
        ][
            "id"
        ]

        callback_data = callback.get(
            "data"
        )

        # Remove o "carregando" do botão
        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id":
                callback_id
            }
        )

        # ====================================================
        # COMPRAR
        # ====================================================

        if callback_data == "comprar":

            cobranca = criar_cobranca_pix(
                chat_id
            )

            # =================================================
            # ERRO AO CRIAR COBRANÇA
            # =================================================

            if cobranca is None:

                telegram(
                    "sendMessage",
                    {

                        "chat_id": chat_id,

                        "text": (

                            "❌ Não foi possível "
                            "gerar o PIX.\n\n"

                            "Tente novamente em "
                            "alguns instantes."
                        )
                    }
                )

            else:

                payment_id = cobranca.get(
                    "id"
                )

                print(
                    "PAGAMENTO CRIADO:",
                    payment_id
                )

                # =================================================
                # BUSCAR PIX
                # =================================================

                pix = obter_pix(
                    payment_id
                )

                if pix is None:

                    telegram(
                        "sendMessage",
                        {

                            "chat_id": chat_id,

                            "text": (

                                "❌ A cobrança foi "
                                "criada, mas não "
                                "conseguimos obter "
                                "o PIX."
                            )
                        }
                    )

                else:

                    qr_code = pix.get(
                        "payload",
                        ""
                    )

                    mensagem = (

                        "💳 PAGAMENTO\n\n"

                        "Produto: ACESSO PREMIUM\n"

                        "Valor: R$ 24,90\n\n"

                        "📱 PIX COPIA E COLA:\n\n"

                        f"{qr_code}\n\n"

                        "Após realizar o pagamento, "
                        "aguarde a confirmação "
                        "automática."
                    )

                    telegram(
                        "sendMessage",
                        {

                            "chat_id": chat_id,

                            "text": mensagem
                        }
                    )

    return jsonify(
        {
            "ok": True
        }
    )


# ============================================================
# WEBHOOK ASAAS
# ============================================================

@app.route("/asaas", methods=["POST"])
def asaas_webhook():

    print(
        "========== WEBHOOK ASAAS =========="
    )

    # ========================================================
    # VALIDAR TOKEN
    # ========================================================

    token_recebido = request.headers.get(
        "asaas-access-token"
    )

    if not ASAAS_WEBHOOK_TOKEN:

        print(
            "ERRO: ASAAS_WEBHOOK_TOKEN "
            "NÃO CONFIGURADO"
        )

        return jsonify(
            {
                "ok": False
            }
        ), 500

    if token_recebido != ASAAS_WEBHOOK_TOKEN:

        print(
            "WEBHOOK ASAAS: TOKEN INVÁLIDO"
        )

        return jsonify(
            {
                "ok": False
            }
        ), 401

    # ========================================================
    # RECEBER DADOS
    # ========================================================

    data = request.get_json(
        silent=True
    ) or {}

    print(
        "DADOS WEBHOOK ASAAS:",
        data
    )

    # ========================================================
    # ID ÚNICO DO EVENTO
    # ========================================================

    event_id = data.get(
        "id"
    )

    evento = data.get(
        "event"
    )

    print(
        "ID DO EVENTO:",
        event_id
    )

    print(
        "EVENTO ASAAS:",
        evento
    )

    # ========================================================
    # PROTEÇÃO 1
    # EVENTO JÁ PROCESSADO?
    # ========================================================

    if event_id:

        if event_id in processed_events:

            print(
                "EVENTO DUPLICADO!"
            )

            print(
                "EVENTO IGNORADO:",
                event_id
            )

            # Retorna 200 para o Asaas
            return jsonify(
                {
                    "ok": True,
                    "duplicate": True
                }
            )

    # ========================================================
    # SÓ PROCESSAR PAGAMENTO RECEBIDO
    # ========================================================

    if evento != "PAYMENT_RECEIVED":

        print(
            "EVENTO NÃO UTILIZADO:",
            evento
        )

        if event_id:

            processed_events.add(
                event_id
            )

        return jsonify(
            {
                "ok": True
            }
        )

    # ========================================================
    # DADOS DO PAGAMENTO
    # ========================================================

    payment = data.get(
        "payment",
        {}
    )

    payment_id = payment.get(
        "id"
    )

    telegram_chat_id = payment.get(
        "externalReference"
    )

    payment_status = payment.get(
        "status"
    )

    print(
        "PAGAMENTO RECEBIDO:",
        payment_id
    )

    print(
        "STATUS:",
        payment_status
    )

    print(
        "TELEGRAM CHAT ID:",
        telegram_chat_id
    )

    # ========================================================
    # VALIDAR ID DA COBRANÇA
    # ========================================================

    if not payment_id:

        print(
            "ERRO: PAGAMENTO SEM ID"
        )

        return jsonify(
            {
                "ok": True
            }
        )

    # ========================================================
    # PROTEÇÃO 2
    # COBRANÇA JÁ ENTREGUE?
    # ========================================================

    if payment_id in fulfilled_payments:

        print(
            "COBRANÇA JÁ PROCESSADA!"
        )

        print(
            "ACESSO NÃO SERÁ GERADO NOVAMENTE."
        )

        if event_id:

            processed_events.add(
                event_id
            )

        return jsonify(
            {
                "ok": True,
                "already_fulfilled": True
            }
        )

    # ========================================================
    # VALIDAR TELEGRAM CHAT ID
    # ========================================================

    if not telegram_chat_id:

        print(
            "ERRO: PAGAMENTO SEM "
            "TELEGRAM CHAT ID"
        )

        return jsonify(
            {
                "ok": True
            }
        )

    # ========================================================
    # CONFIRMAR STATUS
    # ========================================================

    if payment_status != "RECEIVED":

        print(
            "PAGAMENTO NÃO ESTÁ COMO RECEIVED."
        )

        print(
            "ACESSO NÃO SERÁ LIBERADO."
        )

        return jsonify(
            {
                "ok": True
            }
        )

    # ========================================================
    # CRIAR LINK DE CONVITE
    # ========================================================

    invite_link = criar_link_convite()

    if not invite_link:

        print(
            "ERRO: NÃO FOI POSSÍVEL "
            "CRIAR LINK"
        )

        # Não marcamos como processado,
        # permitindo uma nova tentativa
        return jsonify(
            {
                "ok": False
            }
        ), 500

    # ========================================================
    # ENVIAR ACESSO PARA O CLIENTE
    # ========================================================

    resposta_telegram = telegram(
        "sendMessage",
        {

            "chat_id": int(
                telegram_chat_id
            ),

            "text": (

                "✅ PAGAMENTO CONFIRMADO!\n\n"

                "Seu pagamento de "
                "R$ 24,90 foi recebido.\n\n"

                "🔓 SEU ACESSO PREMIUM:\n\n"

                f"{invite_link}\n\n"

                "⚠️ Este link é individual "
                "e pode ser usado para "
                "uma única entrada no canal.\n\n"

                "⏰ O link ficará disponível "
                "por 24 horas."
            )
        }
    )

    # ========================================================
    # VERIFICAR SE TELEGRAM ACEITOU A MENSAGEM
    # ========================================================

    if not resposta_telegram.get(
        "ok"
    ):

        print(
            "ERRO AO ENVIAR ACESSO "
            "PARA O TELEGRAM."
        )

        return jsonify(
            {
                "ok": False
            }
        ), 500

    # ========================================================
    # MARCAR COMO PROCESSADO
    # ========================================================

    fulfilled_payments.add(
        payment_id
    )

    if event_id:

        processed_events.add(
            event_id
        )

    print(
        "ACESSO ENVIADO PARA:",
        telegram_chat_id
    )

    print(
        "PAGAMENTO MARCADO COMO "
        "PROCESSADO:",
        payment_id
    )

    return jsonify(
        {
            "ok": True
        }
    )


# ============================================================
# INICIAR SERVIDOR
# ============================================================

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
