import os
from datetime import datetime, timedelta

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify


app = Flask(__name__)


# ============================================================
# CONFIGURAÇÕES
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Asaas PRODUÇÃO
ASAAS_API = "https://api.asaas.com/v3"

# Produto
PRODUCT_NAME = "ACESSO PREMIUM"
PRODUCT_VALUE = 24.90

# Canal privado do Telegram
CHANNEL_ID = -1004395341778

# URL pública do bot
BASE_URL = "https://telegram-pix-bot-hbii.onrender.com"


# ============================================================
# VALIDAÇÃO DAS VARIÁVEIS
# ============================================================

if not TELEGRAM_TOKEN:
    raise Exception("TELEGRAM_TOKEN não configurado.")

if not ASAAS_API_KEY:
    raise Exception("ASAAS_API_KEY não configurado.")

if not ASAAS_WEBHOOK_TOKEN:
    raise Exception("ASAAS_WEBHOOK_TOKEN não configurado.")

if not DATABASE_URL:
    raise Exception("DATABASE_URL não configurado.")


# ============================================================
# BANCO DE DADOS
# ============================================================

def get_db():
    return psycopg2.connect(DATABASE_URL)


def inicializar_banco():

    print("INICIALIZANDO BANCO DE DADOS...")

    conn = get_db()

    try:

        with conn.cursor() as cur:

            # Mantém a tabela anterior para não perder histórico
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payment_fulfillments (
                    payment_id TEXT PRIMARY KEY,
                    telegram_chat_id BIGINT NOT NULL,
                    status TEXT NOT NULL,
                    invite_link TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

            # Novo controle dos Checkouts
            cur.execute("""
                CREATE TABLE IF NOT EXISTS checkout_orders (
                    checkout_id TEXT PRIMARY KEY,
                    telegram_chat_id BIGINT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

            # Idempotência dos webhooks
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    processed_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

        conn.commit()

        print("BANCO DE DADOS PRONTO.")

    finally:

        conn.close()


inicializar_banco()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, data):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        json=data,
        timeout=30
    )

    try:

        result = response.json()

    except Exception:

        result = {
            "ok": False,
            "description": response.text
        }

    print(
        f"Telegram {method}: "
        f"HTTP {response.status_code} - {result}"
    )

    return result


def enviar_mensagem(
    chat_id,
    texto,
    reply_markup=None
):

    data = {
        "chat_id": chat_id,
        "text": texto
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return telegram_request(
        "sendMessage",
        data
    )


# ============================================================
# MENU DO BOT
# ============================================================

def enviar_menu(chat_id):

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "COMPRAR — R$ 24,90",
                    "callback_data": "comprar"
                }
            ]
        ]
    }

    texto = (
        "🔐 *ACESSO PREMIUM*\n\n"
        "Tenha acesso ao conteúdo exclusivo.\n\n"
        "💰 Valor: *R$ 24,90*\n\n"
        "Clique no botão abaixo para realizar o pagamento:"
    )

    data = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown",
        "reply_markup": keyboard
    }

    return telegram_request(
        "sendMessage",
        data
    )


# ============================================================
# ASAAS
# ============================================================

def asaas_headers():

    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "Telegram-Pix-Bot/1.0"
    }


# ============================================================
# CRIAR CHECKOUT ASAAS
# ============================================================

def criar_checkout(chat_id):

    # Identificador interno da venda
    external_reference = (
        f"telegram-{chat_id}-"
        f"{int(datetime.utcnow().timestamp())}"
    )

    payload = {

        # Somente PIX
        "billingTypes": [
            "PIX"
        ],

        # Pagamento avulso
        "chargeTypes": [
            "DETACHED"
        ],

        # Checkout válido por 60 minutos
        "minutesToExpire": 60,

        # Identificação da venda
        "externalReference": external_reference,

        # URLs de retorno.
        # IMPORTANTE:
        # elas NÃO confirmam o pagamento.
        "callback": {
            "successUrl": f"{BASE_URL}/checkout/sucesso",
            "cancelUrl": f"{BASE_URL}/checkout/cancelado",
            "expiredUrl": f"{BASE_URL}/checkout/expirado"
        },

        # Produto
        "items": [
            {
                "name": PRODUCT_NAME,
                "description": "Acesso ao conteúdo premium",
                "quantity": 1,
                "value": PRODUCT_VALUE
            }
        ]

        # NÃO enviamos:
        # customer
        # customerData
        #
        # Dessa forma o próprio comprador
        # preencherá seus dados no Checkout.
    }

    response = requests.post(
        f"{ASAAS_API}/checkouts",
        headers=asaas_headers(),
        json=payload,
        timeout=30
    )

    print(
        "Criação Checkout Asaas:",
        response.status_code,
        response.text
    )

    if response.status_code not in (200, 201):

        return None

    return response.json()


# ============================================================
# GERAR PAGAMENTO
# ============================================================

def criar_pagamento(chat_id):

    checkout = criar_checkout(chat_id)

    if not checkout:

        enviar_mensagem(
            chat_id,
            "❌ Não foi possível gerar o pagamento agora.\n\n"
            "Tente novamente em alguns instantes."
        )

        return

    checkout_id = checkout.get("id")

    if not checkout_id:

        print(
            "Checkout retornado sem ID:",
            checkout
        )

        enviar_mensagem(
            chat_id,
            "❌ Não foi possível gerar o pagamento."
        )

        return

    # Registra o Checkout no banco
    conn = get_db()

    try:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO checkout_orders
                (
                    checkout_id,
                    telegram_chat_id,
                    status
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (checkout_id)
                DO UPDATE SET
                    telegram_chat_id = EXCLUDED.telegram_chat_id,
                    updated_at = NOW()
            """, (
                checkout_id,
                chat_id,
                "pending"
            ))

        conn.commit()

    finally:

        conn.close()

    # Link oficial do Checkout Asaas
    checkout_url = (
        "https://asaas.com/checkoutSession/show"
        f"?id={checkout_id}"
    )

    texto = (
        "💳 *PAGAMENTO GERADO*\n\n"
        f"Produto: *{PRODUCT_NAME}*\n"
        f"Valor: *R$ {PRODUCT_VALUE:.2f}*\n\n"
        "Ao clicar abaixo, você será direcionado "
        "para a página segura de pagamento.\n\n"
        "📋 Seus dados serão preenchidos diretamente "
        "no Checkout do Asaas.\n\n"
        "⚠️ Após a confirmação do pagamento, "
        "o acesso será enviado automaticamente aqui."
    )

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "💰 PAGAR PIX",
                    "url": checkout_url
                }
            ]
        ]
    }

    enviar_mensagem(
        chat_id,
        texto,
        keyboard
    )


