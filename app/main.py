from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient, ReturnDocument
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
MONGODB_URI = os.getenv("MONGODB_URI")

HISTORY_IMAGE_EN = "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20ENG%20(1).PNG"
HISTORY_IMAGE_TEL = "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20TEL%20(1).PNG"

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID, MONGODB_URI]):
    raise Exception("Missing core environment variables")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

# =====================================================
# DATABASE
# =====================================================

client = MongoClient(MONGODB_URI)
db = client["sohum_db"]

devotees = db["devotees"]
bookings = db["bookings"]
counters = db["counters"]

if "phone_1" not in devotees.index_information():
    devotees.create_index("phone", unique=True)

if "booking_id_1" not in bookings.index_information():
    bookings.create_index("booking_id", unique=True)

# =====================================================
# LOAD SPECIAL DAYS DATASET
# =====================================================

try:
    with open("app/data/special_days.json", "r", encoding="utf-8") as f:
        SPECIAL_DAYS = json.load(f)
    logger.info("Special days dataset loaded.")
except Exception as e:
    logger.error(f"Dataset load failed: {e}")
    SPECIAL_DAYS = []

# =====================================================
# RAZORPAY INIT
# =====================================================

razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

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


def send_text(phone, message):
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json=data
    )


def send_list(phone, text, rows):
    data = {
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
    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json=data
    )


def send_image(phone, image_url, caption):
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {
            "link": image_url,
            "caption": caption
        }
    }
    requests.post(
        GRAPH_URL,
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        json=data
    )

# =====================================================
# TITHI SEARCH ENGINE
# =====================================================

def get_next_tithi(keyword):
    today = date.today()
    upcoming = []

    for event in SPECIAL_DAYS:
        name_en = event.get("event_english", "").lower()
        name_tel = event.get("event_telugu", "").lower()

        if keyword in name_en or keyword in name_tel:
            try:
                event_date = date(
                    today.year,
                    event["month_number"],
                    event["date"]
                )
                if event_date >= today:
                    upcoming.append((event_date, event))
            except:
                continue

    if not upcoming:
        return None

    upcoming.sort(key=lambda x: x[0])
    return upcoming[0][1]

# =====================================================
# GEMINI FALLBACK
# =====================================================

def gemini_reply(phone, user_message):
    if not GEMINI_API_KEY:
        return None

    try:
        lang = language_sessions.get(phone, "en")
        instruction = "Reply in Telugu." if lang == "tel" else "Reply in English."

        url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

        payload = {
            "contents": [{
                "parts": [{
                    "text": f"""
You are assistant of Sri Parvathi Jadala Ramalingeshwara Swamy Temple.
Be devotional and temple-specific.
{instruction}

User:
{user_message}
"""
                }]
            }]
        }

        response = requests.post(url, json=payload)
        result = response.json()

        if "candidates" in result:
            return result["candidates"][0]["content"]["parts"][0]["text"]

        return None

    except:
        return None

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

    try:
        value = data["entry"][0]["changes"][0]["value"]
        if "messages" not in value:
            return {"status": "no message"}

        message = value["messages"][0]
        sender = normalize_phone(message["from"])

        if message["type"] == "text":
            return handle_text(sender, message["text"]["body"].strip())

        if message["type"] == "interactive":
            selected = message["interactive"]["list_reply"]["id"]
            return handle_navigation(sender, selected)

    except Exception as e:
        logger.error(e)

    return {"status": "ok"}

# =====================================================
# TEXT HANDLER
# =====================================================

