
import os
import time
import hmac
import hashlib
from datetime import datetime, timedelta, date

import requests
import psycopg2
from flask import Flask, request, jsonify

# ============================================================
# CONFIGURACOES
# ============================================================

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN")

FLOWINPAY_API_KEY = os.getenv("FLOWINPAY_API_KEY")
FLOWINPAY_WEBHOOK_SECRET = os.getenv("FLOWINPAY_WEBHOOK_SECRET")

DATABASE_URL = os.getenv("DATABASE_URL")

# Mantem Asaas como padrao ate a FlowinPay ser testada.
# Para trocar depois, configure PAYMENT_PROVIDER=flowinpay no Render.
PAYMENT_PROVIDER = os.getenv(
    "PAYMENT_PROVIDER", "asaas"
).strip().lower()

ASAAS_API = "https://api.asaas.com/v3"
FLOWINPAY_API = "https://app.flowinpay.com.br/api/v1"

BASE_URL = "https://telegram-pix-bot-hbii.onrender.com"

CHANNEL_ID = -1004395341778

PRODUCT_NAME = "ACESSO PREMIUM"
PRODUCT_VALUE = 24.90

ASAAS_CUSTOMER_NAME = "Cliente tele"


# ============================================================
# BANCO DE DADOS
# ============================================================

def get_db():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL nao configurada.")

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