# ============================================================
# ENTREGAR ACESSO
# ============================================================

def processar_acesso(
    checkout_id,
    chat_id
):

    conn = get_db()

    try:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            # Cria/obtém registro de entrega
            cur.execute("""
                INSERT INTO payment_fulfillments
                (
                    payment_id,
                    telegram_chat_id,
                    status
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (payment_id)
                DO UPDATE SET
                    telegram_chat_id = EXCLUDED.telegram_chat_id,
                    updated_at = NOW()
                RETURNING *
            """, (
                checkout_id,
                chat_id,
                "pending"
            ))

            registro = cur.fetchone()

            # Se já foi entregue, não envia novamente
            if registro["status"] == "sent":

                print(
                    "Acesso já enviado:",
                    checkout_id
                )

                conn.commit()

                return True

            # Convite válido por 24 horas
            expire_date = int(
                (
                    datetime.utcnow()
                    + timedelta(days=1)
                ).timestamp()
            )

            invite_result = telegram_request(
                "createChatInviteLink",
                {
                    "chat_id": CHANNEL_ID,
                    "member_limit": 1,
                    "expire_date": expire_date
                }
            )

            if not invite_result.get("ok"):

                print(
                    "Erro ao criar convite:",
                    invite_result
                )

                conn.rollback()

                return False

            invite_link = (
                invite_result
                ["result"]
                ["invite_link"]
            )

            texto = (
                "✅ *PAGAMENTO CONFIRMADO!*\n\n"
                "Seu pagamento foi aprovado.\n\n"
                "🔐 Aqui está seu acesso ao conteúdo premium:\n\n"
                f"👉 {invite_link}\n\n"
                "⚠️ Este link é individual e expira em 24 horas.\n"
                "Use-o para entrar no canal."
            )

            telegram_result = enviar_mensagem(
                chat_id,
                texto
            )

            if not telegram_result.get("ok"):

                print(
                    "Erro ao enviar acesso:",
                    telegram_result
                )

                conn.rollback()

                return False

            cur.execute("""
                UPDATE payment_fulfillments
                SET
                    status = %s,
                    invite_link = %s,
                    updated_at = NOW()
                WHERE payment_id = %s
            """, (
                "sent",
                invite_link,
                checkout_id
            ))

            cur.execute("""
                UPDATE checkout_orders
                SET
                    status = %s,
                    updated_at = NOW()
                WHERE checkout_id = %s
            """, (
                "paid",
                checkout_id
            ))

            conn.commit()

            print(
                "ACESSO ENTREGUE COM SUCESSO:",
                checkout_id
            )

            return True

    except Exception as e:

        conn.rollback()

        print(
            "ERRO AO PROCESSAR ACESSO:",
            repr(e)
        )

        return False

    finally:

        conn.close()


