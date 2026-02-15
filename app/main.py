from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient, ReturnDocument
from datetime import datetime
import requests
import os
import logging

# =====================================================
# APP INIT
# =====================================================

app = FastAPI()
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
    raise Exception("Missing environment variables")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# =====================================================
# DATABASE
# =====================================================

client = MongoClient(MONGODB_URI)
db = client["sohum_db"]

devotees = db["devotees"]
seva_bookings = db["seva_bookings"]
accommodation_bookings = db["accommodation_bookings"]
counters = db["counters"]
festival_config = db["festival_config"]

devotees.create_index("phone", unique=True)

# =====================================================
# SESSION STORES
# =====================================================

registration_sessions = {}
flow_sessions = {}

# =====================================================
# UTILITIES
# =====================================================

def normalize_phone(phone):
    phone = phone.replace("+", "")
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone

def headers():
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

def send_text(phone, message):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    requests.post(GRAPH_URL, headers=headers(), json=payload)

def send_buttons(phone, text, buttons):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {"buttons": buttons}
        }
    }
    requests.post(GRAPH_URL, headers=headers(), json=payload)

def btn(id, title):
    return {"type": "reply", "reply": {"id": id, "title": title}}

def generate_booking_id(prefix):
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M")
    counter = counters.find_one_and_update(
        {"_id": prefix},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return f"SPJRS-{prefix}-{timestamp}-{counter['seq']:04d}"

def get_user(phone):
    return devotees.find_one({"phone": phone})

def set_language(phone, lang):
    devotees.update_one(
        {"phone": phone},
        {"$set": {"phone": phone, "language": lang}},
        upsert=True
    )

# =====================================================
# HEALTH
# =====================================================

@app.api_route("/", methods=["GET", "HEAD"])
async def health():
    return {"status": "alive"}

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

        if msg_type == "text":
            text = message["text"]["body"].strip()
            return handle_text(sender, text)

        if msg_type == "interactive":
            selected_id = message["interactive"]["button_reply"]["id"]
            return handle_navigation(sender, selected_id)

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"status": "ok"}

# =====================================================
# LANGUAGE SELECTION
# =====================================================

def send_language_selection(phone):
    send_buttons(
        phone,
        "Om Namah Shivaya 🙏\nPlease choose your language:\nదయచేసి మీ భాషను ఎంచుకోండి:",
        [
            btn("lang_en", "English 🇬🇧"),
            btn("lang_tel", "తెలుగు 🇮🇳")
        ]
    )

# =====================================================
# MAIN MENU
# =====================================================

def send_main_menu(phone):

    user = get_user(phone)
    lang = user.get("language", "en") if user else "en"

    # Festival banner
    festival = festival_config.find_one({"active": True})
    if festival:
        send_text(phone, festival.get("message"))

    if lang == "tel":
        text = "మెయిన్ మెనూ ఎంపిక చేయండి:"
        buttons = [
            btn("darshan","🕉️ దర్శన సమయాలు"),
            btn("seva","🙏 పూజా బుకింగ్"),
            btn("accommodation","🏠 వసతి"),
        ]
    else:
        text = "Please choose a service:"
        buttons = [
            btn("darshan","🕉️ Darshan & Timings"),
            btn("seva","🙏 Seva Booking"),
            btn("accommodation","🏠 Accommodation"),
        ]

    send_buttons(phone, text, buttons)

# =====================================================
# TEXT HANDLER
# =====================================================

def handle_text(sender, text):

    lower = text.lower()

    if sender in registration_sessions:
        handle_registration(sender, text)
        return {"status":"registration"}

    if sender in flow_sessions:
        handle_booking_flow(sender, text)
        return {"status":"flow"}

    if lower in ["hi","hello","start","namaste"]:
        send_language_selection(sender)
        return {"status":"lang"}

    if lower == "menu":
        send_main_menu(sender)
        return {"status":"menu"}

    # AI fallback only unknown
    reply = gemini_reply(text)
    send_text(sender, reply)
    return {"status":"ai"}

# =====================================================
# NAVIGATION
# =====================================================

def handle_navigation(phone, selected_id):

    if selected_id == "lang_en":
        set_language(phone,"en")
        send_main_menu(phone)

    elif selected_id == "lang_tel":
        set_language(phone,"tel")
        send_main_menu(phone)

    elif selected_id == "darshan":
        send_text(phone,"Morning 06:00–12:30\nEvening 05:00–08:30\n\nType menu.")

    elif selected_id == "seva":
        start_seva_booking(phone)

    elif selected_id == "accommodation":
        start_accommodation_booking(phone)

    return {"status":"nav"}

# =====================================================
# SEVA BOOKING FLOW
# =====================================================

def start_seva_booking(phone):
    flow_sessions[phone] = {"flow":"seva","step":"seva_name"}
    send_text(phone,"Enter Seva Name:")

def handle_booking_flow(phone, text):

    session = flow_sessions[phone]

    if session["flow"] == "seva":

        if session["step"] == "seva_name":
            session["seva"] = text
            session["step"] = "date"
            send_text(phone,"Enter Booking Date (YYYY-MM-DD):")

        elif session["step"] == "date":
            session["date"] = text
            booking_id = generate_booking_id("SB")

            seva_bookings.insert_one({
                "booking_id": booking_id,
                "phone": phone,
                "seva": session["seva"],
                "booking_date": session["date"],
                "created_at": datetime.utcnow()
            })

            flow_sessions.pop(phone)
            send_text(phone,f"Seva booked successfully!\nBooking ID: {booking_id}")
            send_main_menu(phone)

# =====================================================
# ACCOMMODATION FLOW
# =====================================================

def start_accommodation_booking(phone):
    flow_sessions[phone] = {"flow":"acc","step":"room_type"}
    send_text(phone,"Enter Room Type (Non-AC / AC / Dormitory):")

# =====================================================
# 5-STEP REGISTRATION
# =====================================================

def start_registration(phone):

    existing = get_user(phone)
    if existing and existing.get("registration_status") == "active":
        send_text(phone,"You are already registered.")
        return

    registration_sessions[phone] = {"step":"name"}
    send_text(phone,"Enter Full Name:")

def handle_registration(phone, text):

    session = registration_sessions[phone]

    if session["step"] == "name":
        session["name"] = text
        session["step"] = "gotram"
        send_text(phone,"Enter Gotram:")
        return

    if session["step"] == "gotram":
        session["gotram"] = text
        session["step"] = "address1"
        send_text(phone,"Enter House No & Street:")
        return

    if session["step"] == "address1":
        session["address1"] = text
        session["step"] = "city"
        send_text(phone,"Enter City:")
        return

    if session["step"] == "city":
        session["city"] = text
        session["step"] = "state"
        send_text(phone,"Enter State:")
        return

    if session["step"] == "state":
        session["state"] = text
        session["step"] = "pincode"
        send_text(phone,"Enter Pincode:")
        return

    if session["step"] == "pincode":

        devotees.insert_one({
            "phone": phone,
            "full_name": session["name"],
            "gotram": session["gotram"],
            "address": {
                "line1": session["address1"],
                "city": session["city"],
                "state": session["state"],
                "pincode": text
            },
            "registration_status":"active",
            "registered_at": datetime.utcnow()
        })

        registration_sessions.pop(phone)
        send_text(phone,"Registration completed successfully.")
        send_main_menu(phone)

# =====================================================
# GEMINI FALLBACK
# =====================================================

def gemini_reply(text):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents":[{"parts":[{"text":f"You are temple assistant.\nUser: {text}"}]}]
        }
        r = requests.post(url, json=payload)
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except:
        return "Please type 'menu' to continue."
