from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient, ReturnDocument
from datetime import datetime
import requests
import os
import logging
import razorpay
import hmac
import hashlib

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

devotees.create_index("phone", unique=True)
bookings.create_index("booking_id", unique=True)

# =====================================================
# RAZORPAY SAFE INIT
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
flow_sessions = {}

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
    requests.post(GRAPH_URL, headers={
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }, json=data)


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
                    "title": "Services",
                    "rows": rows
                }]
            }
        }
    }
    requests.post(GRAPH_URL, headers={
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }, json=data)


def generate_booking_id(prefix):
    counter = counters.find_one_and_update(
        {"_id": prefix},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return f"SPJRSD-{prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M')}-{counter['seq']:04d}"


# =====================================================
# 🔥 GEMINI INTELLIGENCE (ADDED SAFELY)
# =====================================================

def gemini_reply(phone, user_message):

    if not GEMINI_API_KEY:
        logger.warning("Gemini API key missing")
        return None

    try:
        language = language_sessions.get(phone, "en")
        instruction = "Reply in Telugu." if language == "tel" else "Reply in English."

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"""
You are official assistant of Sri Parvathi Jadala Ramalingeshwara Swamy Temple.

Rules:
- Be respectful and devotional.
- Answer clearly.
- If unrelated to temple, gently guide back.
- {instruction}

User:
{user_message}
"""
                        }
                    ]
                }
            ]
        }

        response = requests.post(url, json=payload)
        result = response.json()

        if "candidates" in result:
            return result["candidates"][0]["content"]["parts"][0]["text"]

        logger.error(f"Gemini response error: {result}")
        return None

    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return None


# =====================================================
# HEALTH
# =====================================================

@app.api_route("/", methods=["GET", "HEAD"])
async def health():
    return {"status": "alive"}


# =====================================================
# WHATSAPP VERIFY
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

    data = await request.json()

    try:
        value = data["entry"][0]["changes"][0]["value"]

        if "messages" not in value:
            return {"status": "no message"}

        message = value["messages"][0]
        sender = normalize_phone(message["from"])
        msg_type = message["type"]

        if msg_type == "text":
            return handle_text(sender, message["text"]["body"].strip())

        if msg_type == "interactive":
            selected = message["interactive"]["list_reply"]["id"]
            return handle_navigation(sender, selected)

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"status": "ok"}


# =====================================================
# TEXT HANDLER (RESTORED + AI FALLBACK)
# =====================================================

def handle_text(sender, text):

    if sender in registration_sessions:
        return handle_registration(sender, text)

    if text.lower() in ["hi", "hello", "namaste", "start"]:
        send_language_selection(sender)
        return {"status": "language"}

    if text.lower().startswith("status"):
        parts = text.split(" ")
        if len(parts) < 2:
            send_text(sender, "Please enter booking ID.")
            return

        booking = bookings.find_one({"booking_id": parts[1]})
        if booking:
            send_text(sender, f"Status: {booking['status']}")
        else:
            send_text(sender, "Booking not found.")
        return

    # 🔥 AI fallback
    ai_response = gemini_reply(sender, text)
    if ai_response:
        send_text(sender, ai_response)
        return {"status": "ai"}

    # AI fallback
ai_reply = gemini_reply(sender, text)

if ai_reply:
    send_text(sender, ai_reply)
else:
    send_text(sender, "Please use menu options.")

return {"status": "ai"}


# =====================================================
# LANGUAGE
# =====================================================

def send_language_selection(phone):
    send_list(phone,
        "Om Namah Shivaya 🙏\nChoose Language:",
        [
            {"id":"lang_en","title":"English 🇬🇧"},
            {"id":"lang_tel","title":"తెలుగు 🇮🇳"}
        ]
    )


def send_main_menu(phone):
    send_list(phone,
        "Main Menu:",
        [
            {"id":"register","title":"📝 Register Devotee"},
            {"id":"darshan","title":"🕉 Darshan & Timings"},
            {"id":"accommodation","title":"🏠 Accommodation"},
            {"id":"donation","title":"💰 Donation"},
            {"id":"location","title":"📍 Location"},
            {"id":"history","title":"📜 History"},
            {"id":"contact","title":"📞 Contact"},
            {"id":"change_lang","title":"🌐 Change Language"}
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

    if selected == "change_lang":
        send_language_selection(phone)
        return

    if selected == "register":
        start_registration(phone)
        return

    if selected == "darshan":
        send_text(phone, "☀ 06:00 AM – 12:30 PM\n🌙 05:00 PM – 08:30 PM")
        send_main_menu(phone)
        return

    if selected == "accommodation":
        send_text(phone, "Accommodation booking coming soon.")
        send_main_menu(phone)
        return

    if selected == "donation":
        send_text(phone, "UPI Donations coming soon.")
        send_main_menu(phone)
        return

    if selected == "location":
        send_text(phone, "Cheruvugattu, Narketpally (5km), Nalgonda (18km)")
        send_main_menu(phone)
        return

    if selected == "history":
        send_text(phone, "Swayambhu Lingam associated with Sage Parashurama.")
        send_main_menu(phone)
        return

    if selected == "contact":
        send_text(phone, "Temple Office: 9390353848\n10 AM – 5 PM")
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