def registrar_pagamento(payment_id, chat_id):
    """Salva a relacao entre uma cobranca e o comprador do Telegram."""

    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO payment_fulfillments
                (payment_id, telegram_chat_id, status)
            VALUES (%s, %s, 'pending')
            ON CONFLICT (payment_id)
            DO UPDATE SET
                telegram_chat_id = EXCLUDED.telegram_chat_id,
                updated_at = NOW()
        """, (str(payment_id), chat_id))

        conn.commit()
        cur.close()

    finally:
        conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, payload):
    if not TELEGRAM_TOKEN:
        raise Exception("TELEGRAM_TOKEN nao configurado.")

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
        {"callback_query_id": callback_query_id}
    )


# ============================================================
# REQUISICOES ASAAS
# ============================================================

def asaas_request(method, endpoint, payload=None):
    if not ASAAS_API_KEY:
        raise Exception("ASAAS_API_KEY nao configurada.")

    url = f"{ASAAS_API}{endpoint}"

    headers = {
        "access_token": ASAAS_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "TelegramPixBot/1.0"
    }

    try:
        if method == "GET":
            response = requests.get(
                url, headers=headers, timeout=30
            )
        elif method == "POST":
            response = requests.post(
                url, headers=headers, json=payload, timeout=30
            )
        elif method == "PUT":
            response = requests.put(
                url, headers=headers, json=payload, timeout=30
            )
        else:
            raise Exception(f"Metodo HTTP nao suportado: {method}")

    except Exception as e:
        print(f"ERRO DE CONEXAO ASAAS: {e}")
        raise

    try:
        data = response.json()
    except Exception:
        data = {"errors": [{"description": response.text}]}

    print(
        f"ASAAS {method} {endpoint}: "
        f"HTTP {response.status_code}"
    )

    if response.status_code >= 400:
        raise Exception(
            f"Asaas HTTP {response.status_code}: {data}"
        )

    return data


# ============================================================
# REQUISICOES FLOWINPAY
# ============================================================

def flowinpay_request(method, endpoint, payload=None):
    if not FLOWINPAY_API_KEY:
        raise Exception("FLOWINPAY_API_KEY nao configurada.")

    url = f"{FLOWINPAY_API}{endpoint}"

    headers = {
        "X-Api-Key": FLOWINPAY_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        if method == "GET":
            response = requests.get(
                url, headers=headers, timeout=20
            )
        elif method == "POST":
            response = requests.post(
                url, headers=headers, json=payload, timeout=20
            )
        else:
            raise Exception(
                f"Metodo HTTP FlowinPay nao suportado: {method}"
            )

    except Exception as e:
        print(f"ERRO DE CONEXAO FLOWINPAY: {e}")
        raise

    try:
        data = response.json()
    except Exception:
        data = {"message": response.text}

    # Nao registrar a chave da API nos logs.
    print(
        f"FLOWINPAY {method} {endpoint}: "
        f"HTTP {response.status_code}"
    )

    if response.status_code >= 400:
        raise Exception(
            f"FlowinPay HTTP {response.status_code}: {data}"
        )

    return data


# ============================================================
# ASAAS: LOCALIZAR CLIENTE
# ============================================================

def localizar_cliente_asaas():
    data = asaas_request("GET", "/customers?limit=100")

    for cliente in data.get("data", []):
        nome = (cliente.get("name") or "").strip().lower()

        if nome == ASAAS_CUSTOMER_NAME.lower():
            customer_id = cliente.get("id")
            print(f"CLIENTE ASAAS ENCONTRADO: {customer_id}")
            return customer_id

    raise Exception(
        f"Cliente '{ASAAS_CUSTOMER_NAME}' nao encontrado no Asaas."
    )


# ============================================================
# CRIAR COBRANCA ASAAS
# ============================================================

def criar_cobranca_asaas(chat_id):
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

    pagamento = asaas_request("POST", "/payments", payload)

    payment_id = pagamento.get("id")

    if not payment_id:
        raise Exception("Asaas nao retornou o ID da cobranca.")

    registrar_pagamento(payment_id, chat_id)

    return pagamento


# ============================================================
# CRIAR COBRANCA FLOWINPAY
# ============================================================

def criar_cobranca_flowinpay(chat_id):
    """
    Cria cobranca FlowinPay e registra o ID antes de enviar
    o link ao comprador.

    O prefixo flowinpay- evita colisao com IDs do Asaas.
    """

    payload = {
        "value": PRODUCT_VALUE,
        "description": PRODUCT_NAME
    }

    resposta = flowinpay_request(
        "POST",
        "/charges",
        payload
    )

    cobranca = resposta.get("charge", resposta)

    charge_id = cobranca.get("id")
    payment_link = cobranca.get("payment_link_url")

    if charge_id is None:
        raise Exception("FlowinPay nao retornou o ID da cobranca.")

    if not payment_link:
        raise Exception(
            "FlowinPay nao retornou payment_link_url."
        )

    payment_id = f"flowinpay-{charge_id}"

    # Salvar antes de enviar o link ao cliente.
    registrar_pagamento(payment_id, chat_id)

    print(
        f"COBRANCA FLOWINPAY CRIADA: "
        f"{payment_id} | CHAT: {chat_id}"
    )

    return {
        "id": payment_id,
        "invoiceUrl": payment_link
    }


# ============================================================
# BOTAO COMPRAR
# ============================================================

def mostrar_produto(chat_id):
    keyboard = {
        "inline_keyboard": [[
            {
                "text": "💰 COMPRAR — R$ 24,90",
                "callback_data": "comprar"
            }
        ]]
    }

    texto = (
        f"<b>{PRODUCT_NAME}</b>\n\n"
        f"💰 Valor: <b>R$ 24,90</b>\n\n"
        "Clique abaixo para gerar seu pagamento PIX."
    )

    enviar_mensagem(chat_id, texto, keyboard)


# ============================================================
# PROCESSAR COMPRA
# ============================================================

def processar_compra(chat_id):
    try:
        if PAYMENT_PROVIDER == "flowinpay":
            pagamento = criar_cobranca_flowinpay(chat_id)

        elif PAYMENT_PROVIDER == "asaas":
            pagamento = criar_cobranca_asaas(chat_id)

        else:
            raise Exception(
                "PAYMENT_PROVIDER invalido. Use asaas ou flowinpay."
            )

        payment_id = pagamento.get("id")
        invoice_url = pagamento.get("invoiceUrl")

        if not invoice_url:
            raise Exception("Nao foi retornado o link de pagamento.")

        keyboard = {
            "inline_keyboard": [[
                {
                    "text": "💰 PAGAR PIX — R$ 24,90",
                    "url": invoice_url
                }
            ]]
        }

        texto = (
            "✅ <b>Pagamento gerado!</b>\n\n"
            "Valor: <b>R$ 24,90</b>\n"
            "Forma de pagamento: <b>PIX</b>\n\n"
            "Clique no botao abaixo para realizar o pagamento.\n\n"
            "⚠️ Apos a confirmacao do pagamento, "
            "seu acesso sera liberado automaticamente."
        )

        enviar_mensagem(chat_id, texto, keyboard)

        print(
            f"COBRANCA CRIADA: "
            f"{payment_id} | CHAT: {chat_id} | "
            f"PROVEDOR: {PAYMENT_PROVIDER}"
        )

    except Exception as e:
        print(f"ERRO AO CRIAR COBRANCA: {e}")

        enviar_mensagem(
            chat_id,
            "❌ Nao foi possivel gerar o pagamento agora.\n\n"
            "Tente novamente em alguns instantes."
        )


# ============================================================
# CRIAR LINK DE CONVITE DO TELEGRAM
# ============================================================

def criar_link_convite():
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
        raise Exception(f"Erro ao criar convite: {resultado}")

    return resultado["result"]["invite_link"]


# ============================================================
# ENTREGAR ACESSO
# ============================================================

def processar_acesso(payment_id):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT telegram_chat_id, status, invite_link
            FROM payment_fulfillments
            WHERE payment_id = %s
        """, (str(payment_id),))

        row = cur.fetchone()

        if not row:
            print(f"PAGAMENTO {payment_id} NAO ENCONTRADO NO BANCO.")
            return

        chat_id, status, existing_invite = row

        # Evita enviar novamente um convite ja entregue.
        if status == "sent" and existing_invite:
            print(f"ACESSO JA ENTREGUE PARA {payment_id}.")
            return

        invite_link = criar_link_convite()

        texto = (
            "🎉 <b>Pagamento confirmado!</b>\n\n"
            "Seu acesso ao conteudo premium foi liberado.\n\n"
            "👇 <b>CLIQUE ABAIXO PARA ENTRAR:</b>"
        )

        keyboard = {
            "inline_keyboard": [[
                {
                    "text": "🔐 ENTRAR NO CANAL PREMIUM",
                    "url": invite_link
                }
            ]]
        }

        resultado = enviar_mensagem(
            chat_id, texto, keyboard
        )

        if not resultado.get("ok"):
            raise Exception(
                f"Telegram nao confirmou o envio: {resultado}"
            )

        cur.execute("""
            UPDATE payment_fulfillments
            SET status = 'sent',
                invite_link = %s,
                updated_at = NOW()
            WHERE payment_id = %s
        """, (invite_link, str(payment_id)))

        conn.commit()

        print(
            f"ACESSO ENTREGUE: "
            f"PAYMENT={payment_id} CHAT={chat_id}"
        )

    except Exception as e:
        conn.rollback()
        print(
            f"ERRO AO ENTREGAR ACESSO: {payment_id} - {e}"
        )

        cur.execute("""
            UPDATE payment_fulfillments
            SET status = 'error', updated_at = NOW()
            WHERE payment_id = %s
        """, (str(payment_id),))

        conn.commit()

    finally:
        cur.close()
        conn.close()