def handle_text(sender, text):

    lower = text.lower()

    # -------------------------------------------------
    # 1️⃣ REGISTRATION FLOW (ALWAYS FIRST)
    # -------------------------------------------------
    if sender in registration_sessions:
        return handle_registration(sender, text)

    # -------------------------------------------------
    # MENU COMMAND
    # -------------------------------------------------
    if lower in ["menu", "main menu", "మెను", "ప్రధాన మెను"]:
        send_main_menu(sender)
        return {"status": "menu"}

    # -------------------------------------------------
    # NEXT AMAVASYA
    # -------------------------------------------------
    if "amavasya" in lower or "అమావాస్య" in lower:
        result = get_next_tithi("amavasya")
        if result:
            message = f"🌑 Next Amavasya:\n📅 {result['date']} {result['month']} {date.today().year}"
            send_text(sender, message)
        else:
            send_text(sender, "No upcoming Amavasya found.")
        return {"status": "amavasya"}

    # -------------------------------------------------
    # NEXT POURNAMI
    # -------------------------------------------------
    if "pournami" in lower or "పౌర్ణమి" in lower:
        result = get_next_tithi("pournami")
        if result:
            message = f"🌕 Next Pournami:\n📅 {result['date']} {result['month']} {date.today().year}"
            send_text(sender, message)
        else:
            send_text(sender, "No upcoming Pournami found.")
        return {"status": "pournami"}
    ai = gemini_reply(sender, text)
    if ai:
        send_text(sender, ai)
        return {"status": "ai"}

    send_text(sender, "Please use menu options.")
    return {"status": "unknown"}

# =====================================================
# MENU
# =====================================================

def send_language_selection(phone):
    send_list(
        phone,
        "Om Namah Shivaya 🙏 Choose Language:",
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
                {"id": "history", "title": "📜 స్థలపురాణం"},
                {"id": "next_tithi", "title": "🌕 తదుపరి పౌర్ణమి / అమావాస్య"},
                {"id": "change_lang", "title": "🌐 భాష మార్చండి"}
            ]
        )
    else:
        send_list(
            phone,
            "Main Menu:",
            [
                {"id": "history", "title": "📜 History"},
                {"id": "next_tithi", "title": "🌕 Know Next Pournami / Amavasya"},
                {"id": "change_lang", "title": "🌐 Change Language"}
            ]
        )


def handle_navigation(phone, selected):
    if selected == "lang_en":
        language_sessions[phone] = "en"
        send_main_menu(phone)
        return

    if selected == "lang_tel":
        language_sessions[phone] = "tel"
        send_main_menu(phone)
        return

    if selected == "next_tithi":
        amavasya = get_next_tithi("amavasya")
        pournami = get_next_tithi("pournami")

        message = ""
        if amavasya:
            message += f"Next Amavasya: {amavasya['date']}-{amavasya['month']}\n"
        if pournami:
            message += f"Next Pournami: {pournami['date']}-{pournami['month']}"

        send_text(phone, message)
        send_main_menu(phone)
        return

    if selected == "history":
        lang = language_sessions.get(phone, "en")
        if lang == "tel":
            send_image(phone, HISTORY_IMAGE_TEL, "స్థలపురాణము")
        else:
            send_image(phone, HISTORY_IMAGE_EN, "Temple History")
        send_main_menu(phone)
        return
        
# =====================================================
# REGISTRATION FLOW (UNCHANGED)
# =====================================================

def start_registration(phone):

    if devotees.find_one({"phone": phone}):
        send_text(phone, "🙏 You are already registered.")
        send_main_menu(phone)
        return

    registration_sessions[phone] = {"step":"name","data":{}}
    send_text(phone, "Enter Full Name:")


def handle_registration(phone, text):

    session = registration_sessions[phone]
    step = session["step"]
    data = session["data"]

    if step == "name":
        data["name"] = text
        session["step"] = "gotram"
        send_text(phone, "Enter Gotram (or type no):")
        return

    if step == "gotram":
        data["gotram"] = text if text.lower()!="no" else "Not Provided"
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
        data["email"] = text if text.lower()!="no" else "Not Provided"

        devotees.insert_one({
            "phone": phone,
            "full_name": data["name"],
            "gotram": data["gotram"],
            "address": data["address"],
            "mobile": data["mobile"],
            "email": data["email"],
            "registered_at": datetime.utcnow()
        })

        registration_sessions.pop(phone)

        send_text(phone, "🎉 Registration Successful!")
        send_main_menu(phone)
        return


# =====================================================
# RAZORPAY WEBHOOK (UNCHANGED)
# =====================================================

@app.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):

    if not RAZORPAY_WEBHOOK_SECRET:
        return {"status":"disabled"}

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400)

    payload = await request.json()

    if payload["event"] == "payment_link.paid":
        booking_id = payload["payload"]["payment_link"]["entity"]["reference_id"]
        bookings.update_one(
            {"booking_id": booking_id},
            {"$set": {"status": "paid"}}
        )

    return {"status":"ok"}
