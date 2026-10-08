import os
from datetime import date, datetime, timedelta

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

# PRODUÇÃO ASAAS
ASAAS_API = "https://api.asaas.com/v3"

# Cliente criado na conta PRODUÇÃO do Asaas
ASAAS_CUSTOMER_NAME = "Cliente tele"

# Produto
PRODUCT_NAME = "ACESSO PREMIUM"
PRODUCT_VALUE = 24.90

# Canal privado do Telegram
CHANNEL_ID = -1004395341778


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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"

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


def enviar_mensagem(chat_id, texto, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": texto
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return telegram_request("sendMessage", data)


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

    return telegram_request("sendMessage", data)


# ============================================================
# ASAAS
# ============================================================

def asaas_headers():
    return {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "Telegram-Pix-Bot/1.0"
    }


def buscar_cliente_asaas():

    url = f"{ASAAS_API}/customers"

    params = {
        "name": ASAAS_CUSTOMER_NAME,
        "limit": 100
    }

    response = requests.get(
        url,
        headers=asaas_headers(),
        params=params,
        timeout=30
    )

    print(
        "Busca cliente Asaas:",
        response.status_code,
        response.text
    )

    if response.status_code != 200:
        return None

    data = response.json()

    customers = data.get("data", [])

    for customer in customers:

        if customer.get("name", "").strip().lower() == \
                ASAAS_CUSTOMER_NAME.strip().lower():

            return customer

    return None


def criar_cobranca_asaas(telegram_chat_id):

    customer = buscar_cliente_asaas()

    if not customer:
        print(
            f"ERRO: cliente '{ASAAS_CUSTOMER_NAME}' "
            "não encontrado no Asaas."
        )

        return None

    customer_id = customer["id"]

    print(
        "Cliente Asaas encontrado:",
        customer_id,
        customer.get("name")
    )

    payload = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": PRODUCT_VALUE,
        "dueDate": date.today().isoformat(),
        "description": PRODUCT_NAME
    }

    response = requests.post(
        f"{ASAAS_API}/payments",
        headers=asaas_headers(),
        json=payload,
        timeout=30
    )

    print(
        "Criação cobrança Asaas:",
        response.status_code,
        response.text
    )

    if response.status_code not in (200, 201):
        return None

    return response.json()


def obter_link_pagamento(payment_id):

    response = requests.get(
        f"{ASAAS_API}/payments/{payment_id}",
        headers=asaas_headers(),
        timeout=30
    )

    print(
        "Consulta pagamento Asaas:",
        response.status_code,
        response.text
    )

    if response.status_code != 200:
        return None

    payment = response.json()

    return payment.get("invoiceUrl")


# ============================================================
# CRIAR PIX
# ============================================================

