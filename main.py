from fastapi import FastAPI, Request
import requests
import os

app = FastAPI()

VERIFY_TOKEN = "siddharth_verify_token"
WHATSAPP_TOKEN = "EAANWTaYRRcwBQtQG2oE838dyJPuz8PfWD1OyVrhagJNsJhi1xZCikngn7lmYaXgDI7P6tJvtOp2N3vg3y319Kl6ekWQeL3UNTb5DPI2ZCpjCuZAzMRHjJuKjGYz34lgRpDWJ8XIeQs32QjsDd4F025YVZCwxhUX2bMB9fwNxv2uHyz9dyLm04oule17iZA3b3SDaftGj5RKJhefWZBsaFZA0tQkrOdcn7CBZCXYJeZCxatNZCNB0YDk6oLilZBOgZCZAGY8Mo27KzpegtVuOpHnZB5Ne2ZBi2hb"
PHONE_NUMBER_ID = "1005546725973223"
SARVAM_API_KEY = "sk_hd62veik_OvDhMIJXYoUfTPSa5DSdRJVj"

@app.get("/webhook")
async def verify(mode: str = None, challenge: str = None, verify_token: str = None):
    if verify_token == VERIFY_TOKEN:
        return int(challenge)
    return "Verification failed"

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"]
        sender = data["entry"][0]["changes"][0]["value"]["messages"][0]["from"]

        reply = generate_reply(message)

        send_whatsapp(sender, reply)

    except:
        pass

    return {"status": "ok"}

def generate_reply(text):
    return f"You said: {text}"

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
