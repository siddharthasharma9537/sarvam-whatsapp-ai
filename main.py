from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests
import os

app = FastAPI()

# =====================================
# CONFIG
# =====================================

VERIFY_TOKEN = "siddharth_verify_token"

WHATSAPP_TOKEN = "EAANWTaYRRcwBQvpiXgaChb5kXBIB2m2dwFBTIc7OlYPC16oZAMGRmqZBPMhO6B3QVeTehbyBsRR7i2WvaICOdQvMlpLpqZCgGwlZAgK3WxWGZCeYQT4vDY0cdF6uib87ceGc9BeiyzySQ36ZAxhJgFdCs1XwZC914rZC2zxanPvhl1te35LLnJzr8X6baIuseQ9kMwZDZD"

PHONE_NUMBER_ID = "1005546725973223"

SARVAM_API_KEY = "sk_hd62veik_OvDhMIJXYoUfTPSa5DSdRJVj"

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"


# Temporary memory database
devotees = {}


# =====================================
# WEBHOOK VERIFY
# =====================================

@app.get("/webhook")
async def verify(request: Request):

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    print("Webhook verification requested")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("Webhook verified successfully")
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)


# =====================================
# MAIN WEBHOOK
# =====================================

@app.post("/webhook")
async def webhook(request: Request):

    try:

        data = await request.json()

        print("Incoming webhook:", data)

        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"status": "no message"}

        message_obj = value["messages"][0]

        sender = message_obj["from"]
        msg_type = message_obj["type"]

        print(f"Message from {sender} type {msg_type}")

        # =====================================
        # TEXT MESSAGE
        # =====================================

        if msg_type == "text":

            message = message_obj["text"]["body"].lower()

            print("Text:", message)

            if message in ["hi", "hello", "namaskaram", "menu", "start"]:
                send_menu(sender)
                return {"status": "menu sent"}

            if message.startswith("register"):
                register_devotee(sender, message)
                return {"status": "registered"}

            reply = sarvam_reply(message)

            send_whatsapp(sender, reply)

            return {"status": "text processed"}


        # =====================================
        # BUTTON CLICK
        # =====================================

        elif msg_type == "interactive":

            button_id = message_obj["interactive"]["button_reply"]["id"]

            print("Button clicked:", button_id)

            reply = handle_button(button_id)

            send_whatsapp(sender, reply)

            return {"status": "button processed"}


        # =====================================
        # VOICE MESSAGE
        # =====================================

        elif msg_type == "audio":

            media_id = message_obj["audio"]["id"]

            print("Audio received:", media_id)

            transcript = speech_to_text(media_id)

            print("Transcript:", transcript)

            if transcript and transcript.strip() != "":
                reply = sarvam_reply(transcript)
            else:
                reply = "క్షమించండి. Voice message clear ga ardham kaaledu. Please try again."

            send_whatsapp(sender, reply)

            return {"status": "audio processed"}


        # =====================================
        # OTHER TYPES
        # =====================================

        else:

            print("Unsupported message type:", msg_type)

            send_whatsapp(
                sender,
                "🙏 Supported message types:\n• Text\n• Voice\n• Menu buttons"
            )

            return {"status": "unsupported type"}

    except Exception as e:

        print("Webhook error:", str(e))

        return {"status": "error"}


# =====================================
# SEND MENU
# =====================================

def send_menu(to):

    print("Sending menu to", to)

    headers = get_headers()

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
                    {
                        "type": "reply",
                        "reply": {
                            "id": "timings",
                            "title": "Temple Timings"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "location",
                            "title": "Temple Location"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "register",
                            "title": "Register Devotee"
                        }
                    }
                ]
            }
        }
    }

    r = requests.post(GRAPH_URL, headers=headers, json=data)

    print("Menu response:", r.status_code, r.text)


# =====================================
# BUTTON HANDLER
# =====================================

def handle_button(button):

    if button == "timings":

        return (
            "🕉 Temple timings:\n\n"
            "Morning: 5:00 AM – 12:30 PM\n"
            "Evening: 3:00 PM – 7:00 PM\n\n"
            "Monday & Friday:\n"
            "Morning till 1:00 PM\n"
            "Evening till 7:30 PM"
        )

    if button == "location":

        return (
            "📍 Temple Location:\n\n"
            "Cheruvugattu, Nalgonda\n\n"
            "https://maps.google.com/?q=17.17491,79.21219"
        )

    if button == "register":

        return (
            "Please type:\n\n"
            "register YourName YourVillage\n\n"
            "Example:\n"
            "register Siddharth Nalgonda"
        )

    return "Please select menu option."


# =====================================
# REGISTER DEVOTEE
# =====================================

def register_devotee(phone, message):

    parts = message.split()

    if len(parts) >= 3:

        name = parts[1]
        village = parts[2]

        devotees[phone] = {
            "name": name,
            "village": village
        }

        send_whatsapp(
            phone,
            f"🙏 Registration successful\n\nWelcome {name} from {village}"
        )

    else:

        send_whatsapp(phone, "Invalid format.\nUse: register Name Village")


# =====================================
# SARVAM AI CHAT
# =====================================

def sarvam_reply(user_message):

    print("Sarvam processing:", user_message)

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
                "content":
                "You are assistant of Sri Parvathi Jadala Ramalingeshwara Swamy Temple."
            },
            {
                "role": "user",
                "content": user_message
            }
        ]
    }

    try:

        response = requests.post(url, headers=headers, json=data)

        result = response.json()

        return result["choices"][0]["message"]["content"]

    except Exception as e:

        print("Sarvam error:", e)

        return "Please try again."


# =====================================
# SPEECH TO TEXT
# =====================================

def speech_to_text(media_id):

    try:

        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

        media = requests.get(
            f"https://graph.facebook.com/v18.0/{media_id}",
            headers=headers
        ).json()

        audio_url = media["url"]

        audio = requests.get(audio_url, headers=headers).content

        stt_url = "https://api.sarvam.ai/v1/speech-to-text"

        files = {"file": audio}

        headers = {"Authorization": f"Bearer {SARVAM_API_KEY}"}

        res = requests.post(stt_url, headers=headers, files=files)

        return res.json().get("text", "")

    except Exception as e:

        print("STT error:", e)

        return ""


# =====================================
# SEND MESSAGE
# =====================================

def send_whatsapp(to, message):

    headers = get_headers()

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    requests.post(GRAPH_URL, headers=headers, json=data)


# =====================================
# HEADERS
# =====================================

def get_headers():

    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }


# =====================================
# BROADCAST
# =====================================

def broadcast(message):

    for phone in devotees:

        send_whatsapp(phone, message)
