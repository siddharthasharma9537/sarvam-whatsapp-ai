from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient
from datetime import datetime
import requests
import os

app = FastAPI()

# =====================================================
# HEALTH CHECK
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
MONGODB_URI = os.getenv("MONGODB_URI")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

if not MONGODB_URI:
    raise Exception("MONGODB_URI not set")


# =====================================================
# DATABASE
# =====================================================

client = MongoClient(MONGODB_URI)
db = client["sohum_db"]
devotees_collection = db["devotees"]

try:
    devotees_collection.create_index("phone", unique=True)
except:
    pass


# =====================================================
# TEMP STORES
# =====================================================

registration_sessions = {}
menu_sessions = {}


# =====================================================
# PHONE NORMALIZER
# =====================================================

def normalize_phone(phone: str):
    phone = phone.strip().replace("+", "")
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone


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

    try:
        entry = data.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})

        if "messages" not in value:
            return {"status": "no message"}

        message_obj = value["messages"][0]
        sender = normalize_phone(message_obj.get("from"))
        msg_type = message_obj.get("type")

        # =========================
        # TEXT MESSAGE
        # =========================

        if msg_type == "text":

            text = message_obj["text"]["body"].strip()
            text_lower = text.lower()

            # REGISTRATION FLOW
            if sender in registration_sessions:
                handle_registration(sender, text)
                return {"status": "registration handled"}

            # GREETING
            if text_lower in ["hi", "hello", "namaskaram", "start", "menu"]:
                send_main_menu(sender)
                return {"status": "main menu shown"}

            # REGISTER COMMAND
            if text_lower == "register":
                existing = devotees_collection.find_one({"phone": sender})
                if existing and existing.get("registration_status") == "active":
                    send_text(sender, "🙏 You are already registered.")
                else:
                    start_registration(sender)
                return {"status": "register handled"}

            # UNKNOWN → AI FALLBACK
            reply = gemini_reply(text)
            send_text(sender, reply)

        # =========================
        # INTERACTIVE
        # =========================

        elif msg_type == "interactive":

            interactive = message_obj.get("interactive", {})
            selected_id = None

            if interactive.get("type") == "list_reply":
                selected_id = interactive["list_reply"]["id"]

            elif interactive.get("type") == "button_reply":
                selected_id = interactive["button_reply"]["id"]

            handle_menu_selection(sender, selected_id)

        # =========================
        # AUDIO
        # =========================

        elif msg_type == "audio":

            media_id = message_obj["audio"]["id"]
            transcript = speech_to_text(media_id)

            if transcript:
                reply = gemini_reply(transcript)
            else:
                reply = "🙏 Sorry, I could not understand the voice message."

            send_text(sender, reply)

    except Exception as e:
        print("Webhook error:", e)

    return {"status": "ok"}


# =====================================================
# STRUCTURED NAVIGATION
# =====================================================

def send_main_menu(phone):

    menu_sessions[phone] = {"level": "main"}

    rows = [
        {
            "id": "l1_temple",
            "title": "Temple Services",
            "description": "Darshan Timings, Sevas, Events"
        },
        {
            "id": "l1_devotee",
            "title": "Devotee Services",
            "description": "Register, Donations, My Details"
        }
    ]

    send_list(phone, "☰ Main Menu", "Please choose a service:", rows)


def send_level2_menu(phone, category):

    menu_sessions[phone] = {"level": "level2", "parent": category}

    if category == "l1_temple":
        rows = [
            {"id": "timings", "title": "Temple Timings", "description": "Daily Darshan Schedule"},
            {"id": "location", "title": "Temple Location", "description": "Google Maps Location"},
        ]

    elif category == "l1_devotee":
        rows = [
            {"id": "register", "title": "Register Devotee", "description": "Join Temple Records"},
            {"id": "donate", "title": "Donate", "description": "Support Temple Activities"},
        ]

    # Always add navigation rows
    rows.append({"id": "go_back", "title": "↩ Go Back", "description": "Return to previous menu"})
    rows.append({"id": "main_menu", "title": "☰ Main Menu", "description": "Return to main menu"})

    send_list(phone, "Temple Services", "Select an option:", rows)


