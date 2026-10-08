import os
import time
from datetime import datetime, timedelta, date

import requests
import psycopg2
from flask import Flask, request, jsonify

# ============================================================
# CONFIGURAÇÕES
# ============================================================

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ASAAS_API = "https://api.asaas.com/v3"

BASE_URL = "https://telegram-pix-bot-hbii.onrender.com"

CHANNEL_ID = -1004395341778

PRODUCT_NAME = "ACESSO PREMIUM"
PRODUCT_VALUE = 24.90

# Nome do cliente já criado no Asaas
ASAAS_CUSTOMER_NAME = "Cliente tele"


# ============================================================
# FUNÇÕES DE BANCO
# ============================================================

def get_db():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL não configurada.")

    return psycopg2.connect(DATABASE_URL)


def init_db():
    print("INICIALIZANDO BANCO DE DADOS...")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_fulfillments (
            payment_id TEXT PRIMARY KEY,
            telegram_chat_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
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
    cur.close()
    conn.close()

    print("BANCO DE DADOS PRONTO.")


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, payload):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"

    response = requests.post(
        url,
        json=payload,
        timeout=30
    )

    try:
        data = response.json()
    except Exception:
        data = {
            "ok": False,
            "description": response.text
        }

    print(
        f"TELEGRAM {method}: "
        f"HTTP {response.status_code} - {data}"
    )

    return data


def enviar_mensagem(chat_id, texto, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML"
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram_request("sendMessage", payload)


def responder_callback(callback_query_id):
    return telegram_request(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_query_id
        }
    )


# ============================================================
# ASAAS
# ============================================================

def asaas_request(method, endpoint, payload=None):
    url = f"{ASAAS_API}{endpoint}"

    headers = {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "TelegramPixBot/1.0"
    }

    try:
        if method == "GET":
            response = requests.get(
                url,
                headers=headers,
                timeout=30
            )

        elif method == "POST":
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=30
            )

        elif method == "PUT":
            response = requests.put(
                url,
                headers=headers,
                json=payload,
                timeout=30
            )

        else:
            raise Exception(f"Método HTTP não suportado: {method}")

    except Exception as e:
        print(f"ERRO DE CONEXÃO ASAAS: {e}")
        raise

    try:
        data = response.json()
    except Exception:
        data = {
            "errors": [
                {
                    "description": response.text
                }
            ]
        }

    print(
        f"ASAAS {method} {endpoint}: "
        f"HTTP {response.status_code} - {data}"
    )

    if response.status_code >= 400:
        raise Exception(
            f"Asaas HTTP {response.status_code}: {data}"
        )

    return data


# ============================================================
# LOCALIZAR CLIENTE "CLIENTE TELE"
# ============================================================

def localizar_cliente_asaas():
    """
    Procura o cliente 'Cliente tele' na conta Asaas.
    Como esse cliente já foi criado e teve o CPF/CNPJ
    preenchido anteriormente, reutilizamos o cadastro.
    """

    data = asaas_request(
        "GET",
        "/customers?limit=100"
    )

    clientes = data.get("data", [])

    for cliente in clientes:
        nome = (cliente.get("name") or "").strip().lower()

        if nome == ASAAS_CUSTOMER_NAME.lower():
            customer_id = cliente.get("id")

            print(
                f"CLIENTE ASAAS ENCONTRADO: "
                f"{ASAAS_CUSTOMER_NAME} -> {customer_id}"
            )

            return customer_id

    raise Exception(
        f"Cliente '{ASAAS_CUSTOMER_NAME}' não encontrado no Asaas."
    )


# ============================================================
# CRIAR COBRANÇA PIX
# ============================================================