# ============================================================
# VALIDAR PAGAMENTO ASAAS
# ============================================================

def validar_pagamento_asaas(payment_id):
    pagamento = asaas_request(
        "GET", f"/payments/{payment_id}"
    )

    status = pagamento.get("status")
    value = float(pagamento.get("value", 0))

    print(
        f"VALIDACAO ASAAS: {payment_id} | "
        f"STATUS={status} | VALOR={value}"
    )

    if status != "RECEIVED":
        return False

    if round(value, 2) != round(PRODUCT_VALUE, 2):
        print("ASAAS: VALOR INCORRETO.")
        return False

    return True


# ============================================================
# VALIDAR PAGAMENTO FLOWINPAY
# ============================================================

def validar_pagamento_flowinpay(charge_id):
    """
    Consulta a cobranca diretamente na FlowinPay.
    O webhook, sozinho, nao e suficiente para liberar acesso.
    """

    resposta = flowinpay_request(
        "GET", f"/charges/{charge_id}"
    )

    cobranca = resposta.get("charge", resposta)

    status = str(cobranca.get("status", "")).lower()

    try:
        value = float(cobranca.get("value", 0))
    except (TypeError, ValueError):
        value = 0.0

    print(
        f"VALIDACAO FLOWINPAY: {charge_id} | "
        f"STATUS={status} | VALOR={value}"
    )

    if status != "paid":
        print("FLOWINPAY: PAGAMENTO AINDA NAO ESTA PAGO.")
        return False

    if round(value, 2) != round(PRODUCT_VALUE, 2):
        print("FLOWINPAY: VALOR INCORRETO.")
        return False

    return True


# ============================================================
# WEBHOOK ASAAS
# ============================================================

