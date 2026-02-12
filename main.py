from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests

app = FastAPI()

VERIFY_TOKEN = "siddharth_verify_token"
WHATSAPP_TOKEN = "EAANWTaYRRcwBQjvVtGxkWtKTOEs71luiHYFO9fI0sEQFTTQ2LaG7K7UgTVwY7fkrrjmCen7LsJtwxkR9B6f6hXRr9gmCfOh1L9rWJaL3uHte2JLtZCZBcPRXkyrRZCt1Ek1ZCsyfsLzDFkkycdCa0tCMiez71qsNQatuGWtu0LmC46ff9AgSLdu5ODeIM7WB76BaZApLC62FKVx7K3YZAQSsaPW9cHJ9b58togZA9RT8pz7nGIrrrUolGxzwDKIZCwmguMTT1XlTJr0ZAmQ58k8EBqx6u"
PHONE_NUMBER_ID = "1005546725973223"


@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"]
        sender = data["entry"][0]["changes"][0]["value"]["messages"][0]["from"]

        reply = f"You said: {message}"

        send_whatsapp(sender, reply)

    except Exception as e:
        print(e)

    return {"status": "ok"}


def send_whatsapp(to, message):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    requests.post(url, headers=headers, json=data)