def handle_menu_selection(phone, selected_id):

    if not selected_id:
        return

    if selected_id == "main_menu":
        send_main_menu(phone)
        return

    if selected_id == "go_back":
        send_main_menu(phone)
        return

    if selected_id.startswith("l1_"):
        send_level2_menu(phone, selected_id)
        return

    # LEVEL 2 ACTIONS
    if selected_id == "timings":
        send_text(phone, "🕉 Morning: 5:00–12:30\nEvening: 3:00–7:00")
        return

    if selected_id == "location":
        send_text(phone, "📍 https://maps.google.com/?q=17.17491,79.21219")
        return

    if selected_id == "register":
        start_registration(phone)
        return

    if selected_id == "donate":
        send_text(phone, "🙏 Donation link coming soon.")
        return


# =====================================================
# REGISTRATION FLOW
# =====================================================

def start_registration(phone):
    registration_sessions[phone] = {"step": "name"}
    send_text(phone, "🙏 Please enter your Full Name:")


def handle_registration(phone, text):

    user = registration_sessions.get(phone)
    step = user["step"]

    if step == "name":
        user["full_name"] = text
        user["step"] = "confirm"
        send_text(phone, f"Confirm registration for {text}? Reply YES to confirm.")
        return

    if step == "confirm":
        if text.lower() == "yes":
            try:
                devotees_collection.update_one(
                    {"phone": phone},
                    {"$set": {
                        "phone": phone,
                        "full_name": user["full_name"],
                        "registration_status": "active",
                        "registered_at": datetime.utcnow()
                    }},
                    upsert=True
                )
                send_text(phone, "🙏 Registration successful!")
            except:
                send_text(phone, "🙏 Already registered.")
        else:
            send_text(phone, "Registration cancelled.")

        registration_sessions.pop(phone, None)


# =====================================================
# LIST SENDER
# =====================================================

def send_list(to, header, body, rows):

    headers = get_headers()

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "action": {
                "button": "View Options",
                "sections": [
                    {
                        "title": "Services",
                        "rows": rows
                    }
                ]
            }
        }
    }

    requests.post(GRAPH_URL, headers=headers, json=data)


# =====================================================
# TEXT SENDER
# =====================================================

def send_text(to, message):
    headers = get_headers()

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    requests.post(GRAPH_URL, headers=headers, json=data)


# =====================================================
# GEMINI AI
# =====================================================

def gemini_reply(user_message):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"

        data = {
            "contents": [{
                "parts": [{"text": f"You are a temple assistant.\nUser: {user_message}"}]
            }]
        }

        response = requests.post(url, json=data)
        result = response.json()

        if "candidates" in result:
            return result["candidates"][0]["content"]["parts"][0]["text"]

        return "🙏 Please try again."

    except:
        return "🙏 Please try again."


# =====================================================
# SPEECH TO TEXT
# =====================================================

def speech_to_text(media_id):
    try:
        whatsapp_headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

        media = requests.get(
            f"https://graph.facebook.com/v18.0/{media_id}",
            headers=whatsapp_headers
        ).json()

        audio_url = media["url"]

        audio_data = requests.get(
            audio_url,
            headers=whatsapp_headers
        ).content

        stt_headers = {"api-subscription-key": SARVAM_API_KEY}

        files = {"file": ("audio.ogg", audio_data, "audio/ogg")}

        response = requests.post(
            "https://api.sarvam.ai/v1/speech-to-text",
            headers=stt_headers,
            files=files
        )

        if response.status_code == 200:
            return response.json().get("text", "")

        return ""

    except:
        return ""


# =====================================================
# HEADERS
# =====================================================

def get_headers():
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