# ============================================================
# WEBHOOK ASAAS
# ============================================================

@app.route(
    "/asaas",
    methods=["POST"]
)
def webhook_asaas():

    received_token = request.headers.get(
        "asaas-access-token"
    )

    if received_token != ASAAS_WEBHOOK_TOKEN:

        print(
            "Webhook Asaas recusado: "
            "token inválido."
        )

        return jsonify({
            "ok": False,
            "error": "unauthorized"
        }), 401

    data = request.get_json(
        silent=True
    ) or {}

    print(
        "WEBHOOK ASAAS RECEBIDO:",
        data
    )

    event = data.get("event")

    event_id = data.get("id")

    # ========================================================
    # IDEMPOTÊNCIA
    # ========================================================

    if event_id:

        conn = get_db()

        try:

            with conn.cursor() as cur:

                cur.execute("""
                    INSERT INTO processed_events
                    (
                        event_id
                    )
                    VALUES (%s)
                    ON CONFLICT (event_id)
                    DO NOTHING
                    RETURNING event_id
                """, (
                    event_id,
                ))

                inserted = cur.fetchone()

            conn.commit()

        finally:

            conn.close()

        # Se já existia, ignora duplicata
        if not inserted:

            print(
                "Evento já processado:",
                event_id
            )

            return jsonify({
                "ok": True
            }), 200

    # ========================================================
    # CHECKOUT PAGO
    # ========================================================

    if event == "CHECKOUT_PAID":

        checkout = data.get(
            "checkout"
        ) or {}

        checkout_id = checkout.get(
            "id"
        )

        checkout_status = checkout.get(
            "status"
        )

        print(
            "CHECKOUT PAGO:",
            checkout_id,
            "status:",
            checkout_status
        )

        if not checkout_id:

            print(
                "CHECKOUT_PAID sem checkout.id"
            )

            return jsonify({
                "ok": True
            }), 200

        if checkout_status != "PAID":

            print(
                "Checkout não está PAID."
            )

            return jsonify({
                "ok": True
            }), 200

        # Confere o valor do item
        items = checkout.get(
            "items"
        ) or []

        total = 0.0

        for item in items:

            try:

                quantity = int(
                    item.get(
                        "quantity",
                        1
                    )
                )

                value = float(
                    item.get(
                        "value",
                        0
                    )
                )

                total += quantity * value

            except Exception:

                pass

        if abs(
            total - PRODUCT_VALUE
        ) > 0.01:

            print(
                "VALOR DO CHECKOUT DIFERENTE:",
                total
            )

            return jsonify({
                "ok": True
            }), 200

        # Recupera o chat do Telegram
        conn = get_db()

        try:

            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:

                cur.execute("""
                    SELECT *
                    FROM checkout_orders
                    WHERE checkout_id = %s
                """, (
                    checkout_id,
                ))

                order = cur.fetchone()

        finally:

            conn.close()

        if not order:

            print(
                "Checkout não encontrado no banco:",
                checkout_id
            )

            return jsonify({
                "ok": True
            }), 200

        chat_id = order[
            "telegram_chat_id"
        ]

        processar_acesso(
            checkout_id,
            chat_id
        )

        return jsonify({
            "ok": True
        }), 200

    # ========================================================
    # EVENTOS ANTIGOS DE PAYMENT_RECEIVED
    # ========================================================

    if event == "PAYMENT_RECEIVED":

        print(
            "PAYMENT_RECEIVED recebido. "
            "Para novos pedidos, o fluxo utiliza CHECKOUT_PAID."
        )

        return jsonify({
            "ok": True
        }), 200

    # ========================================================
    # OUTROS EVENTOS
    # ========================================================

    print(
        "Evento Asaas ignorado:",
        event
    )

    return jsonify({
        "ok": True
    }), 200


