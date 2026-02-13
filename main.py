from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests
import os
import base64

app = FastAPI()

# =====================================================
# CONFIG (ENV VARIABLES)
# =====================================================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")

PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Temporary memory
devotees = {}

# =====================================================
# WEBHOOK VERIFICATION
# =====================================================

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


# =====================================================
# MAIN WEBHOOK
# =====================================================

@app.post("/webhook")
async def webhook(request: Request):

    data = await request.json()

    print("Incoming webhook:", data)

    try:

        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"status": "no message"}

        message_obj = value["messages"][0]

        sender = message_obj["from"]

        msg_type = message_obj["type"]

        print("Sender:", sender)
        print("Type:", msg_type)

        # =====================================================
        # TEXT MESSAGE
        # =====================================================

        if msg_type == "text":

            text = message_obj["text"]["body"]

            print("Text:", text)

            text_lower = text.lower()

            if text_lower in ["hi", "hello", "namaskaram", "menu", "start"]:
                send_menu(sender)
                return {"status": "menu sent"}

            if text_lower.startswith("register"):
                register_devotee(sender, text)
                return {"status": "registered"}

            reply = gemini_reply(text)

            send_whatsapp(sender, reply)

        # =====================================================
        # BUTTON CLICK
        # =====================================================

        elif msg_type == "interactive":

            button_id = message_obj["interactive"]["button_reply"]["id"]

            reply = handle_button(button_id)

            send_whatsapp(sender, reply)

        # =====================================================
        # VOICE MESSAGE
        # =====================================================

        elif msg_type == "audio":

            media_id = message_obj["audio"]["id"]

            print("Audio received:", media_id)

            transcript = speech_to_text(media_id)

            print("Transcript:", transcript)

            if transcript:

                reply = gemini_reply(transcript)

            else:

                reply = "క్షమించండి. Voice message ardham kaaledu. Please try again."

            send_whatsapp(sender, reply)

    except Exception as e:

        print("Webhook error:", e)

    return {"status": "ok"}


# =====================================================
# SEND MENU
# =====================================================

def send_menu(to):

    headers = get_headers()

    data = {

        "messaging_product": "whatsapp",

        "to": to,

        "type": "interactive",

        "interactive": {

            "type": "button",

            "body": {

                "text":
                "🙏 Namaskaram!\n\n"
                "Sri Parvathi Jadala Ramalingeshwara Swamy Temple Assistant\n\n"
                "Please choose:"

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

    requests.post(GRAPH_URL, headers=headers, json=data)


# =====================================================
# BUTTON HANDLER
# =====================================================

def handle_button(button):

    if button == "timings":

        return (
            "🕉 Temple timings:\n\n"
            "Morning: 5:00 AM – 12:30 PM\n"
            "Evening: 3:00 PM – 7:00 PM"
        )

    if button == "location":

        return (
            "📍 Location:\n"
            "https://maps.google.com/?q=17.17491,79.21219"
        )

    if button == "register":

        return (
            "Type:\n"
            "register YourName Village\n\n"
            "Example:\n"
            "register Siddharth Nalgonda"
        )

    return "Please choose valid option."


# =====================================================
# REGISTER DEVOTEE
# =====================================================

def register_devotee(phone, text):

    parts = text.split()

    if len(parts) >= 3:

        name = parts[1]
        village = parts[2]

        devotees[phone] = {
            "name": name,
            "village": village
        }

        send_whatsapp(
            phone,
            f"🙏 Registration successful\n{name} from {village}"
        )

    else:

        send_whatsapp(
            phone,
            "Invalid format.\nUse:\nregister Name Village"
        )


# =====================================================
# GEMINI INTELLIGENCE
# =====================================================

def gemini_reply(user_message):

    try:

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

        data = {

            "contents": [

                {

                    "parts": [

                        {

                            "text":
                            "You are temple assistant of Sri Parvathi Jadala Ramalingeshwara Swamy Temple. "
                            "Reply in user's language (Telugu, Hindi, Tamil, Kannada, Malayalam, Marathi, Bengali, Gujarati, Punjabi, Urdu, English). "
                            "Be clear and helpful.\n\n"
                            f"User: {user_message}"

                        }

                    ]

                }

            ]

        }

        res = requests.post(url, json=data)

        result = res.json()

        print("Gemini response:", result)

        return result["candidates"][0]["content"]["parts"][0]["text"]

    except Exception as e:

        print("Gemini error:", e)

        return "Please try again."


# =====================================================
# GEMINI SPEECH TO TEXT (WORKS FOR ALL INDIAN LANGUAGES)
# =====================================================

def speech_to_text(media_id):

    try:

        print("Downloading audio")

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}"
        }

        media = requests.get(
            f"https://graph.facebook.com/v18.0/{media_id}",
            headers=headers
        ).json()

        audio_url = media["url"]

        audio_bytes = requests.get(
            audio_url,
            headers=headers
        ).content

        print("Audio size:", len(audio_bytes))

        audio_base64 = base64.b64encode(audio_bytes).decode()

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

        data = {

            "contents": [

                {

                    "parts": [

                        {
                            "text":
                            "Transcribe this audio exactly. Detect language automatically."
                        },

                        {
                            "inline_data": {
                                "mime_type": "audio/ogg",
                                "data": audio_base64
                            }
                        }

                    ]

                }

            ]

        }

        res = requests.post(url, json=data)

        result = res.json()

        print("Gemini STT:", result)

        transcript = result["candidates"][0]["content"]["parts"][0]["text"]

        return transcript

    except Exception as e:

        print("STT error:", e)

        return ""


# =====================================================
# SEND WHATSAPP MESSAGE
# =====================================================

def send_whatsapp(to, message):

    headers = get_headers()

    data = {

        "messaging_product": "whatsapp",

        "to": to,

        "type": "text",

        "text": {
            "body": message
        }

    }

    requests.post(GRAPH_URL, headers=headers, json=data)


# =====================================================
# HEADERS
# =====================================================

def get_headers():

    return {

        "Authorization": f"Bearer {WHATSAPP_TOKEN}",

        "Content-Type": "application/json"

    }


# =====================================================
# BROADCAST
# =====================================================

def broadcast(message):

    for phone in devotees:

        send_whatsapp(phone, message)
