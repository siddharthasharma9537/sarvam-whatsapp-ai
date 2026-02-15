from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient, ReturnDocument
from datetime import datetime
import requests
import os
import logging
import hmac
import hashlib

# Razorpay optional
try:
    import razorpay
except:
    razorpay = None


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
# OPTIONAL RAZORPAY INIT
# =====================================================

razorpay_client = None
if razorpay and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
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


def get_headers():
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }


def send_text(phone, message):
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    requests.post(GRAPH_URL, headers=get_headers(), json=data)


def send_list(phone, header, body, button_text, sections):
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": header},
            "body": {"text": body},
            "action": {
                "button": button_text,
                "sections": sections
            }
        }
    }
    requests.post(GRAPH_URL, headers=get_headers(), json=data)


def generate_booking_id(prefix):
    counter = counters.find_one_and_update(
        {"_id": prefix},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return f"SPJRSD-{prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M')}-{counter['seq']:04d}"


# =====================================================
# HEALTH CHECK
# =====================================================

@app.api_route("/", methods=["GET", "HEAD"])
async def root_health():
    return {
        "status": "alive",
        "service": "Temple WhatsApp Bot",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health")
async def health():
    try:
        client.admin.command("ping")
        return {"status": "healthy"}
    except:
        return {"status": "db_error"}


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

    data = await request.json()

    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
        sender = normalize_phone(message["from"])
        msg_type = message["type"]

        if msg_type == "text":
            return handle_text(sender, message["text"]["body"].strip())

        if msg_type == "interactive":
            interactive = message["interactive"]

            if interactive["type"] == "list_reply":
                selected = interactive["list_reply"]["id"]
            else:
                selected = interactive["button_reply"]["id"]

            return handle_navigation(sender, selected)

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"status": "ok"}


# =====================================================
# TEXT HANDLER
# =====================================================

def handle_text(sender, text):

    text_lower = text.lower()

    if sender in registration_sessions:
        return handle_registration(sender, text)

    if text_lower in ["hi", "hello", "namaste", "start"]:
        send_language_selection(sender)
        return {"status": "language"}

    send_text(sender, "Please use menu options.")
    return {"status": "unknown"}


# =====================================================
# LANGUAGE SELECTION
# =====================================================

def send_language_selection(phone):

    send_list(
        phone,
        "Om Namah Shivaya 🙏",
        "Choose Language / భాష ఎంచుకోండి:",
        "Select",
        [{
            "title": "Language",
            "rows": [
                {"id": "lang_en", "title": "English 🇬🇧"},
                {"id": "lang_tel", "title": "తెలుగు 🇮🇳"}
            ]
        }]
    )


# =====================================================
# MAIN MENU (LIST BASED)
# =====================================================

def send_main_menu(phone):

    lang = language_sessions.get(phone, "en")

    if lang == "en":
        header = "Temple Services"
        body = "Please select a service:"
        rows = [
            {"id":"register","title":"Register Devotee"},
            {"id":"darshan","title":"Darshan & Timings"},
            {"id":"accommodation","title":"Accommodation"},
            {"id":"donation","title":"Donations"},
            {"id":"location","title":"Location"},
            {"id":"history","title":"Temple History"},
            {"id":"contact","title":"Contact Office"},
            {"id":"change_lang","title":"Change Language"}
        ]
    else:
        header = "ఆలయ సేవలు"
        body = "సేవను ఎంచుకోండి:"
        rows = [
            {"id":"register","title":"భక్తుని నమోదు"},
            {"id":"darshan","title":"దర్శన సమయాలు"},
            {"id":"accommodation","title":"వసతి"},
            {"id":"donation","title":"విరాళం"},
            {"id":"location","title":"మార్గం"},
            {"id":"history","title":"క్షేత్ర పురాణం"},
            {"id":"contact","title":"సంప్రదించండి"},
            {"id":"change_lang","title":"భాష మార్చండి"}
        ]

    send_list(
        phone,
        header,
        body,
        "View Menu",
        [{
            "title": "Main Menu",
            "rows": rows
        }]
    )


# =====================================================
# NAVIGATION ROUTER
# =====================================================

def handle_navigation(phone, selected):

    logger.info(f"Selected: {selected}")

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
        send_text(phone, "Morning: 06:00–12:30\nEvening: 05:00–08:30")
        send_main_menu(phone)
        return

    if selected == "accommodation":
        send_text(phone, "Rooms:\nNon-AC ₹300\nAC ₹800\nDorm ₹100")
        send_main_menu(phone)
        return

    if selected == "donation":
        send_text(phone, "Support Annadanam via UPI.")
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

    send_main_menu(phone)


# =====================================================
# REGISTRATION FLOW
# =====================================================

def start_registration(phone):
    registration_sessions[phone] = {"step": "name"}
    send_text(phone, "Enter Full Name:")


def handle_registration(phone, text):

    session = registration_sessions[phone]

    if session["step"] == "name":

        devotees.update_one(
            {"phone": phone},
            {"$set": {
                "phone": phone,
                "full_name": text,
                "registered_at": datetime.utcnow()
            }},
            upsert=True
        )

        registration_sessions.pop(phone)

        send_text(phone, "Registration successful 🙏")
        send_main_menu(phone)

    return {"status": "registered"}


# =====================================================
# RAZORPAY WEBHOOK (SAFE)
# =====================================================

@app.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):

    if not RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=400)

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400)

    return {"status": "ok"}