# ============================================================
# WEBHOOK TELEGRAM
# ============================================================

@app.route(
    "/telegram",
    methods=["POST"]
)
def webhook_telegram():

    data = request.get_json(
        silent=True
    ) or {}

    print(
        "UPDATE TELEGRAM RECEBIDO:",
        data
    )

    # ========================================================
    # MENSAGEM
    # ========================================================

    message = data.get(
        "message"
    )

    if message:

        chat = message.get(
            "chat"
        ) or {}

        chat_id = chat.get(
            "id"
        )

        text = message.get(
            "text",
            ""
        )

        if chat_id and text:

            if text.startswith(
                "/start"
            ):

                enviar_menu(
                    chat_id
                )

    # ========================================================
    # BOTÃO
    # ========================================================

    callback_query = data.get(
        "callback_query"
    )

    if callback_query:

        callback_id = callback_query.get(
            "id"
        )

        chat_id = (
            callback_query
            .get("message", {})
            .get("chat", {})
            .get("id")
        )

        callback_data = callback_query.get(
            "data"
        )

        # Remove o carregamento do botão
        if callback_id:

            telegram_request(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id
                }
            )

        if (
            callback_data == "comprar"
            and chat_id
        ):

            criar_pagamento(
                chat_id
            )

    return jsonify({
        "ok": True
    }), 200


# ============================================================
# PÁGINAS DE RETORNO DO CHECKOUT
# ============================================================

@app.route(
    "/checkout/sucesso",
    methods=["GET"]
)
def checkout_sucesso():

    return (
        "Pagamento processado. "
        "Se o pagamento foi confirmado, "
        "o acesso será enviado automaticamente "
        "pelo Telegram."
    )


@app.route(
    "/checkout/cancelado",
    methods=["GET"]
)
def checkout_cancelado():

    return (
        "Pagamento cancelado. "
        "Você pode voltar ao Telegram "
        "e gerar um novo pagamento."
    )


@app.route(
    "/checkout/expirado",
    methods=["GET"]
)
def checkout_expirado():

    return (
        "Este pagamento expirou. "
        "Volte ao Telegram e gere um novo pagamento."
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "Telegram Pix Bot funcionando em PRODUÇÃO."
    )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                5000
            )
        )
        )
