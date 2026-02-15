from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient
from datetime import datetime
import requests
import os
import logging

# =====================================================
# APP INIT
# =====================================================

app = FastAPI()

# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TempleBot")

# =====================================================
# ENV CONFIG
# =====================================================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGODB_URI = os.getenv("MONGODB_URI")

if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID, GEMINI_API_KEY, MONGODB_URI]):
    raise Exception("One or more environment variables missing.")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# =====================================================
# DATABASE
# =====================================================

client = MongoClient(MONGODB_URI)
db = client["sohum_db"]
devotees = db["devotees"]

devotees.create_index("phone", unique=True)

# =====================================================
# SESSION STORES
# =====================================================

registration_sessions = {}
navigation_sessions = {}

# =====================================================
# HEALTH CHECK
# =====================================================

@app.get("/")
async def health():
    return {"status": "alive"}

# =====================================================
# ADMIN APIs
# =====================================================

@app.get("/admin/devotees")
async def list_devotees():
    return list(devotees.find({}, {"_id": 0}))

@app.get("/admin/devotee/{phone}")
async def get_devotee(phone: str):
    user = devotees.find_one({"phone": phone}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Devotee not found")
    return user

# =====================================================
# UTILITIES
# =====================================================

def normalize_phone(phone: str):
    phone = phone.strip().replace("+", "")
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone

def send_text(phone, message):
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    requests.post(GRAPH_URL, headers=get_headers(), json=data)

def send_buttons(phone, body_text, buttons):
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": buttons}
        }
    }
    requests.post(GRAPH_URL, headers=get_headers(), json=data)

def get_headers():
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

# =====================================================
# WEBHOOK VERIFY
# =====================================================

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.mode") == "subscribe" and \
       request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(request.query_params.get("hub.challenge"))
    return PlainTextResponse("Verification failed", status_code=403)

# =====================================================
# MAIN WEBHOOK
# =====================================================

@app.post("/webhook")
async def webhook(request: Request):

    try:
        data = await request.json()
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]

        sender = normalize_phone(message["from"])
        msg_type = message["type"]

        logger.info(f"Incoming from {sender} - {msg_type}")

        if msg_type == "text":
            text = message["text"]["body"].strip()
            return handle_text(sender, text)

        if msg_type == "interactive":
            interactive = message["interactive"]
            selected_id = None

            if interactive["type"] == "button_reply":
                selected_id = interactive["button_reply"]["id"]
            if interactive["type"] == "list_reply":
                selected_id = interactive["list_reply"]["id"]

            return handle_navigation(sender, selected_id)

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"status": "ok"}

# =====================================================
# TEXT HANDLER
# =====================================================

def handle_text(sender, text):

    text_lower = text.lower()

    if sender in registration_sessions:
        handle_registration(sender, text)
        return {"status": "registration flow"}

    if text_lower in ["hi", "hello", "start", "menu"]:
        send_main_menu(sender)
        return {"status": "menu"}

    if text_lower == "register":
        start_registration(sender)
        return {"status": "registration started"}

    # AI only unknown
    reply = gemini_reply(text)
    send_text(sender, reply)
    return {"status": "ai"}

# =====================================================
# MAIN MENU
# =====================================================

def send_main_menu(phone):

    navigation_sessions[phone] = "main"

    send_buttons(
        phone,
        "SPJRS Temple Services\n\nChoose an option:",
        [
            {"type": "reply", "reply": {"id": "register", "title": "Register Devotee"}},
            {"type": "reply", "reply": {"id": "info", "title": "Temple Info"}},
            {"type": "reply", "reply": {"id": "seva", "title": "Seva Services"}}
        ]
    )

# =====================================================
# NAVIGATION
# =====================================================