def criar_cobranca_pix(chat_id):
    """
    Cria uma cobrança PIX direta no Asaas.

    Não utiliza Checkout.
    Não solicita dados do comprador no momento da compra.
    """

    customer_id = localizar_cliente_asaas()

    external_reference = (
        f"telegram-{chat_id}-{int(time.time())}"
    )

    payload = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": PRODUCT_VALUE,
        "dueDate": date.today().isoformat(),
        "description": PRODUCT_NAME,
        "externalReference": external_reference
    }

    pagamento = asaas_request(
        "POST",
        "/payments",
        payload
    )

    payment_id = pagamento.get("id")

    if not payment_id:
        raise Exception(
            "Asaas não retornou o ID da cobrança."
        )

    # Guarda a relação entre cobrança e usuário Telegram
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO payment_fulfillments
        (
            payment_id,
            telegram_chat_id,
            status
        )
        VALUES (%s, %s, 'pending')
        ON CONFLICT (payment_id)
        DO UPDATE SET
            telegram_chat_id = EXCLUDED.telegram_chat_id,
            updated_at = NOW()
    """, (
        payment_id,
        chat_id
    ))

    conn.commit()
    cur.close()
    conn.close()

    return pagamento


# ============================================================
# BOTÃO COMPRAR
# ============================================================

def mostrar_produto(chat_id):
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "💰 COMPRAR — R$ 24,90",
                    "callback_data": "comprar"
                }
            ]
        ]
    }

    texto = (
        f"<b>{PRODUCT_NAME}</b>\n\n"
        f"💰 Valor: <b>R$ 24,90</b>\n\n"
        f"Clique abaixo para gerar seu pagamento PIX."
    )

    enviar_mensagem(
        chat_id,
        texto,
        keyboard
    )


# ============================================================
# PROCESSAR COMPRA
# ============================================================

def processar_compra(chat_id):
    try:
        pagamento = criar_cobranca_pix(chat_id)

        payment_id = pagamento.get("id")

        # invoiceUrl é a página de pagamento da cobrança
        invoice_url = pagamento.get("invoiceUrl")

        if not invoice_url:
            raise Exception(
                "Asaas não retornou invoiceUrl."
            )

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "💰 PAGAR PIX — R$ 24,90",
                        "url": invoice_url
                    }
                ]
            ]
        }

        texto = (
            "✅ <b>Pagamento gerado!</b>\n\n"
            "Valor: <b>R$ 24,90</b>\n"
            "Forma de pagamento: <b>PIX</b>\n\n"
            "Clique no botão abaixo para realizar o pagamento.\n\n"
            "⚠️ Após a confirmação do pagamento, "
            "seu acesso será liberado automaticamente."
        )

        enviar_mensagem(
            chat_id,
            texto,
            keyboard
        )

        print(
            f"COBRANÇA CRIADA: "
            f"{payment_id} | CHAT: {chat_id}"
        )

    except Exception as e:
        print(f"ERRO AO CRIAR COBRANÇA: {e}")

        enviar_mensagem(
            chat_id,
            "❌ Não foi possível gerar o pagamento agora.\n\n"
            "Tente novamente em alguns instantes."
        )


# ============================================================
# CRIAR LINK DE CONVITE DO TELEGRAM
# ============================================================

def criar_link_convite():
    """
    Cria um convite de uso único para o canal privado.

    O convite expira em 24 horas e pode ser usado apenas uma vez.
    """

    expire_timestamp = int(
        (datetime.utcnow() + timedelta(hours=24)).timestamp()
    )

    payload = {
        "chat_id": CHANNEL_ID,
        "member_limit": 1,
        "expire_date": expire_timestamp
    }

    resultado = telegram_request(
        "createChatInviteLink",
        payload
    )

    if not resultado.get("ok"):
        raise Exception(
            f"Erro ao criar convite: {resultado}"
        )

    invite_link = resultado["result"]["invite_link"]

    return invite_link


# ============================================================
# ENTREGAR ACESSO
# ============================================================

def processar_acesso(payment_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            telegram_chat_id,
            status,
            invite_link
        FROM payment_fulfillments
        WHERE payment_id = %s
    """, (payment_id,))

    row = cur.fetchone()

    if not row:
        print(
            f"PAGAMENTO {payment_id} NÃO ENCONTRADO NO BANCO."
        )

        cur.close()
        conn.close()
        return

    chat_id, status, existing_invite = row

    # Evita enviar dois convites caso o Asaas repita o webhook
    if status == "sent" and existing_invite:
        print(
            f"ACESSO JÁ ENTREGUE PARA {payment_id}."
        )

        cur.close()
        conn.close()
        return

    try:
        invite_link = criar_link_convite()

        texto = (
            "🎉 <b>Pagamento confirmado!</b>\n\n"
            "Seu acesso ao conteúdo premium foi liberado.\n\n"
            "👇 <b>CLIQUE ABAIXO PARA ENTRAR:</b>"
        )

        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "🔐 ENTRAR NO CANAL PREMIUM",
                        "url": invite_link
                    }
                ]
            ]
        }

        enviar_mensagem(
            chat_id,
            texto,
            keyboard
        )

        cur.execute("""
            UPDATE payment_fulfillments
            SET
                status = 'sent',
                invite_link = %s,
                updated_at = NOW()
            WHERE payment_id = %s
        """, (
            invite_link,
            payment_id
        ))

        conn.commit()

        print(
            f"ACESSO ENTREGUE: "
            f"PAYMENT={payment_id} "
            f"CHAT={chat_id}"
        )

    except Exception as e:
        print(
            f"ERRO AO ENTREGAR ACESSO: "
            f"{payment_id} - {e}"
        )

        cur.execute("""
            UPDATE payment_fulfillments
            SET
                status = 'error',
                updated_at = NOW()
            WHERE payment_id = %s
        """, (payment_id,))

        conn.commit()

    finally:
        cur.close()
        conn.close()


# ============================================================
# VALIDAR PAGAMENTO NO ASAAS
# ============================================================

def validar_pagamento(payment_id):
    """
    Consulta a cobrança diretamente no Asaas antes de liberar
    o produto.

    Isso evita liberar acesso apenas porque alguém enviou
    um webhook falso.
    """

    pagamento = asaas_request(
        "GET",
        f"/payments/{payment_id}"
    )

    status = pagamento.get("status")
    value = float(pagamento.get("value", 0))

    print(
        f"VALIDAÇÃO PAGAMENTO: "
        f"{payment_id} | "
        f"STATUS={status} | "
        f"VALOR={value}"
    )

    if status != "RECEIVED":
        print(
            f"PAGAMENTO {payment_id} "
            f"NÃO ESTÁ RECEBIDO. STATUS={status}"
        )
        return False

    if round(value, 2) != round(PRODUCT_VALUE, 2):
        print(
            f"VALOR INCORRETO: "
            f"esperado={PRODUCT_VALUE}, recebido={value}"
        )
        return False

    return True


