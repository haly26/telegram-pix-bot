
import os
import time
import hmac
import hashlib
from datetime import datetime, timedelta, date

import requests
import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN")
FLOWINPAY_API_KEY = os.getenv("FLOWINPAY_API_KEY")
FLOWINPAY_WEBHOOK_SECRET = os.getenv("FLOWINPAY_WEBHOOK_SECRET")
FLOWINPAY_DIAGNOSTIC_TOKEN = os.getenv("FLOWINPAY_DIAGNOSTIC_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "asaas").lower().strip()

ASAAS_API = "https://api.asaas.com/v3"
FLOWINPAY_API = "https://app.flowinpay.com.br/api/v1"

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
    conn = get_db()
    try:
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
        print("BANCO DE DADOS PRONTO.")
    finally:
        conn.close()


def registrar_pagamento(payment_id, chat_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO payment_fulfillments
                (payment_id, telegram_chat_id, status)
            VALUES (%s, %s, 'pending')
            ON CONFLICT (payment_id) DO NOTHING
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

    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
        json=payload,
        timeout=25
    )

    try:
        data = response.json()
    except Exception:
        data = {"ok": False, "description": "Resposta nao JSON"}

    if response.status_code >= 400 or not data.get("ok"):
        raise Exception(
            f"Erro Telegram {method}: HTTP {response.status_code}"
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


def responder_callback(callback_id):
    return telegram_request(
        "answerCallbackQuery",
        {"callback_query_id": callback_id}
    )


# ============================================================
# CLIENTES HTTP DOS GATEWAYS
# ============================================================

def asaas_request(method, endpoint, payload=None):
    if not ASAAS_API_KEY:
        raise Exception("ASAAS_API_KEY nao configurada.")

    response = requests.request(
        method,
        f"{ASAAS_API}{endpoint}",
        headers={
            "access_token": ASAAS_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "TelegramPixBot/1.0"
        },
        json=payload,
        timeout=25
    )

    try:
        data = response.json()
    except Exception:
        data = {"message": "Resposta nao JSON"}

    print(f"ASAAS {method} {endpoint}: HTTP {response.status_code}")

    if response.status_code >= 400:
        raise Exception(f"Asaas HTTP {response.status_code}: {data}")

    return data


def flowinpay_request(method, endpoint, payload=None):
    if not FLOWINPAY_API_KEY:
        raise Exception("FLOWINPAY_API_KEY nao configurada.")

    response = requests.request(
        method,
        f"{FLOWINPAY_API}{endpoint}",
        headers={
            "X-Api-Key": FLOWINPAY_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TelegramPixBot/1.0"
        },
        json=payload,
        timeout=25
    )

    try:
        data = response.json()
    except Exception:
        data = {"message": "Resposta nao JSON"}

    print(f"FLOWINPAY {method} {endpoint}: HTTP {response.status_code}")

    if response.status_code >= 400:
        raise Exception(f"FlowinPay HTTP {response.status_code}: {data}")

    return data


# ============================================================
# DIAGNOSTICO TEMPORARIO FLOWINPAY
# Nao cria cobrancas nem mostra o saldo
# ============================================================

@app.route("/diagnostico-flowinpay", methods=["GET"])
def diagnostico_flowinpay():
    if not FLOWINPAY_DIAGNOSTIC_TOKEN:
        return jsonify({"erro": "Diagnostico desativado"}), 404

    received_token = request.args.get("token", "")

    if not hmac.compare_digest(
        received_token, FLOWINPAY_DIAGNOSTIC_TOKEN
    ):
        return jsonify({"erro": "Nao autorizado"}), 401

    if not FLOWINPAY_API_KEY:
        return jsonify({
            "diagnostico": "FLOWINPAY_API_KEY nao configurada no Render"
        }), 500

    try:
        response = requests.get(
            f"{FLOWINPAY_API}/balance",
            headers={
                "X-Api-Key": FLOWINPAY_API_KEY,
                "Accept": "application/json",
                "User-Agent": "TelegramPixBot-Diagnostic/1.0"
            },
            timeout=15,
            allow_redirects=False
        )

        content_type = response.headers.get(
            "Content-Type", ""
        ).lower()

        body_start = response.text[:2000].lower()

        if (
            "text/html" in content_type
            or "just a moment" in body_start
        ):
            if (
                "cloudflare" in body_start
                or "just a moment" in body_start
                or "enable javascript" in body_start
            ):
                diagnosis = (
                    "BLOQUEIO_CLOUDFLARE: a resposta parece ser "
                    "uma verificacao de seguranca, nao a API."
                )
            else:
                diagnosis = (
                    "RESPOSTA_HTML: o servidor devolveu HTML, "
                    "nao uma resposta JSON normal."
                )

        elif response.status_code == 200:
            diagnosis = (
                "API_ACESSIVEL: consulta respondeu HTTP 200. "
                "Isso nao confirma permissao para criar cobrancas."
            )

        elif response.status_code == 401:
            diagnosis = (
                "HTTP_401: verificar se a chave esta correta, "
                "ativa e enviada no cabecalho esperado."
            )

        elif response.status_code == 403:
            diagnosis = (
                "HTTP_403: acesso negado. Pode ser permissao, "
                "restricao de acesso ou bloqueio de seguranca."
            )

        elif response.status_code == 429:
            diagnosis = (
                "HTTP_429: limite de requisicoes atingido."
            )

        else:
            diagnosis = (
                "RESPOSTA_API: verificar o codigo HTTP retornado."
            )

        print(
            "DIAGNOSTICO FLOWINPAY: "
            f"HTTP={response.status_code}; "
            f"CONTENT_TYPE={content_type[:80]}; "
            f"RESULTADO={diagnosis}"
        )

        return jsonify({
            "http_status": response.status_code,
            "content_type": content_type[:80],
            "diagnostico": diagnosis
        }), 200

    except requests.RequestException as e:
        print(
            "DIAGNOSTICO FLOWINPAY: erro de conexao "
            f"{type(e).__name__}"
        )
        return jsonify({
            "diagnostico": "ERRO_DE_CONEXAO",
            "tipo": type(e).__name__
        }), 502


# ============================================================
# CRIAR COBRANCAS
# ============================================================

def localizar_cliente_asaas():
    data = asaas_request("GET", "/customers?limit=100")

    for cliente in data.get("data", []):
        if (
            (cliente.get("name") or "").strip().lower()
            == ASAAS_CUSTOMER_NAME.lower()
        ):
            return cliente["id"]

    raise Exception(
        f"Cliente '{ASAAS_CUSTOMER_NAME}' nao encontrado no Asaas."
    )


def criar_cobranca_asaas(chat_id):
    customer_id = localizar_cliente_asaas()

    payload = {
        "customer": customer_id,
        "billingType": "PIX",
        "value": PRODUCT_VALUE,
        "dueDate": date.today().isoformat(),
        "description": PRODUCT_NAME,
        "externalReference": f"telegram-{chat_id}-{int(time.time())}"
    }

    pagamento = asaas_request("POST", "/payments", payload)

    if not pagamento.get("id") or not pagamento.get("invoiceUrl"):
        raise Exception("Resposta incompleta do Asaas.")

    registrar_pagamento(pagamento["id"], chat_id)

    return str(pagamento["id"]), pagamento["invoiceUrl"]


def criar_cobranca_flowinpay(chat_id):
    resposta = flowinpay_request("POST", "/charges", {
        "value": PRODUCT_VALUE,
        "description": PRODUCT_NAME
    })

    cobranca = resposta.get("charge", resposta)
    charge_id = cobranca.get("id")
    payment_link = cobranca.get("payment_link_url")

    if charge_id is None or not payment_link:
        raise Exception("FlowinPay nao retornou ID e link da cobranca.")

    payment_id = f"flowinpay-{charge_id}"
    registrar_pagamento(payment_id, chat_id)

    print(f"COBRANCA FLOWINPAY CRIADA: {payment_id}")

    return payment_id, payment_link


# ============================================================
# MENU E COMPRA
# ============================================================

def mostrar_produto(chat_id):
    enviar_mensagem(
        chat_id,
        f"<b>{PRODUCT_NAME}</b>\n\n"
        f"💰 Valor: <b>R$ {PRODUCT_VALUE:.2f}</b>\n\n"
        "Clique abaixo para gerar seu pagamento PIX.",
        {"inline_keyboard": [[{
            "text": "💰 COMPRAR — R$ 24,90",
            "callback_data": "comprar"
        }]]}
    )


def processar_compra(chat_id):
    try:
        if PAYMENT_PROVIDER == "flowinpay":
            payment_id, invoice_url = criar_cobranca_flowinpay(chat_id)
        elif PAYMENT_PROVIDER == "asaas":
            payment_id, invoice_url = criar_cobranca_asaas(chat_id)
        else:
            raise Exception(
                "PAYMENT_PROVIDER deve ser asaas ou flowinpay."
            )

        enviar_mensagem(
            chat_id,
            "✅ <b>Pagamento gerado!</b>\n\n"
            "Valor: <b>R$ 24,90</b>\n"
            "Forma de pagamento: <b>PIX</b>\n\n"
            "Clique abaixo para pagar. Seu acesso será liberado "
            "após a confirmação.",
            {"inline_keyboard": [[{
                "text": "💰 PAGAR PIX — R$ 24,90",
                "url": invoice_url
            }]]}
        )

        print(
            f"COBRANCA CRIADA: {payment_id}; "
            f"provedor={PAYMENT_PROVIDER}"
        )

    except Exception as e:
        print(f"ERRO AO CRIAR COBRANCA: {e}")
        enviar_mensagem(
            chat_id,
            "❌ Não foi possível gerar o pagamento agora. "
            "Tente novamente em alguns instantes."
        )


# ============================================================
# VALIDAR PAGAMENTOS
# ============================================================

def validar_pagamento_asaas(payment_id):
    pagamento = asaas_request("GET", f"/payments/{payment_id}")

    try:
        value = float(pagamento.get("value", 0))
    except (ValueError, TypeError):
        return False

    return (
        pagamento.get("status") == "RECEIVED"
        and round(value, 2) == round(PRODUCT_VALUE, 2)
    )


def validar_pagamento_flowinpay(charge_id):
    resposta = flowinpay_request("GET", f"/charges/{charge_id}")
    cobranca = resposta.get("charge", resposta)

    try:
        value = float(cobranca.get("value", 0))
    except (ValueError, TypeError):
        return False

    return (
        str(cobranca.get("status", "")).lower() == "paid"
        and round(value, 2) == round(PRODUCT_VALUE, 2)
    )


# ============================================================
# CONVITE E ENTREGA IDEMPOTENTE
# ============================================================

def criar_link_convite():
    expire_timestamp = int(
        (datetime.utcnow() + timedelta(hours=24)).timestamp()
    )

    result = telegram_request("createChatInviteLink", {
        "chat_id": CHANNEL_ID,
        "member_limit": 1,
        "expire_date": expire_timestamp
    })

    return result["result"]["invite_link"]


def processar_acesso(payment_id):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT telegram_chat_id, status, invite_link
            FROM payment_fulfillments
            WHERE payment_id = %s
            FOR UPDATE
        """, (str(payment_id),))

        row = cur.fetchone()

        if not row:
            conn.rollback()
            print(f"PEDIDO NAO ENCONTRADO: {payment_id}")
            return False

        chat_id, status, existing_invite = row

        if status == "sent" and existing_invite:
            conn.commit()
            print(f"ACESSO JA ENTREGUE: {payment_id}")
            return True

        try:
            invite_link = criar_link_convite()

            resultado = enviar_mensagem(
                chat_id,
                "🎉 <b>Pagamento confirmado!</b>\n\n"
                "Seu acesso ao conteúdo premium foi liberado.\n\n"
                "👇 <b>CLIQUE ABAIXO PARA ENTRAR:</b>",
                {"inline_keyboard": [[{
                    "text": "🔐 ENTRAR NO CANAL PREMIUM",
                    "url": invite_link
                }]]}
            )

            if not resultado.get("ok"):
                raise Exception("Telegram nao confirmou a mensagem.")

            cur.execute("""
                UPDATE payment_fulfillments
                SET status = 'sent',
                    invite_link = %s,
                    updated_at = NOW()
                WHERE payment_id = %s
            """, (invite_link, str(payment_id)))

            conn.commit()
            print(f"ACESSO ENTREGUE: {payment_id}")
            return True

        except Exception:
            conn.rollback()
            raise

    except Exception as e:
        print(f"ERRO AO ENTREGAR ACESSO {payment_id}: {e}")

        try:
            cur2 = conn.cursor()
            cur2.execute("""
                UPDATE payment_fulfillments
                SET status = 'error', updated_at = NOW()
                WHERE payment_id = %s AND status <> 'sent'
            """, (str(payment_id),))
            conn.commit()
            cur2.close()
        except Exception as db_error:
            conn.rollback()
            print(f"ERRO AO REGISTRAR FALHA: {db_error}")

        raise

    finally:
        conn.close()


# ============================================================
# WEBHOOK ASAAS
# ============================================================

@app.route("/asaas", methods=["POST"])
def asaas_webhook():
    if not ASAAS_WEBHOOK_TOKEN:
        return jsonify({"error": "Webhook token not configured"}), 500

    received_token = request.headers.get("asaas-access-token", "")

    if not hmac.compare_digest(received_token, ASAAS_WEBHOOK_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    event_type = body.get("event")
    payment = body.get("payment") or {}
    payment_id = payment.get("id")

    print(f"WEBHOOK ASAAS: EVENT={event_type} PAYMENT={payment_id}")

    if event_type != "PAYMENT_RECEIVED" or not payment_id:
        return jsonify({"received": True}), 200

    try:
        if not validar_pagamento_asaas(payment_id):
            return jsonify({"received": True}), 200

        if not processar_acesso(str(payment_id)):
            return jsonify({"error": "Order not found"}), 500

    except Exception as e:
        print(f"ERRO WEBHOOK ASAAS: {e}")
        return jsonify({"error": "Processing failed"}), 500

    return jsonify({"received": True}), 200


# ============================================================
# WEBHOOK FLOWINPAY
# ============================================================

@app.route("/flowinpay", methods=["POST"])
def flowinpay_webhook():
    if not FLOWINPAY_WEBHOOK_SECRET:
        print("FLOWINPAY_WEBHOOK_SECRET nao configurado.")
        return jsonify({"error": "Webhook secret not configured"}), 500

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

    print(f"WEBHOOK FLOWINPAY: EVENT={event_type} CHARGE={charge_id}")

    if event_type == "webhook.test":
        return jsonify({"received": True}), 200

    if event_type != "charge.completed":
        return jsonify({"received": True}), 200

    if charge_id is None:
        print("WEBHOOK FLOWINPAY SEM ID DA COBRANCA.")
        return jsonify({"error": "Missing charge ID"}), 400

    payment_id = f"flowinpay-{charge_id}"

    try:
        conn = get_db()

        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM payment_fulfillments WHERE payment_id = %s",
                (payment_id,)
            )
            exists = cur.fetchone() is not None
            cur.close()
        finally:
            conn.close()

        if not exists:
            print(f"COBRANCA SEM PEDIDO REGISTRADO: {payment_id}")
            return jsonify({"error": "Order not found"}), 404

        if not validar_pagamento_flowinpay(charge_id):
            print(f"PAGAMENTO FLOWINPAY NAO VALIDADO: {payment_id}")
            return jsonify({"error": "Payment not confirmed"}), 409

        if not processar_acesso(payment_id):
            return jsonify({"error": "Delivery failed"}), 500

    except Exception as e:
        print(f"ERRO WEBHOOK FLOWINPAY: {e}")
        return jsonify({"error": "Processing failed"}), 500

    return jsonify({"received": True}), 200


# ============================================================
# STATUS E TELEGRAM
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "Telegram PIX Bot funcionando em PRODUCAO.", 200


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    message = update.get("message")

    if message:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text", "")

        if chat_id and text.startswith("/start"):
            mostrar_produto(chat_id)

        return jsonify({"ok": True}), 200

    callback = update.get("callback_query")

    if callback:
        callback_id = callback.get("id")

        if callback_id:
            try:
                responder_callback(callback_id)
            except Exception as e:
                print(f"ERRO CALLBACK: {e}")

        data = callback.get("data", "")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if data == "comprar" and chat_id:
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
