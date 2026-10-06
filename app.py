import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


@app.route("/", methods=["GET"])
def home():
    return "BOT TESTE 5"


@app.route("/telegram", methods=["POST"])
def telegram_webhook():

    print("========== RECEBI TELEGRAM ==========")
    print("DADOS:", request.data.decode("utf-8"))

    try:
        data = request.get_json()

        print("JSON:", data)

        if data and "message" in data:

            chat_id = data["message"]["chat"]["id"]

            print("CHAT ID:", chat_id)

            resposta = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "✅ FUNCIONOU! O bot recebeu sua mensagem."
                },
                timeout=20
            )

            print("RESPOSTA TELEGRAM:", resposta.status_code)
            print("CORPO:", resposta.text)

    except Exception as erro:

        print("ERRO:", erro)

    return jsonify({"ok": True})


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