# ============================================================
# WEBHOOK ASAAS
# ============================================================

@app.route("/asaas", methods=["POST"])
def asaas_webhook():

    # --------------------------------------------------------
    # VALIDAR TOKEN DO WEBHOOK
    # --------------------------------------------------------

    received_token = request.headers.get(
        "asaas-access-token"
    )

    if not ASAAS_WEBHOOK_TOKEN:
        print("ASAAS_WEBHOOK_TOKEN não configurado.")
        return jsonify({"error": "Webhook token not configured"}), 500

    if received_token != ASAAS_WEBHOOK_TOKEN:
        print("WEBHOOK ASAAS: TOKEN INVÁLIDO.")
        return jsonify({"error": "Unauthorized"}), 401

    # --------------------------------------------------------
    # LER PAYLOAD
    # --------------------------------------------------------

    body = request.get_json(silent=True) or {}

    event_id = body.get("id")
    event_type = body.get("event")
    payment = body.get("payment") or {}

    payment_id = payment.get("id")

    print(
        f"WEBHOOK ASAAS RECEBIDO: "
        f"EVENT={event_type} "
        f"EVENT_ID={event_id} "
        f"PAYMENT={payment_id}"
    )

    if not event_id:
        return jsonify({"received": True}), 200

    # --------------------------------------------------------
    # IDEMPOTÊNCIA
    # --------------------------------------------------------

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT event_id
        FROM processed_events
        WHERE event_id = %s
    """, (event_id,))

    already_processed = cur.fetchone()

    if already_processed:
        print(
            f"EVENTO JÁ PROCESSADO: {event_id}"
        )

        cur.close()
        conn.close()

        return jsonify({"received": True}), 200

    # Persiste antes de processar
    cur.execute("""
        INSERT INTO processed_events
        (
            event_id
        )
        VALUES (%s)
        ON CONFLICT (event_id) DO NOTHING
    """, (event_id,))

    conn.commit()

    cur.close()
    conn.close()

    # --------------------------------------------------------
    # PROCESSAR SOMENTE PAGAMENTO RECEBIDO
    # --------------------------------------------------------

    if event_type != "PAYMENT_RECEIVED":
        print(
            f"EVENTO IGNORADO: {event_type}"
        )

        return jsonify({"received": True}), 200

    if not payment_id:
        print(
            "WEBHOOK PAYMENT_RECEIVED SEM PAYMENT ID."
        )

        return jsonify({"received": True}), 200

    # --------------------------------------------------------
    # VALIDAR PAGAMENTO DIRETAMENTE NO ASAAS
    # --------------------------------------------------------

    try:
        pagamento_valido = validar_pagamento(
            payment_id
        )

        if not pagamento_valido:
            return jsonify({"received": True}), 200

    except Exception as e:
        print(
            f"ERRO AO VALIDAR PAGAMENTO "
            f"{payment_id}: {e}"
        )

        # Retornamos 500 para o Asaas poder tentar novamente
        return jsonify({
            "error": "payment validation failed"
        }), 500

    # --------------------------------------------------------
    # ENTREGAR ACESSO
    # --------------------------------------------------------

    processar_acesso(payment_id)

    return jsonify({"received": True}), 200


# ============================================================
# WEBHOOK DE TESTE / STATUS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return (
        "Telegram PIX Bot funcionando em PRODUÇÃO."
    ), 200


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route("/telegram", methods=["POST"])
def telegram_webhook():

    update = request.get_json(silent=True) or {}

    print(
        f"TELEGRAM WEBHOOK RECEBIDO: {update}"
    )

    # --------------------------------------------------------
    # MENSAGEM NORMAL
    # --------------------------------------------------------

    message = update.get("message")

    if message:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        text = message.get("text", "")

        if not chat_id:
            return jsonify({"ok": True}), 200

        if text.startswith("/start"):
            mostrar_produto(chat_id)

            return jsonify({"ok": True}), 200

    # --------------------------------------------------------
    # CALLBACK DOS BOTÕES
    # --------------------------------------------------------

    callback_query = update.get("callback_query")

    if callback_query:

        callback_id = callback_query.get("id")

        callback_data = callback_query.get(
            "data",
            ""
        )

        callback_message = (
            callback_query.get("message") or {}
        )

        callback_chat = (
            callback_message.get("chat") or {}
        )

        chat_id = callback_chat.get("id")

        responder_callback(
            callback_id
        )

        if callback_data == "comprar":

            if chat_id:
                processar_compra(
                    chat_id
                )

        return jsonify({"ok": True}), 200

    return jsonify({"ok": True}), 200


# ============================================================
# INICIALIZAÇÃO
# ============================================================

try:
    init_db()
except Exception as e:
    print(
        f"ERRO AO INICIALIZAR BANCO: {e}"
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", 10000)
        )
        )
