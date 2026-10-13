import os
import time
import hmac
import hashlib
from datetime import datetime, timedelta, date, timezone

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN")
FLOWINPAY_API_KEY = os.getenv("FLOWINPAY_API_KEY")
FLOWINPAY_WEBHOOK_SECRET = os.getenv("FLOWINPAY_WEBHOOK_SECRET")
FLOWINPAY_DIAGNOSTIC_TOKEN = os.getenv("FLOWINPAY_DIAGNOSTIC_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "flowinpay").lower().strip()

ASAAS_API = "https://api.asaas.com/v3"
FLOWINPAY_API = "https://app.flowinpay.com.br/api/v1"
CHANNEL_ID = -1004395341778
PRODUCT_NAME = "ACESSO PREMIUM VIP"
PRODUCT_VALUE = 24.90
SUBSCRIPTION_DAYS = 30
GRACE_HOURS = 24

WELCOME_TEXT = (
    "🔥 <b>PROMOÇÃO VIP — ACESSO PREMIUM</b> 🔥\n\n"
    "👀 <b>Mais de 1.000 conteúdos em uma área exclusiva!</b>\n\n"
    "Entre para conhecer uma seleção premium de conteúdo adulto, novidades "
    "e materiais exclusivos em um só lugar.\n\n"
    "💎 Acesso por 30 dias\n"
    "⚡ Liberação após a confirmação do PIX\n"
    "🔞 Exclusivo para maiores de 18 anos\n\n"
    "👇 <b>Quer conhecer a área VIP?</b>\n"
    "Toque no botão abaixo e garanta seu acesso."
)


def utcnow():
    return datetime.now(timezone.utc)


def db_connect():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL nao configurada.")
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_fulfillments (
                payment_id TEXT PRIMARY KEY,
                telegram_chat_id BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                invite_link TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            ALTER TABLE payment_fulfillments
            ADD COLUMN IF NOT EXISTS payment_link TEXT,
            ADD COLUMN IF NOT EXISTS payment_type TEXT NOT NULL DEFAULT 'initial',
            ADD COLUMN IF NOT EXISTS renewal_key TEXT,
            ADD COLUMN IF NOT EXISTS payment_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS subscription_applied BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ
        """)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS payment_fulfillments_renewal_key_uq ON payment_fulfillments (renewal_key) WHERE renewal_key IS NOT NULL")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                telegram_chat_id BIGINT PRIMARY KEY,
                expires_at TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                current_invite_link TEXT,
                reminder_3d_sent BOOLEAN NOT NULL DEFAULT FALSE,
                reminder_due_sent BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        print("BANCO DE DADOS PRONTO.")
    finally:
        conn.close()


def telegram_request(method, payload):
    if not TELEGRAM_TOKEN:
        raise Exception("TELEGRAM_TOKEN nao configurado.")
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
        json=payload, timeout=25
    )
    try:
        data = response.json()
    except Exception:
        data = {"ok": False, "description": "Resposta nao JSON"}
    if response.status_code >= 400 or not data.get("ok"):
        raise Exception(f"Erro Telegram {method}: HTTP {response.status_code}; {data.get('description', '')}")
    return data


def enviar_mensagem(chat_id, texto, reply_markup=None):
    payload = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_request("sendMessage", payload)


def responder_callback(callback_id):
    return telegram_request("answerCallbackQuery", {"callback_query_id": callback_id})


def asaas_request(method, endpoint, payload=None):
    if not ASAAS_API_KEY:
        raise Exception("ASAAS_API_KEY nao configurada.")
    response = requests.request(
        method, f"{ASAAS_API}{endpoint}",
        headers={"access_token": ASAAS_API_KEY, "Content-Type": "application/json", "User-Agent": "TelegramPixBot/2.0"},
        json=payload, timeout=25
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
        method, f"{FLOWINPAY_API}{endpoint}",
        headers={"X-Api-Key": FLOWINPAY_API_KEY, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "TelegramPixBot/2.0"},
        json=payload, timeout=25
    )
    try:
        data = response.json()
    except Exception:
        data = {"message": "Resposta nao JSON"}
    print(f"FLOWINPAY {method} {endpoint}: HTTP {response.status_code}")
    if response.status_code >= 400:
        raise Exception(f"FlowinPay HTTP {response.status_code}: {data}")
    return data


def criar_cobranca_flowinpay(chat_id, renewal_key=None):
    resposta = flowinpay_request("POST", "/charges", {"value": PRODUCT_VALUE, "description": PRODUCT_NAME})
    cobranca = resposta.get("charge", resposta)
    charge_id = cobranca.get("id")
    payment_link = cobranca.get("payment_link_url")
    if charge_id is None or not payment_link:
        raise Exception("FlowinPay nao retornou ID e link da cobranca.")
    payment_id = f"flowinpay-{charge_id}"
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO payment_fulfillments
                (payment_id, telegram_chat_id, status, payment_link, payment_type, renewal_key)
            VALUES (%s, %s, 'pending', %s, %s, %s)
            ON CONFLICT (payment_id) DO NOTHING
        """, (payment_id, chat_id, payment_link, "renewal" if renewal_key else "initial", renewal_key))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    print(f"COBRANCA FLOWINPAY CRIADA: {payment_id}")
    return payment_id, payment_link


def localizar_cliente_asaas():
    data = asaas_request("GET", "/customers?limit=100")
    for cliente in data.get("data", []):
        if (cliente.get("name") or "").strip().lower() == "cliente tele":
            return cliente["id"]
    raise Exception("Cliente 'Cliente tele' nao encontrado no Asaas.")


def criar_cobranca_asaas(chat_id):
    customer_id = localizar_cliente_asaas()
    pagamento = asaas_request("POST", "/payments", {
        "customer": customer_id, "billingType": "PIX", "value": PRODUCT_VALUE,
        "dueDate": date.today().isoformat(), "description": PRODUCT_NAME,
        "externalReference": f"telegram-{chat_id}-{int(time.time())}"
    })
    if not pagamento.get("id") or not pagamento.get("invoiceUrl"):
        raise Exception("Resposta incompleta do Asaas.")
    payment_id = str(pagamento["id"])
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO payment_fulfillments (payment_id, telegram_chat_id, payment_link) VALUES (%s, %s, %s) ON CONFLICT (payment_id) DO NOTHING", (payment_id, chat_id, pagamento["invoiceUrl"]))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return payment_id, pagamento["invoiceUrl"]


def mostrar_produto(chat_id):
    enviar_mensagem(chat_id, WELCOME_TEXT, {"inline_keyboard": [[{"text": "💎 COMPRAR ACESSO VIP — R$ 24,90", "callback_data": "comprar"}]]})


def processar_compra(chat_id):
    try:
        if PAYMENT_PROVIDER == "flowinpay":
            payment_id, invoice_url = criar_cobranca_flowinpay(chat_id)
        elif PAYMENT_PROVIDER == "asaas":
            payment_id, invoice_url = criar_cobranca_asaas(chat_id)
        else:
            raise Exception("PAYMENT_PROVIDER deve ser asaas ou flowinpay.")
        enviar_mensagem(chat_id,
            "✅ <b>Seu PIX está pronto!</b>\n\n"
            "Valor: <b>R$ 24,90</b>\n"
            "Plano: <b>30 dias de acesso VIP</b>\n\n"
            "Toque no botão abaixo para pagar. O acesso será liberado após a confirmação do pagamento.",
            {"inline_keyboard": [[{"text": "💰 PAGAR PIX — R$ 24,90", "url": invoice_url}]]})
        print(f"COBRANCA CRIADA: {payment_id}; provedor={PAYMENT_PROVIDER}")
    except Exception as e:
        print(f"ERRO AO CRIAR COBRANCA: {e}")
        enviar_mensagem(chat_id, "❌ Não foi possível gerar o pagamento agora. Tente novamente em alguns instantes.")


def validar_pagamento_asaas(payment_id):
    pagamento = asaas_request("GET", f"/payments/{payment_id}")
    try:
        value = float(pagamento.get("value", 0))
    except (ValueError, TypeError):
        return False
    return pagamento.get("status") == "RECEIVED" and round(value, 2) == round(PRODUCT_VALUE, 2)


def validar_pagamento_flowinpay(charge_id):
    resposta = flowinpay_request("GET", f"/charges/{charge_id}")
    cobranca = resposta.get("charge", resposta)
    try:
        value = float(cobranca.get("value", 0))
    except (ValueError, TypeError):
        return False
    return str(cobranca.get("status", "")).lower() == "paid" and round(value, 2) == round(PRODUCT_VALUE, 2)


def criar_link_convite():
    expires = int((utcnow() + timedelta(hours=24)).timestamp())
    result = telegram_request("createChatInviteLink", {"chat_id": CHANNEL_ID, "member_limit": 1, "expire_date": expires})
    return result["result"]["invite_link"]


def obter_status_membro(chat_id):
    result = telegram_request("getChatMember", {"chat_id": CHANNEL_ID, "user_id": chat_id})["result"]
    status = result.get("status", "left")
    if status == "restricted":
        return "member" if result.get("is_member") else "left"
    return status


def garantir_acesso_telegram(chat_id, expires_at, renewal=False):
    try:
        member_status = obter_status_membro(chat_id)
    except Exception as e:
        print(f"NAO FOI POSSIVEL CONSULTAR MEMBRO {chat_id}: {e}")
        member_status = "unknown"

    if member_status in ("member", "administrator", "creator"):
        texto = ("✅ <b>Renovação confirmada!</b>\n\n" if renewal else "✅ <b>Pagamento confirmado!</b>\n\n")
        texto += f"Seu acesso VIP está ativo até <b>{expires_at.strftime('%d/%m/%Y %H:%M UTC')}</b>."
        enviar_mensagem(chat_id, texto)
        return None

    if member_status == "kicked":
        telegram_request("unbanChatMember", {"chat_id": CHANNEL_ID, "user_id": chat_id, "only_if_banned": True})

    invite = criar_link_convite()
    texto = ("🎉 <b>Renovação confirmada!</b>\n\n" if renewal else "🎉 <b>Pagamento confirmado!</b>\n\n")
    texto += f"Seu acesso VIP está ativo até <b>{expires_at.strftime('%d/%m/%Y %H:%M UTC')}</b>.\n\n"
    texto += "👇 <b>Use seu convite individual para entrar no canal:</b>"
    enviar_mensagem(chat_id, texto, {"inline_keyboard": [[{"text": "🔐 ENTRAR NO CANAL VIP", "url": invite}]]})
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE subscriptions SET current_invite_link = %s, updated_at = NOW() WHERE telegram_chat_id = %s", (invite, chat_id))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return invite


def processar_acesso(payment_id):
    """Aplica cada pagamento uma única vez; entrega pode ser tentada novamente."""
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT telegram_chat_id, payment_type, subscription_applied FROM payment_fulfillments WHERE payment_id = %s FOR UPDATE", (str(payment_id),))
        row = cur.fetchone()
        if not row:
            conn.rollback()
            print(f"PEDIDO NAO ENCONTRADO: {payment_id}")
            return False
        chat_id, payment_type, applied = row
        if not applied:
            cur.execute("SELECT expires_at FROM subscriptions WHERE telegram_chat_id = %s FOR UPDATE", (chat_id,))
            sub = cur.fetchone()
            now = utcnow()
            if sub:
                current_expiry = sub[0]
                if current_expiry.tzinfo is None:
                    current_expiry = current_expiry.replace(tzinfo=timezone.utc)
                base = max(current_expiry, now)
                new_expiry = base + timedelta(days=SUBSCRIPTION_DAYS)
                cur.execute("""
                    UPDATE subscriptions SET expires_at = %s, status = 'active',
                        reminder_3d_sent = FALSE, reminder_due_sent = FALSE,
                        current_invite_link = NULL, updated_at = NOW()
                    WHERE telegram_chat_id = %s
                """, (new_expiry, chat_id))
            else:
                new_expiry = now + timedelta(days=SUBSCRIPTION_DAYS)
                cur.execute("""INSERT INTO subscriptions (telegram_chat_id, expires_at, status)
                    VALUES (%s, %s, 'active')""", (chat_id, new_expiry))
            cur.execute("""UPDATE payment_fulfillments SET subscription_applied = TRUE,
                payment_confirmed = TRUE, paid_at = COALESCE(paid_at, NOW()),
                status = 'paid', updated_at = NOW() WHERE payment_id = %s""", (str(payment_id),))
            conn.commit()
            renewal = payment_type == "renewal"
        else:
            cur.execute("SELECT expires_at FROM subscriptions WHERE telegram_chat_id = %s", (chat_id,))
            sub = cur.fetchone()
            conn.commit()
            if not sub:
                raise Exception("Pagamento ja aplicado, mas assinatura nao encontrada.")
            new_expiry = sub[0]
            if new_expiry.tzinfo is None:
                new_expiry = new_expiry.replace(tzinfo=timezone.utc)
            renewal = payment_type == "renewal"
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Sempre tenta entregar/confirmar acesso, mesmo se o pagamento já havia sido aplicado.
    garantir_acesso_telegram(chat_id, new_expiry, renewal=renewal)
    return True


def processar_webhook_pagamento(provider, payment_id):
    if provider == "flowinpay":
        charge_id = str(payment_id)
        if charge_id.startswith("flowinpay-"):
            charge_id = charge_id[len("flowinpay-"):]
        valid = validar_pagamento_flowinpay(charge_id)
        db_payment_id = f"flowinpay-{charge_id}"
    else:
        db_payment_id = str(payment_id)
        valid = validar_pagamento_asaas(db_payment_id)
    if not valid:
        print(f"PAGAMENTO NAO VALIDADO: {db_payment_id}")
        return False
    return processar_acesso(db_payment_id)


def criar_ou_reutilizar_cobranca_renovacao(chat_id, expires_at):
    if PAYMENT_PROVIDER != "flowinpay":
        raise Exception("A rotina automatica de renovacao esta configurada para FlowinPay. Confirme o provedor antes de usar Asaas.")
    key_expiry = expires_at.astimezone(timezone.utc).isoformat()
    renewal_key = f"renewal:{chat_id}:{key_expiry}"
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT payment_link, payment_id FROM payment_fulfillments WHERE renewal_key = %s", (renewal_key,))
        existing = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if existing and existing[0]:
        return existing[0], existing[1]
    # Uma rotina Cron única deve ser usada; isso evita dois processos simultâneos criando a mesma renovação.
    payment_id, payment_link = criar_cobranca_flowinpay(chat_id, renewal_key=renewal_key)
    return payment_link, payment_id


def executar_rotina_diaria():
    now = utcnow()
    conn = db_connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT telegram_chat_id, expires_at, status, current_invite_link, reminder_3d_sent, reminder_due_sent FROM subscriptions WHERE status = 'active' ORDER BY expires_at")
        subscriptions = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    for sub in subscriptions:
        chat_id = sub["telegram_chat_id"]
        expiry = sub["expires_at"]
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        try:
            if expiry > now and expiry <= now + timedelta(days=3) and not sub["reminder_3d_sent"]:
                link, payment_id = criar_ou_reutilizar_cobranca_renovacao(chat_id, expiry)
                enviar_mensagem(chat_id,
                    "⏳ <b>Sua assinatura VIP está perto de vencer!</b>\n\n"
                    f"Vencimento: <b>{expiry.strftime('%d/%m/%Y %H:%M UTC')}</b>\n"
                    "Renove agora para continuar com acesso sem interrupção.\n\n"
                    "💰 Valor: <b>R$ 24,90 por mais 30 dias</b>",
                    {"inline_keyboard": [[{"text": "🔄 RENOVAR AGORA — R$ 24,90", "url": link}]]})
                conn2 = db_connect()
                try:
                    c2 = conn2.cursor()
                    c2.execute("UPDATE subscriptions SET reminder_3d_sent = TRUE, updated_at = NOW() WHERE telegram_chat_id = %s AND expires_at = %s AND reminder_3d_sent = FALSE", (chat_id, expiry))
                    conn2.commit()
                    c2.close()
                finally:
                    conn2.close()
                print(f"LEMBRETE 3 DIAS ENVIADO: {chat_id}; {payment_id}")

            if now >= expiry and now <= expiry + timedelta(hours=GRACE_HOURS) and not sub["reminder_due_sent"]:
                link, payment_id = criar_ou_reutilizar_cobranca_renovacao(chat_id, expiry)
                enviar_mensagem(chat_id,
                    "⚠️ <b>Sua assinatura VIP vence hoje/está vencida.</b>\n\n"
                    "Você tem até 24 horas após o vencimento para renovar antes da remoção automática do canal.\n\n"
                    "💰 Renovação por mais 30 dias: <b>R$ 24,90</b>",
                    {"inline_keyboard": [[{"text": "🔄 RENOVAR AGORA — R$ 24,90", "url": link}]]})
                conn2 = db_connect()
                try:
                    c2 = conn2.cursor()
                    c2.execute("UPDATE subscriptions SET reminder_due_sent = TRUE, updated_at = NOW() WHERE telegram_chat_id = %s AND expires_at = %s AND reminder_due_sent = FALSE", (chat_id, expiry))
                    conn2.commit()
                    c2.close()
                finally:
                    conn2.close()
                print(f"LEMBRETE VENCIMENTO ENVIADO: {chat_id}; {payment_id}")

            if now > expiry + timedelta(hours=GRACE_HOURS):
                # Só marca como expirado depois de concluir a remoção; se falhar, o Cron tenta novamente.
                conn2 = db_connect()
                try:
                    c2 = conn2.cursor()
                    c2.execute("SELECT status FROM subscriptions WHERE telegram_chat_id = %s AND expires_at = %s FOR UPDATE", (chat_id, expiry))
                    state_row = c2.fetchone()
                    conn2.commit()
                    c2.close()
                finally:
                    conn2.close()
                if state_row and state_row[0] == 'active':
                    if sub["current_invite_link"]:
                        try:
                            telegram_request("revokeChatInviteLink", {"chat_id": CHANNEL_ID, "invite_link": sub["current_invite_link"]})
                        except Exception as e:
                            print(f"AVISO: nao foi possivel revogar convite {chat_id}: {e}")
                    try:
                        telegram_request("banChatMember", {"chat_id": CHANNEL_ID, "user_id": chat_id, "revoke_messages": False})
                        telegram_request("unbanChatMember", {"chat_id": CHANNEL_ID, "user_id": chat_id, "only_if_banned": True})
                        # Banir e desbanir remove a pessoa, mas permite que volte com um novo convite.
                        enviar_mensagem(chat_id, "⛔ Sua assinatura VIP expirou e o acesso ao canal foi encerrado. Para voltar, renove pelo bot usando /start.")
                        conn3 = db_connect()
                        try:
                            c3 = conn3.cursor()
                            c3.execute("UPDATE subscriptions SET status = 'expired', updated_at = NOW() WHERE telegram_chat_id = %s AND expires_at = %s AND status = 'active'", (chat_id, expiry))
                            conn3.commit()
                            c3.close()
                        finally:
                            conn3.close()
                        print(f"ASSINATURA EXPIRADA/ACESSO REMOVIDO: {chat_id}")
                    except Exception as e:
                        print(f"ERRO AO REMOVER ASSINANTE {chat_id}; o Cron tentara novamente: {e}")
        except Exception as e:
            print(f"ERRO NA ROTINA DIARIA PARA {chat_id}: {e}")


@app.route("/diagnostico-flowinpay", methods=["GET"])
def diagnostico_flowinpay():
    if not FLOWINPAY_DIAGNOSTIC_TOKEN:
        return jsonify({"erro": "Diagnostico desativado"}), 404
    received_token = request.args.get("token", "")
    if not hmac.compare_digest(received_token, FLOWINPAY_DIAGNOSTIC_TOKEN):
        return jsonify({"erro": "Nao autorizado"}), 401
    if not FLOWINPAY_API_KEY:
        return jsonify({"diagnostico": "FLOWINPAY_API_KEY nao configurada no Render"}), 500
    try:
        response = requests.get(f"{FLOWINPAY_API}/balance", headers={"X-Api-Key": FLOWINPAY_API_KEY, "Accept": "application/json", "User-Agent": "TelegramPixBot-Diagnostic/2.0"}, timeout=15, allow_redirects=False)
        content_type = response.headers.get("Content-Type", "").lower()
        body_start = response.text[:2000].lower()
        if "text/html" in content_type or "just a moment" in body_start:
            diagnosis = "BLOQUEIO_OU_RESPOSTA_HTML: verificar protecao de seguranca da API."
        elif response.status_code == 200:
            diagnosis = "API_ACESSIVEL: HTTP 200; isso nao confirma permissao para criar cobrancas."
        elif response.status_code == 401:
            diagnosis = "HTTP_401: verificar chave da API."
        elif response.status_code == 403:
            diagnosis = "HTTP_403: acesso negado ou bloqueio de seguranca."
        elif response.status_code == 429:
            diagnosis = "HTTP_429: limite de requisicoes atingido."
        else:
            diagnosis = "RESPOSTA_API: verificar codigo HTTP."
        print(f"DIAGNOSTICO FLOWINPAY: HTTP={response.status_code}; RESULTADO={diagnosis}")
        return jsonify({"http_status": response.status_code, "content_type": content_type[:80], "diagnostico": diagnosis}), 200
    except requests.RequestException as e:
        return jsonify({"diagnostico": "ERRO_DE_CONEXAO", "tipo": type(e).__name__}), 502


@app.route("/asaas", methods=["POST"])
def asaas_webhook():
    if not ASAAS_WEBHOOK_TOKEN:
        return jsonify({"error": "Webhook token not configured"}), 500
    received_token = request.headers.get("asaas-access-token", "")
    if not hmac.compare_digest(received_token, ASAAS_WEBHOOK_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    event_type = body.get("event")
    payment_id = (body.get("payment") or {}).get("id")
    print(f"WEBHOOK ASAAS: EVENT={event_type} PAYMENT={payment_id}")
    if event_type != "PAYMENT_RECEIVED" or not payment_id:
        return jsonify({"received": True}), 200
    try:
        processar_webhook_pagamento("asaas", str(payment_id))
    except Exception as e:
        print(f"ERRO WEBHOOK ASAAS: {e}")
        return jsonify({"error": "Processing failed"}), 500
    return jsonify({"received": True}), 200


@app.route("/flowinpay", methods=["POST"])
def flowinpay_webhook():
    if not FLOWINPAY_WEBHOOK_SECRET:
        return jsonify({"error": "Webhook secret not configured"}), 500
    raw_body = request.get_data(cache=True)
    received_signature = request.headers.get("X-FlowinPay-Signature", "").strip()
    expected_signature = hmac.new(FLOWINPAY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not received_signature or not hmac.compare_digest(received_signature, expected_signature):
        print("WEBHOOK FLOWINPAY: ASSINATURA INVALIDA.")
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    event_type = body.get("event") or request.headers.get("event") or request.headers.get("X-FlowinPay-Event")
    charge_id = (body.get("charge") or {}).get("id")
    print(f"WEBHOOK FLOWINPAY: EVENT={event_type} CHARGE={charge_id}")
    if event_type == "webhook.test" or event_type != "charge.completed":
        return jsonify({"received": True}), 200
    if charge_id is None:
        return jsonify({"error": "Missing charge ID"}), 400
    try:
        payment_id = f"flowinpay-{charge_id}"
        conn = db_connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM payment_fulfillments WHERE payment_id = %s", (payment_id,))
            exists = cur.fetchone() is not None
            cur.close()
        finally:
            conn.close()
        if not exists:
            print(f"COBRANCA SEM PEDIDO REGISTRADO: {payment_id}")
            return jsonify({"error": "Order not found"}), 404
        processar_webhook_pagamento("flowinpay", payment_id)
    except Exception as e:
        print(f"ERRO WEBHOOK FLOWINPAY: {e}")
        return jsonify({"error": "Processing failed"}), 500
    return jsonify({"received": True}), 200


@app.route("/", methods=["GET"])
def home():
    return "Telegram PIX Bot com assinaturas funcionando.", 200


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
        chat = ((callback.get("message") or {}).get("chat") or {})
        chat_id = chat.get("id")
        if data == "comprar" and chat_id:
            processar_compra(chat_id)
        return jsonify({"ok": True}), 200
    return jsonify({"ok": True}), 200


try:
    init_db()
except Exception as e:
    print(f"ERRO AO INICIALIZAR BANCO: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
