import os
import requests
import psycopg2

from flask import Flask, request, jsonify
from datetime import date, datetime, timedelta, timezone


app = Flask(__name__)


# ============================================================
# CONFIGURAÇÕES
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY")
ASAAS_WEBHOOK_TOKEN = os.environ.get("ASAAS_WEBHOOK_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

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
# BANCO DE DADOS
# ============================================================

def conectar_banco():

    if not DATABASE_URL:

        raise Exception(
            "DATABASE_URL não configurada."
        )

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        connect_timeout=10
    )


def inicializar_banco():

    print(
        "INICIALIZANDO BANCO DE DADOS..."
    )

    conn = None

    try:

        conn = conectar_banco()

        cursor = conn.cursor()

        # ====================================================
        # TABELA DE PAGAMENTOS
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_fulfillments (

                payment_id TEXT PRIMARY KEY,

                telegram_chat_id TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'pending',

                invite_link TEXT,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        # ====================================================
        # TABELA DE EVENTOS ASAAS
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (

                event_id TEXT PRIMARY KEY,

                processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        conn.commit()

        cursor.close()

        print(
            "BANCO DE DADOS PRONTO."
        )

    except Exception as erro:

        print(
            "ERRO AO INICIALIZAR BANCO:",
            erro
        )

        if conn:

            conn.rollback()

        raise

    finally:

        if conn:

            conn.close()


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

    print(
        "PROCURANDO CLIENTE NO ASAAS..."
    )

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

        print(
            "ERRO AO BUSCAR CLIENTE"
        )

        return None

    data = response.json()

    clientes = data.get(
        "data",
        []
    )

    if not clientes:

        print(
            "CLIENTE NÃO ENCONTRADO"
        )

        return None

    cliente = clientes[0]

    cliente_id = cliente.get(
        "id"
    )

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

    print(
        "CRIANDO COBRANÇA ASAAS..."
    )

    payload = {

        "customer": customer_id,

        "billingType": "PIX",

        "value": PRODUCT_VALUE,

        "description": "Acesso Premium",

        "dueDate": date.today().isoformat(),

        # Identifica qual usuário Telegram
        # gerou a cobrança
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

    print(
        "BUSCANDO QR CODE PIX..."
    )

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

    print(
        "CRIANDO LINK DE CONVITE..."
    )

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

            # Apenas uma pessoa pode entrar
            # usando este link
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
# GARANTIR REGISTRO DO PAGAMENTO
# ============================================================

def criar_registro_pagamento(
    payment_id,
    telegram_chat_id
):

    conn = conectar_banco()

    try:

        cursor = conn.cursor()

        cursor.execute(

            """
            INSERT INTO payment_fulfillments
            (
                payment_id,
                telegram_chat_id,
                status
            )

            VALUES
            (
                %s,
                %s,
                'pending'
            )

            ON CONFLICT (payment_id)
            DO NOTHING
            """,

            (
                payment_id,
                str(telegram_chat_id)
            )
        )

        conn.commit()

        cursor.close()

    finally:

        conn.close()


# ============================================================
# PROCESSAR ACESSO
# ============================================================

def processar_acesso(
    payment_id,
    telegram_chat_id
):

    conn = conectar_banco()

    try:

        cursor = conn.cursor()

        # ====================================================
        # GARANTIR QUE O PAGAMENTO EXISTE
        # ====================================================

        cursor.execute(

            """
            INSERT INTO payment_fulfillments
            (
                payment_id,
                telegram_chat_id,
                status
            )

            VALUES
            (
                %s,
                %s,
                'pending'
            )

            ON CONFLICT (payment_id)
            DO NOTHING
            """,

            (
                payment_id,
                str(telegram_chat_id)
            )
        )

        conn.commit()

        # ====================================================
        # BLOQUEAR O REGISTRO
        # ====================================================

        cursor.execute(

            """
            SELECT
                status,
                invite_link,
                telegram_chat_id

            FROM payment_fulfillments

            WHERE payment_id = %s

            FOR UPDATE
            """,

            (
                payment_id,
            )
        )

        registro = cursor.fetchone()

        if not registro:

            print(
                "ERRO: REGISTRO DO PAGAMENTO "
                "NÃO ENCONTRADO."
            )

            conn.rollback()

            return False

        status = registro[0]

        invite_link = registro[1]

        telegram_chat_id_db = registro[2]

        # ====================================================
        # PAGAMENTO JÁ ENTREGUE
        # ====================================================

        if status == "sent":

            print(
                "PAGAMENTO JÁ ENTREGUE:",
                payment_id
            )

            conn.commit()

            return True

        # ====================================================
        # CRIAR LINK CASO AINDA NÃO EXISTA
        # ====================================================

        if not invite_link:

            invite_link = criar_link_convite()

            if not invite_link:

                print(
                    "NÃO FOI POSSÍVEL CRIAR "
                    "O LINK DE CONVITE."
                )

                conn.rollback()

                return False

            # Salva o link imediatamente.
            # Se o envio da mensagem falhar,
            # podemos reutilizar o mesmo link.
            cursor.execute(

                """
                UPDATE payment_fulfillments

                SET
                    invite_link = %s,
                    status = 'ready',
                    updated_at = NOW()

                WHERE payment_id = %s
                """,

                (
                    invite_link,
                    payment_id
                )
            )

        # ====================================================
        # ENVIAR ACESSO
        # ====================================================

        resposta_telegram = telegram(

            "sendMessage",

            {

                "chat_id": int(
                    telegram_chat_id_db
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

        # ====================================================
        # TELEGRAM ACEITOU
        # ====================================================

        if resposta_telegram.get("ok"):

            cursor.execute(

                """
                UPDATE payment_fulfillments

                SET
                    status = 'sent',
                    updated_at = NOW()

                WHERE payment_id = %s
                """,

                (
                    payment_id,
                )
            )

            conn.commit()

            print(
                "ACESSO ENTREGUE COM SUCESSO:",
                payment_id
            )

            return True

        # ====================================================
        # TELEGRAM RECUSOU
        # ====================================================

        print(
            "TELEGRAM NÃO ACEITOU "
            "A MENSAGEM."
        )

        conn.rollback()

        return False

    except Exception as erro:

        print(
            "ERRO AO PROCESSAR ACESSO:",
            erro
        )

        conn.rollback()

        return False

    finally:

        cursor.close()

        conn.close()


# ============================================================
# REGISTRAR EVENTO PROCESSADO
# ============================================================

def registrar_evento(event_id):

    if not event_id:

        return

    conn = conectar_banco()

    try:

        cursor = conn.cursor()

        cursor.execute(

            """
            INSERT INTO processed_events
            (
                event_id
            )

            VALUES
            (
                %s
            )

            ON CONFLICT (event_id)
            DO NOTHING
            """,

            (
                event_id,
            )
        )

        conn.commit()

        cursor.close()

    finally:

        conn.close()


# ============================================================
# VERIFICAR EVENTO JÁ PROCESSADO
# ============================================================

def evento_ja_processado(event_id):

    if not event_id:

        return False

    conn = conectar_banco()

    try:

        cursor = conn.cursor()

        cursor.execute(

            """
            SELECT 1

            FROM processed_events

            WHERE event_id = %s

            LIMIT 1
            """,

            (
                event_id,
            )
        )

        resultado = cursor.fetchone()

        cursor.close()

        return resultado is not None

    finally:

        conn.close()


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return "BOT ONLINE - ASAAS"


# ============================================================
# WEBHOOK TELEGRAM
# ============================================================

@app.route(
    "/telegram",
    methods=["POST"]
)
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

                            "text":
                            "💳 COMPRAR — R$ 24,90",

                            "callback_data":
                            "comprar"
                        }

                    ]

                ]
            }

            telegram(

                "sendMessage",

                {

                    "chat_id":
                    chat_id,

                    "text": (

                        "🔥 ACESSO PREMIUM\n\n"

                        "Tenha acesso ao nosso "
                        "conteúdo exclusivo.\n\n"

                        "💰 Valor: R$ 24,90\n\n"

                        "Clique abaixo para "
                        "realizar o pagamento:"
                    ),

                    "reply_markup":
                    keyboard
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

                        "chat_id":
                        chat_id,

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

                            "chat_id":
                            chat_id,

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

                            "chat_id":
                            chat_id,

                            "text":
                            mensagem
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

@app.route(
    "/asaas",
    methods=["POST"]
)
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
    # EVENTO JÁ PROCESSADO
    # ========================================================

    if event_id and evento_ja_processado(
        event_id
    ):

        print(
            "EVENTO JÁ PROCESSADO."
        )

        print(
            "EVENTO IGNORADO:",
            event_id
        )

        return jsonify(
            {
                "ok": True,
                "duplicate": True
            }
        )

    # ========================================================
    # SÓ PROCESSAR PAYMENT_RECEIVED
    # ========================================================

    if evento != "PAYMENT_RECEIVED":

        print(
            "EVENTO NÃO UTILIZADO:",
            evento
        )

        if event_id:

            registrar_evento(
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

    payment_value = payment.get(
        "value"
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
        "VALOR:",
        payment_value
    )

    print(
        "TELEGRAM CHAT ID:",
        telegram_chat_id
    )

    # ========================================================
    # VALIDAR ID
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
    # CONFIRMAR VALOR
    # ========================================================

    try:

        valor = float(
            payment_value
        )

    except:

        print(
            "VALOR DO PAGAMENTO INVÁLIDO."
        )

        return jsonify(
            {
                "ok": True
            }
        )

    if abs(
        valor - PRODUCT_VALUE
    ) > 0.01:

        print(
            "VALOR DIFERENTE DO PRODUTO."
        )

        print(
            "ESPERADO:",
            PRODUCT_VALUE
        )

        print(
            "RECEBIDO:",
            valor
        )

        return jsonify(
            {
                "ok": True
            }
        )

    # ========================================================
    # ENTREGAR ACESSO
    # ========================================================

    sucesso = processar_acesso(

        payment_id,

        telegram_chat_id
    )

    if not sucesso:

        print(
            "FALHA AO ENTREGAR ACESSO."
        )

        # Retornamos erro para o Asaas
        # tentar novamente.
        return jsonify(
            {
                "ok": False
            }
        ), 500

    # ========================================================
    # MARCAR EVENTO COMO PROCESSADO
    # ========================================================

    if event_id:

        registrar_evento(
            event_id
        )

    print(
        "WEBHOOK PROCESSADO COM SUCESSO."
    )

    return jsonify(
        {
            "ok": True
        }
    )


# ============================================================
# INICIALIZAÇÃO
# ============================================================

try:

    inicializar_banco()

except Exception as erro:

    print(
        "AVISO: BANCO NÃO PÔDE SER "
        "INICIALIZADO:",
        erro
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
