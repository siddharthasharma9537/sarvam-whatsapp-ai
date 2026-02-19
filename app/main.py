from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient
from datetime import datetime, date
import requests
import os
import logging
import razorpay
import hmac
import hashlib
import json

# =====================================================
# APP INIT
# =====================================================

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TempleBot")

# =====================================================
# ENV VARIABLES
# =====================================================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
MONGODB_URI = os.getenv("MONGODB_URI")

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID, MONGODB_URI]):
    raise Exception("Missing required environment variables")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

HISTORY_IMAGE_EN = "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20ENG%20(1).PNG"
HISTORY_IMAGE_TEL = "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20TEL%20(1).PNG"

# =====================================================
# DATABASE
# =====================================================

client = MongoClient(MONGODB_URI)
db = client["sohum_db"]

devotees = db["devotees"]
bookings = db["bookings"]

devotees.create_index("phone", unique=True)
bookings.create_index("booking_id", unique=True)

# =====================================================
# LOAD SPECIAL DAYS (2026)
# =====================================================

SPECIAL_DAYS = []

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    FILE_PATH = os.path.join(BASE_DIR, "data", "special_days_2026.json")

    with open(FILE_PATH, "r", encoding="utf-8") as f:
        SPECIAL_DAYS = json.load(f)

    logger.info("Special days 2026 dataset loaded successfully.")

except Exception as e:
    logger.error(f"Dataset load failed: {e}")

# =====================================================
# RAZORPAY INIT
# =====================================================

razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    )

# =====================================================
# SESSION STORES
# =====================================================

language_sessions = {}
registration_sessions = {}

# =====================================================
# UTILITIES
# =====================================================

def normalize_phone(phone):
    phone = phone.replace("+", "")
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone


def whatsapp_request(payload):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(GRAPH_URL, headers=headers, json=payload)

    logger.info(f"WhatsApp Status: {response.status_code}")
    logger.info(f"WhatsApp Response: {response.text}")

    return response


def send_text(phone, message):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    whatsapp_request(payload)


def send_list(phone, text, rows):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": text},
            "action": {
                "button": "Select Option",
                "sections": [{
                    "title": "Temple Services",
                    "rows": rows
                }]
            }
        }
    }
    whatsapp_request(payload)


def send_image(phone, image_url, caption):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {
            "link": image_url,
            "caption": caption
        }
    }
    whatsapp_request(payload)

# =====================================================
# TITHI ENGINE
# =====================================================

def get_next_tithi(tithi_type):
    if not SPECIAL_DAYS:
        return None

    today = date.today()
    upcoming = []

    for event in SPECIAL_DAYS:

        if event.get("tithi_type") != tithi_type:
            continue

        try:
            event_date = datetime.strptime(
                event["date_iso"], "%Y-%m-%d"
            ).date()
        except Exception:
            continue

        if event_date >= today:
            upcoming.append((event_date, event))

    if not upcoming:
        return None

    upcoming.sort(key=lambda x: x[0])
    return upcoming[0][1]

# =====================================================
# HEALTH
# =====================================================

@app.get("/")
async def health():
    return {"status": "alive"}

# =====================================================
# VERIFY
# =====================================================

@app.get("/webhook")
async def verify(request: Request):
    if (
        request.query_params.get("hub.mode") == "subscribe"
        and request.query_params.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return PlainTextResponse(request.query_params.get("hub.challenge"))

    return PlainTextResponse("Verification failed", status_code=403)

# =====================================================
# MAIN WEBHOOK
# =====================================================

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    logger.info(f"WEBHOOK RECEIVED: {data}")

    try:
        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"status": "no message"}

        message = value["messages"][0]
        sender = normalize_phone(message["from"])

        if message["type"] == "text":
            return handle_text(sender, message["text"]["body"])

        if message["type"] == "interactive":
            selected = message["interactive"]["list_reply"]["id"]
            return handle_navigation(sender, selected)

    except Exception:
        logger.exception("Webhook processing error")

    return {"status": "ok"}

# =====================================================
# TEXT HANDLER
# =====================================================

def handle_text(sender, text):

    if sender in registration_sessions:
        return handle_registration(sender, text)

    lower = text.strip().lower()

    if lower in ["hi", "hello", "namaste", "start"]:
        send_main_menu(sender)
        return {"status": "menu"}

    if lower in ["menu", "main menu"]:
        send_main_menu(sender)
        return {"status": "menu"}

    send_text(sender, "Please use menu options.")
    return {"status": "unknown"}

# =====================================================
# MENU
# =====================================================