def criar_pix(chat_id):

    payment = criar_cobranca_asaas(chat_id)

    if not payment:
        enviar_mensagem(
            chat_id,
            "❌ Não foi possível gerar o pagamento agora.\n\n"
            "Tente novamente em alguns instantes."
        )
        return

    payment_id = payment.get("id")

    if not payment_id:
        enviar_mensagem(
            chat_id,
            "❌ O pagamento não pôde ser criado."
        )
        return

    payment_link = payment.get("invoiceUrl")

    if not payment_link:
        payment_link = obter_link_pagamento(payment_id)

    if not payment_link:

        enviar_mensagem(
            chat_id,
            "❌ O link de pagamento não foi encontrado.\n\n"
            "Tente novamente."
        )

        return

    # Registra o pagamento no banco
    conn = get_db()

    try:

        with conn.cursor() as cur:

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
            """, (
                payment_id,
                chat_id,
                "pending"
            ))

        conn.commit()

    finally:
        conn.close()

    texto = (
        "💳 *PAGAMENTO GERADO*\n\n"
        f"Produto: *{PRODUCT_NAME}*\n"
        f"Valor: *R$ {PRODUCT_VALUE:.2f}*\n\n"
        "Clique no botão abaixo para pagar via PIX.\n\n"
        "⚠️ Após a confirmação do pagamento, "
        "o acesso será enviado automaticamente aqui."
    )

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "💰 PAGAR PIX",
                    "url": payment_link
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

def processar_acesso(payment_id):

    conn = get_db()

    try:

        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM payment_fulfillments
                WHERE payment_id = %s
                FOR UPDATE
            """, (payment_id,))

            registro = cur.fetchone()

            if not registro:

                print(
                    "Pagamento não encontrado no banco:",
                    payment_id
                )

                return False

            # Evita entregar o mesmo acesso duas vezes
            if registro["status"] == "sent":

                print(
                    "Acesso já enviado para pagamento:",
                    payment_id
                )

                return True

            chat_id = registro["telegram_chat_id"]

            # Cria convite individual
            expire_date = int(
                (datetime.utcnow() + timedelta(days=1)).timestamp()
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

                return False

            invite_link = invite_result["result"]["invite_link"]

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
                    "Erro ao enviar acesso para o cliente:",
                    telegram_result
                )

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
                payment_id
            ))

            conn.commit()

            print(
                "ACESSO ENTREGUE COM SUCESSO:",
                payment_id
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

@app.route("/asaas", methods=["POST"])
def webhook_asaas():

    received_token = request.headers.get(
        "asaas-access-token"
    )

    if received_token != ASAAS_WEBHOOK_TOKEN:

        print("Webhook Asaas recusado: token inválido.")

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

    # Só nos interessa quando o pagamento foi realmente recebido
    if event != "PAYMENT_RECEIVED":

        print(
            "Evento ignorado:",
            event
        )

        return jsonify({
            "ok": True,
            "ignored": True
        }), 200

    payment = data.get("payment") or {}

    payment_id = payment.get("id")

    if not payment_id:

        print(
            "Webhook sem payment.id"
        )

        return jsonify({
            "ok": True
        }), 200

    payment_status = payment.get("status")

    payment_value = payment.get("value")

    print(
        "Pagamento:",
        payment_id,
        "status:",
        payment_status,
        "valor:",
        payment_value
    )

    # Segurança: somente RECEIVED
    if payment_status != "RECEIVED":

        print(
            "Pagamento ainda não está RECEIVED."
        )

        return jsonify({
            "ok": True
        }), 200

    # Segurança: confere valor
    try:
        payment_value = float(payment_value)

    except Exception:

        print(
            "Valor do pagamento inválido."
        )

        return jsonify({
            "ok": True
        }), 200

    if abs(payment_value - PRODUCT_VALUE) > 0.01:

        print(
            "VALOR DIFERENTE DO PRODUTO:",
            payment_value
        )

        return jsonify({
            "ok": True
        }), 200

    # Processa e entrega o acesso
    sucesso = processar_acesso(
        payment_id
    )

    # Registra evento somente depois do processamento
    if sucesso:

        event_id = data.get("id")

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
                    """, (
                        event_id,
                    ))

                conn.commit()

            finally:
                conn.close()

    return jsonify({
        "ok": True
    }), 200


# ============================================================
# WEBHOOK TELEGRAM
# ============================================================

@app.route("/telegram", methods=["POST"])
def webhook_telegram():

    data = request.get_json(
        silent=True
    ) or {}

    print(
        "UPDATE TELEGRAM RECEBIDO:",
        data
    )

    message = data.get("message")

    if message:

        chat = message.get("chat") or {}

        chat_id = chat.get("id")

        text = message.get("text", "")

        if chat_id and text:

            if text.startswith("/start"):

                enviar_menu(chat_id)

    callback_query = data.get("callback_query")

    if callback_query:

        callback_id = callback_query.get("id")

        from_user = callback_query.get("from") or {}

        chat_id = (
            callback_query
            .get("message", {})
            .get("chat", {})
            .get("id")
        )

        callback_data = callback_query.get(
            "data"
        )

        # Remove o "loading" do botão
        if callback_id:

            telegram_request(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id
                }
            )

        if callback_data == "comprar" and chat_id:

            criar_pix(chat_id)

    return jsonify({
        "ok": True
    }), 200


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return "Telegram Pix Bot funcionando em PRODUÇÃO."


# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", 5000)
        )
    )
