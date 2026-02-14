from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests
import os

app = FastAPI()

# =====================================================
# HEALTH CHECK (Render + UptimeRobot)
# =====================================================

@app.get("/")
async def health_check():
    return {"status": "alive"}


# =====================================================
# CONFIG
# =====================================================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# Temporary in-memory storage
devotees = {}


# =====================================================
# WEBHOOK VERIFICATION
# =====================================================

@app.get("/webhook")
async def verify(request: Request):

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)


# =====================================================
# MAIN WEBHOOK
# =====================================================

@app.post("/webhook")
async def webhook(request: Request):

    data = await request.json()
    print("Incoming:", data)

    try:
        entry = data.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})

        if "messages" not in value:
            return {"status": "no message"}

        message_obj = value["messages"][0]

        sender = message_obj.get("from")
        msg_type = message_obj.get("type")

        print("Sender:", sender)
        print("Type:", msg_type)

        # =====================================================
        # TEXT MESSAGE
        # =====================================================

        if msg_type == "text":

            text = message_obj["text"]["body"]
            text_lower = text.lower()

            # If user is in registration flow
            if sender in devotees and devotees[sender].get("step") != "completed":
                handle_registration(sender, text)
                return {"status": "registration step handled"}

            if text_lower in ["hi", "hello", "namaskaram", "menu", "start"]:
                send_menu(sender)
                return {"status": "menu sent"}

            if text_lower == "register":
                start_registration(sender)
                return {"status": "registration started"}

            reply = gemini_reply(text)
            send_whatsapp(sender, reply)

        # =====================================================
        # INTERACTIVE (LIST OR BUTTON)
        # =====================================================

        elif msg_type == "interactive":

            interactive = message_obj.get("interactive", {})
            interactive_type = interactive.get("type")

            selected_id = None

            if interactive_type == "list_reply":
                selected_id = interactive.get("list_reply", {}).get("id")

            elif interactive_type == "button_reply":
                selected_id = interactive.get("button_reply", {}).get("id")

            print("Selected ID:", selected_id)

            if selected_id == "register":
                start_registration(sender)
            elif selected_id:
                reply = handle_button(selected_id)
                send_whatsapp(sender, reply)
            else:
                send_whatsapp(sender, "Please try again.")

        # =====================================================
        # AUDIO MESSAGE
        # =====================================================

        elif msg_type == "audio":

            media_id = message_obj["audio"]["id"]
            transcript = speech_to_text(media_id)

            if transcript:
                reply = gemini_reply(transcript)
            else:
                reply = "🙏 Sorry, I could not understand the voice message."

            send_whatsapp(sender, reply)

    except Exception as e:
        print("Webhook error:", e)

    return {"status": "ok"}


# =====================================================
# DEVOTEE REGISTRATION FLOW
# =====================================================

def start_registration(phone):

    devotees[phone] = {
        "step": "name"
    }

    send_whatsapp(
        phone,
        """🙏 Devotee Registration

We will collect:

1. Full Name
2. Gotram
3. Address
4. Mobile Number
5. Email (optional)

If you do not have any detail, type: no

Please enter your Full Name:"""
    )


def handle_registration(phone, text):

    user = devotees.get(phone)
    step = user.get("step")

    if step == "name":
        user["name"] = text
        user["step"] = "gotram"
        send_whatsapp(phone, "Please enter your Gotram (or type no):")
        return

    if step == "gotram":
        user["gotram"] = text if text.lower() != "no" else "Not Provided"
        user["step"] = "address"
        send_whatsapp(phone, "Please enter your Address (or type no):")
        return

    if step == "address":
        user["address"] = text if text.lower() != "no" else "Not Provided"
        user["step"] = "mobile"
        send_whatsapp(phone, "Please enter your Mobile Number:")
        return

    if step == "mobile":
        user["mobile"] = text
        user["step"] = "email"
        send_whatsapp(phone, "Please enter your Email (or type no):")
        return

    if step == "email":
        user["email"] = text if text.lower() != "no" else "Not Provided"
        user["step"] = "confirm"

        send_whatsapp(
            phone,
            f"""🙏 Please confirm your details:

Full Name: {user['name']}
Gotram: {user['gotram']}
Address: {user['address']}
Mobile: {user['mobile']}
Email: {user['email']}

Type YES to submit
Type NO to cancel"""
        )
        return

    if step == "confirm":

        if text.lower() == "yes":
            user["phone"] = phone
            user["step"] = "completed"

            print("Registered devotee:", user)

            send_whatsapp(
                phone,
                "🙏 Registration successful! Thank you for registering with the Temple."
            )
        else:
            devotees.pop(phone, None)
            send_whatsapp(
                phone,
                "Registration cancelled. Type register to start again."
            )


# =====================================================
# MENU
# =====================================================

def send_menu(to):

    headers = get_headers()

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": "Sri Parvathi Jadala Ramalingeshwara Swamy"
            },
            "body": {
                "text": "🙏 Namaskaram!\nPlease select a service:"
            },
            "footer": {
                "text": "Temple Assistant"
            },
            "action": {
                "button": "View Menu",
                "sections": [
                    {
                        "title": "Temple Information",
                        "rows": [
                            {"id": "timings", "title": "Temple Timings"},
                            {"id": "location", "title": "Temple Location"}
                        ]
                    },
                    {
                        "title": "Devotee Services",
                        "rows": [
                            {"id": "register", "title": "Register Devotee"}
                        ]
                    },
                    {
                        "title": "Spiritual",
                        "rows": [
                            {"id": "ask", "title": "Ask Temple Assistant"}
                        ]
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
        return "📍 Location:\nhttps://maps.google.com/?q=17.17491,79.21219"

    if button == "ask":
        return "🙏 Please type your spiritual question."

    return "Please choose a valid option."


# =====================================================
# GEMINI AI
# =====================================================

def gemini_reply(user_message):

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"

        headers = {"Content-Type": "application/json"}

        data = {
            "contents": [{
                "parts": [{
                    "text": f"""
You are official assistant of Sri Parvathi Jadala Ramalingeshwara Swamy Temple.

Reply politely and devotionally.
Reply in the same language as the user.

User message:
{user_message}
"""
                }]
            }]
        }

        response = requests.post(url, headers=headers, json=data)
        result = response.json()

        if "candidates" in result:
            return result["candidates"][0]["content"]["parts"][0]["text"]

        return "🙏 Please try again."

    except Exception as e:
        print("Gemini error:", e)
        return "🙏 Please try again."


# =====================================================
# SPEECH TO TEXT
# =====================================================

def speech_to_text(media_id):

    try:
        whatsapp_headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}"
        }

        media = requests.get(
            f"https://graph.facebook.com/v18.0/{media_id}",
            headers=whatsapp_headers
        ).json()

        audio_url = media["url"]

        audio_data = requests.get(
            audio_url,
            headers=whatsapp_headers
        ).content

        stt_headers = {
            "api-subscription-key": SARVAM_API_KEY
        }

        files = {
            "file": ("audio.ogg", audio_data, "audio/ogg")
        }

        response = requests.post(
            "https://api.sarvam.ai/v1/speech-to-text",
            headers=stt_headers,
            files=files
        )

        if response.status_code == 200:
            return response.json().get("text", "")

        return ""

    except Exception as e:
        print("STT error:", e)
        return ""


# =====================================================
# SEND WHATSAPP
# =====================================================

def send_whatsapp(to, message):

    headers = get_headers()

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    requests.post(GRAPH_URL, headers=headers, json=data)


def get_headers():
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