def send_language_selection(phone):
    send_list(
        phone,
        "Choose Language:",
        [
            {"id": "lang_en", "title": "English 🇬🇧"},
            {"id": "lang_tel", "title": "తెలుగు 🇮🇳"}
        ]
    )


def send_main_menu(phone):
    lang = language_sessions.get(phone, "en")

    if lang == "tel":
        send_list(
            phone,
            "ప్రధాన మెను:",
            [
                {"id": "register", "title": "📝 భక్తుడు నమోదు"},
                {"id": "history", "title": "📜 స్థలపురాణం"},
                {"id": "next_tithi", "title": "🌕 తదుపరి తిథి"},
                {"id": "change_lang", "title": "🌐 భాష మార్చండి"}
            ]
        )
    else:
        send_list(
            phone,
            "Main Menu:",
            [
                {"id": "register", "title": "📝 Register Devotee"},
                {"id": "history", "title": "📜 History"},
                {"id": "next_tithi", "title": "🌕 Know Next Tithi"},
                {"id": "change_lang", "title": "🌐 Change Language"}
            ]
        )

# =====================================================
# NAVIGATION
# =====================================================

def handle_navigation(phone, selected):

    if selected == "lang_en":
        language_sessions[phone] = "en"
        send_main_menu(phone)
        return {"status": "lang_en"}

    if selected == "lang_tel":
        language_sessions[phone] = "tel"
        send_main_menu(phone)
        return {"status": "lang_tel"}

    if selected == "change_lang":
        send_language_selection(phone)
        return {"status": "change_lang"}

    if selected == "next_tithi":

        amavasya = get_next_tithi("amavasya")
        pournami = get_next_tithi("pournami")

        if not amavasya and not pournami:
            send_text(phone, "No upcoming tithis found.")
            send_main_menu(phone)
            return {"status": "no_tithi"}

        message = ""

        if amavasya:
            message += f"🌑 Next Amavasya:\n{amavasya['date_iso']}\n\n"

        if pournami:
            message += f"🌕 Next Pournami:\n{pournami['date_iso']}"

        send_text(phone, message.strip())
        send_main_menu(phone)

        return {"status": "tithi_sent"}

    if selected == "history":
        lang = language_sessions.get(phone, "en")

        if lang == "tel":
            send_image(phone, HISTORY_IMAGE_TEL, "స్థలపురాణము")
        else:
            send_image(phone, HISTORY_IMAGE_EN, "Temple History")

        send_main_menu(phone)
        return {"status": "history"}

    if selected == "register":
        return start_registration(phone)

    send_text(phone, "Invalid option selected.")
    send_main_menu(phone)
    return {"status": "unknown"}

# =====================================================
# REGISTRATION FLOW
# =====================================================

def start_registration(phone):

    if devotees.find_one({"phone": phone}):
        send_text(phone, "🙏 You are already registered.")
        send_main_menu(phone)
        return {"status": "already_registered"}

    registration_sessions[phone] = {"step": "name", "data": {}}
    send_text(phone, "📝 Enter Full Name:\n(Type 'cancel' anytime to stop)")
    return {"status": "registration_started"}


def handle_registration(phone, text):

    if text.lower() == "cancel":
        registration_sessions.pop(phone, None)
        send_text(phone, "Registration cancelled.")
        send_main_menu(phone)
        return {"status": "cancelled"}

    session = registration_sessions.get(phone)
    if not session:
        return {"status": "no_session"}

    step = session["step"]
    data = session["data"]

    if step == "name":
        data["name"] = text
        session["step"] = "gotram"
        send_text(phone, "Enter Gotram (or type no):")
        return

    if step == "gotram":
        data["gotram"] = text if text.lower() != "no" else "Not Provided"
        session["step"] = "address"
        send_text(phone, "Enter Address:")
        return

    if step == "address":
        data["address"] = text
        session["step"] = "mobile"
        send_text(phone, "Enter Mobile:")
        return

    if step == "mobile":
        data["mobile"] = text
        session["step"] = "email"
        send_text(phone, "Enter Email (or type no):")
        return

    if step == "email":
        data["email"] = text if text.lower() != "no" else "Not Provided"

        devotees.insert_one({
            "phone": phone,
            "full_name": data["name"],
            "gotram": data["gotram"],
            "address": data["address"],
            "mobile": data["mobile"],
            "email": data["email"],
            "registered_at": datetime.utcnow()
        })

        registration_sessions.pop(phone, None)

        send_text(phone, "🎉 Registration Successful!\nMay Lord Shiva bless you 🙏")
        send_main_menu(phone)

        return {"status": "registered"}