def handle_navigation(phone, selected_id):

    if selected_id == "register":
        start_registration(phone)

    elif selected_id == "info":
        send_buttons(
            phone,
            "Temple Information:",
            [
                {"type": "reply", "reply": {"id": "timings", "title": "Temple Timings"}},
                {"type": "reply", "reply": {"id": "location", "title": "Location"}},
                {"type": "reply", "reply": {"id": "go_back", "title": "↩ Go Back"}}
            ]
        )

    elif selected_id == "timings":
        send_text(phone, "🕉 Morning: 5:00–12:30\nEvening: 3:00–7:00")
        send_back_button(phone)

    elif selected_id == "location":
        send_text(phone, "📍 https://maps.google.com/?q=17.17491,79.21219")
        send_back_button(phone)

    elif selected_id == "seva":
        send_buttons(
            phone,
            "Seva Services:",
            [
                {"type": "reply", "reply": {"id": "archana", "title": "Archana"}},
                {"type": "reply", "reply": {"id": "abhishekam", "title": "Abhishekam"}},
                {"type": "reply", "reply": {"id": "go_back", "title": "↩ Go Back"}}
            ]
        )

    elif selected_id == "go_back":
        send_main_menu(phone)

    elif selected_id == "confirm_registration":
        confirm_registration(phone)

    elif selected_id == "cancel_registration":
        registration_sessions.pop(phone, None)
        send_text(phone, "Registration cancelled.")
        send_main_menu(phone)

    return {"status": "navigation"}

def send_back_button(phone):
    send_buttons(
        phone,
        "Choose next action:",
        [{"type": "reply", "reply": {"id": "go_back", "title": "↩ Go Back"}}]
    )

# =====================================================
# REGISTRATION (5 STEP)
# =====================================================

def start_registration(phone):

    existing = devotees.find_one({"phone": phone})

    if existing and existing.get("registration_status") == "active":
        send_text(phone, "🙏 You are already registered.")
        send_main_menu(phone)
        return

    registration_sessions[phone] = {"step": "name"}
    send_text(phone, "🙏 Please enter your Full Name:")

def handle_registration(phone, text):

    session = registration_sessions[phone]
    step = session["step"]

    if step == "name":
        session["full_name"] = text
        session["step"] = "gotram"
        send_text(phone, "Enter Gotram (or type 'no'):")
        return

    if step == "gotram":
        session["gotram"] = text if text.lower() != "no" else "Not Provided"
        session["step"] = "address"
        send_text(phone, "Enter Address (or type 'no'):")
        return

    if step == "address":
        session["address"] = text if text.lower() != "no" else "Not Provided"
        session["step"] = "mobile"
        send_text(phone, "Enter Mobile Number:")
        return

    if step == "mobile":
        session["mobile"] = text
        session["step"] = "email"
        send_text(phone, "Enter Email (or type 'no'):")
        return

    if step == "email":
        session["email"] = text if text.lower() != "no" else "Not Provided"
        session["step"] = "confirm"

        send_buttons(
            phone,
            f"Confirm registration for {session['full_name']}?",
            [
                {"type": "reply", "reply": {"id": "confirm_registration", "title": "✅ Confirm"}},
                {"type": "reply", "reply": {"id": "cancel_registration", "title": "❌ Cancel"}}
            ]
        )

def confirm_registration(phone):

    session = registration_sessions.get(phone)
    if not session:
        send_text(phone, "Session expired.")
        send_main_menu(phone)
        return

    devotees.update_one(
        {"phone": phone},
        {
            "$set": {
                "phone": phone,
                "full_name": session["full_name"],
                "gotram": session["gotram"],
                "address": session["address"],
                "mobile_number": session["mobile"],
                "email": session["email"],
                "registration_status": "active",
                "registered_at": datetime.utcnow()
            }
        },
        upsert=True
    )

    registration_sessions.pop(phone, None)
    send_text(phone, "🙏 Registration successful!")
    send_main_menu(phone)

# =====================================================
# GEMINI AI
# =====================================================

def gemini_reply(text):

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
        data = {"contents":[{"parts":[{"text":f"You are temple assistant.\nUser: {text}"}]}]}
        response = requests.post(url, json=data)
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except:
        return "🙏 Please try again."