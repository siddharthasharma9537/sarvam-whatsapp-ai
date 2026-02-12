from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests
import base64
import os

app = FastAPI()

# =========================
# CONFIG
# =========================

VERIFY_TOKEN = "siddharth_verify_token"

WHATSAPP_TOKEN = "EAANWTaYRRcwBQpW2H2ZClGjeVvVKjCfZBNyc8qWCuPl1AYsBcHl5BZCa5ERxEtsrIVqurBKBX4TW3lqWnkgWwfsrICwi16YWRZAwPPuBLdPtYUxNxTJgZBiG21C7AJ8pPNXaZAIegbjuZCZBAFgEiW1bzTCCAiHeErkYSUgg5nZC1a8dJEcz6RZBbbsv77d3NDQTLvs6al7t81aS74JiodA7rX9cOC6gwiWYLnUKdklVL7JjZA0ZArWlp70nhWJCf9GgLRZASLeFMUXtEU9fosKdkzOOxmO7nrWCJ"

PHONE_NUMBER_ID = "1005546725973223"

SARVAM_API_KEY = "sk_hd62veik_OvDhMIJXYoUfTPSa5DSdRJVj"


# Devotee database (temporary memory)
devotees = {}


# =========================
# WEBHOOK VERIFY
# =========================

@app.get("/webhook")
async def verify(request: Request):

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)


# =========================
# MAIN WEBHOOK
# =========================

@app.post("/webhook")
async def webhook(request: Request):

    data = await request.json()

    try:

        value = data["entry"][0]["changes"][0]["value"]
        message_obj = value["messages"][0]

        sender = message_obj["from"]
        msg_type = message_obj["type"]

        # TEXT MESSAGE
        if msg_type == "text":

            message = message_obj["text"]["body"].lower()

            if message in ["hi", "hello", "namaskaram", "menu", "start"]:
                send_menu(sender)

            elif message.startswith("register"):
                register_devotee(sender, message)

            else:
                reply = sarvam_reply(message)
                send_whatsapp(sender, reply)

        # BUTTON CLICK
        elif msg_type == "interactive":

            button_id = message_obj["interactive"]["button_reply"]["id"]

            reply = handle_button(button_id)

            send_whatsapp(sender, reply)

        # VOICE MESSAGE
        elif msg_type == "audio":

            media_id = message_obj["audio"]["id"]

            transcript = speech_to_text(media_id)

            reply = sarvam_reply(transcript)

            send_whatsapp(sender, reply)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}


# =========================
# MENU
# =========================

def send_menu(to):

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "🙏 Namaskaram!\n\nSri Parvathi Jadala Ramalingeshwara Swamy Devasthanam Assistant\n\nPlease choose:"
            },
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "timings", "title": "Temple Timings"}},
                    {"type": "reply", "reply": {"id": "location", "title": "Temple Location"}},
                    {"type": "reply", "reply": {"id": "giripradakshina", "title": "Giripradakshina"}},
                    {"type": "reply", "reply": {"id": "register", "title": "Register Devotee"}}
                ]
            }
        }
    }

    requests.post(url, headers=headers, json=data)


# =========================
# BUTTON HANDLER
# =========================

def handle_button(button_id):

    if button_id == "timings":

        return """🕉 Temple timings:

Morning: 5:00 AM – 12:30 PM
Evening: 3:00 PM – 7:00 PM

Monday & Friday:
Morning: till 1:00 PM
Evening: till 7:30 PM"""


    if button_id == "location":

        return """📍 Temple location:

Cheruvugattu, Nalgonda District

Google Maps:
https://maps.google.com/?q=17.17491,79.21219"""


    if button_id == "giripradakshina":

        return """🚶 Giripradakshina:

Sacred pradakshina around Cheruvugattu hill.

Performed especially during Maha Shivaratri."""


    if button_id == "register":

        return """Please type:

register YourName YourVillage

Example:
register Siddharth Nalgonda"""


# =========================
# REGISTER DEVOTEE
# =========================

def register_devotee(phone, message):

    parts = message.split()

    if len(parts) >= 3:

        name = parts[1]
        village = parts[2]

        devotees[phone] = {
            "name": name,
            "village": village
        }

        send_whatsapp(phone, f"🙏 Registration successful.\nWelcome {name} from {village}.")

    else:

        send_whatsapp(phone, "Invalid format.\nType: register Name Village")


# =========================
# SARVAM AI
# =========================

def sarvam_reply(user_message):

    url = "https://api.sarvam.ai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {SARVAM_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "sarvam-m",
        "messages": [
            {
                "role": "system",
                "content": """You are temple assistant of Sri Parvathi Jadala Ramalingeshwara Swamy Devasthanam.

Reply Telugu or English based on user."""
            },
            {
                "role": "user",
                "content": user_message
            }
        ]
    }

    response = requests.post(url, headers=headers, json=data)

    if response.status_code == 200:

        return response.json()["choices"][0]["message"]["content"]

    return "క్షమించండి. Please try again."


# =========================
# SPEECH TO TEXT
# =========================

def speech_to_text(media_id):

    url = f"https://graph.facebook.com/v18.0/{media_id}"

    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    res = requests.get(url, headers=headers).json()

    audio_url = res["url"]

    audio_data = requests.get(audio_url, headers=headers).content

    # send to Sarvam speech endpoint
    stt_url = "https://api.sarvam.ai/v1/speech-to-text"

    files = {"file": audio_data}

    headers = {"Authorization": f"Bearer {SARVAM_API_KEY}"}

    response = requests.post(stt_url, headers=headers, files=files)

    return response.json()["text"]


# =========================
# SEND WHATSAPP TEXT
# =========================

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


# =========================
# BROADCAST ANNOUNCEMENT
# =========================

def broadcast(message):

    for phone in devotees:

        send_whatsapp(phone, message)