@app.route("/asaas", methods=["POST"])
def asaas_webhook():
    received_token = request.headers.get(
        "asaas-access-token"
    )

    if not ASAAS_WEBHOOK_TOKEN:
        print("ASAAS_WEBHOOK_TOKEN nao configurado.")
        return jsonify({"error": "Webhook token not configured"}), 500

    if received_token != ASAAS_WEBHOOK_TOKEN:
        print("WEBHOOK ASAAS: TOKEN INVALIDO.")
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}

    event_id = body.get("id")
    event_type = body.get("event")
    payment = body.get("payment") or {}
    payment_id = payment.get("id")

    print(
        f"WEBHOOK ASAAS: EVENT={event_type} "
        f"EVENT_ID={event_id} PAYMENT={payment_id}"
    )

    if not event_id:
        return jsonify({"received": True}), 200

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT event_id FROM processed_events
            WHERE event_id = %s
        """, (event_id,))

        if cur.fetchone():
            return jsonify({"received": True}), 200

        cur.execute("""
            INSERT INTO processed_events (event_id)
            VALUES (%s)
            ON CONFLICT (event_id) DO NOTHING
        """, (event_id,))

        conn.commit()

    finally:
        cur.close()
        conn.close()

    if event_type != "PAYMENT_RECEIVED":
        return jsonify({"received": True}), 200

    if not payment_id:
        return jsonify({"received": True}), 200

    try:
        if not validar_pagamento_asaas(payment_id):
            return jsonify({"received": True}), 200

    except Exception as e:
        print(f"ERRO AO VALIDAR ASAAS {payment_id}: {e}")
        return jsonify({"error": "payment validation failed"}), 500

    processar_acesso(payment_id)

    return jsonify({"received": True}), 200


# ============================================================
# WEBHOOK FLOWINPAY
# ============================================================

@app.route("/flowinpay", methods=["POST"])
def flowinpay_webhook():
    if not FLOWINPAY_WEBHOOK_SECRET:
        print("FLOWINPAY_WEBHOOK_SECRET nao configurado.")
        return jsonify({"error": "Webhook secret not configured"}), 500

    # Validar a assinatura usando o corpo original da requisicao.
    raw_body = request.get_data(cache=True)

    received_signature = request.headers.get(
        "X-FlowinPay-Signature", ""
    ).strip()

    expected_signature = hmac.new(
        FLOWINPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not received_signature or not hmac.compare_digest(
        received_signature, expected_signature
    ):
        print("WEBHOOK FLOWINPAY: ASSINATURA INVALIDA.")
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}

    event_type = (
        body.get("event")
        or request.headers.get("event")
        or request.headers.get("X-FlowinPay-Event")
    )

    charge = body.get("charge") or {}
    charge_id = charge.get("id")

    print(
        f"WEBHOOK FLOWINPAY: EVENT={event_type} "
        f"CHARGE={charge_id}"
    )

    # Somente pagamento confirmado pode liberar acesso.
    if event_type != "charge.completed":
        return jsonify({"received": True}), 200

    if charge_id is None:
        print("WEBHOOK FLOWINPAY SEM ID DA COBRANCA.")
        return jsonify({"received": True}), 200

    payment_id = f"flowinpay-{charge_id}"

    # Confere se essa cobranca foi criada pelo nosso bot.
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT status
            FROM payment_fulfillments
            WHERE payment_id = %s
        """, (payment_id,))

        row = cur.fetchone()

    finally:
        cur.close()
        conn.close()

    if not row:
        print(
            f"COBRANCA FLOWINPAY NAO PERTENCE A UM PEDIDO "
            f"REGISTRADO: {payment_id}"
        )
        return jsonify({"received": True}), 200

    # Consulta a API antes de liberar o canal.
    try:
        pagamento_valido = validar_pagamento_flowinpay(
            charge_id
        )

        if not pagamento_valido:
            return jsonify({"received": True}), 200

    except Exception as e:
        print(
            f"ERRO AO VALIDAR FLOWINPAY {charge_id}: {e}"
        )
        # HTTP 500 permite que a FlowinPay tente novamente.
        return jsonify({"error": "payment validation failed"}), 500

    processar_acesso(payment_id)

    return jsonify({"received": True}), 200


# ============================================================
# PAGINA INICIAL / STATUS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "Telegram PIX Bot funcionando em PRODUCAO.", 200


# ============================================================
# WEBHOOK TELEGRAM
# ============================================================

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    print("TELEGRAM WEBHOOK RECEBIDO.")

    # Mensagens normais
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

    # Cliques nos botoes
    callback_query = update.get("callback_query")

    if callback_query:
        callback_id = callback_query.get("id")
        callback_data = callback_query.get("data", "")

        callback_message = (
            callback_query.get("message") or {}
        )

        callback_chat = (
            callback_message.get("chat") or {}
        )

        chat_id = callback_chat.get("id")

        if callback_id:
            responder_callback(callback_id)

        if callback_data == "comprar" and chat_id:
            processar_compra(chat_id)

        return jsonify({"ok": True}), 200

    return jsonify({"ok": True}), 200


# ============================================================
# INICIALIZACAO
# ============================================================

try:
    init_db()
except Exception as e:
    print(f"ERRO AO INICIALIZAR BANCO: {e}")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000))
    )
