from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from pymongo import MongoClient, ReturnDocument
from datetime import datetime
import razorpay
import requests
import os
import logging
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
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
MONGODB_URI = os.getenv("MONGODB_URI")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

if not all([
    VERIFY_TOKEN, WHATSAPP_TOKEN,
    PHONE_NUMBER_ID, MONGODB_URI,
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET
]):
    raise Exception("Missing required environment variables")

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
# RAZORPAY
# =====================================================

razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

# =====================================================
# SESSION STORE
# =====================================================

language_sessions = {}
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

def send_buttons(phone, text, buttons):
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {"buttons": buttons}
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
# HEALTH
# =====================================================

@app.api_route("/", methods=["GET", "HEAD"])
async def health():
    return {"status": "alive"}

# =====================================================
# WEBHOOK VERIFY (WhatsApp)
# =====================================================

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.mode") == "subscribe" and \
       request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(request.query_params.get("hub.challenge"))
    return PlainTextResponse("Verification failed", status_code=403)

# =====================================================
# MAIN WHATSAPP WEBHOOK
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
            selected = message["interactive"]["button_reply"]["id"]
            return handle_navigation(sender, selected)

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return {"status": "ok"}

# =====================================================
# TEXT HANDLER
# =====================================================

def handle_text(sender, text):

    if text.lower() in ["hi", "hello", "namaste", "start"]:
        send_language_selection(sender)
        return {"status": "language"}

    if text.lower().startswith("status"):
        booking_id = text.split(" ")[1]
        booking = bookings.find_one({"booking_id": booking_id})
        if booking:
            send_text(sender, f"Status: {booking['status']}")
        else:
            send_text(sender, "Booking not found.")
        return {"status": "status"}

    send_text(sender, "Please use menu options.")
    return {"status": "unknown"}

# =====================================================
# LANGUAGE SELECTION
# =====================================================

def send_language_selection(phone):
    send_buttons(
        phone,
        "Om Namah Shivaya 🙏\nChoose Language / భాష ఎంచుకోండి:",
        [
            {"type":"reply","reply":{"id":"lang_en","title":"English 🇬🇧"}},
            {"type":"reply","reply":{"id":"lang_tel","title":"తెలుగు 🇮🇳"}}
        ]
    )

# =====================================================
# MAIN MENU
# =====================================================

def send_main_menu(phone):

    lang = language_sessions.get(phone, "en")

    if lang == "en":
        text = "Main Menu:"
        options = [
            ("darshan","🕉 Darshan & Timings"),
            ("seva","🙏 Seva Booking"),
            ("accommodation","🏠 Accommodation"),
            ("donation","💰 Donations"),
            ("location","📍 Location"),
            ("history","📜 History"),
            ("contact","📞 Contact"),
            ("change_lang","🌐 Change Language")
        ]
    else:
        text = "మెయిన్ మెనూ:"
        options = [
            ("darshan","🕉 దర్శనం"),
            ("seva","🙏 పూజా బుకింగ్"),
            ("accommodation","🏠 వసతి"),
            ("donation","💰 విరాళం"),
            ("location","📍 మార్గం"),
            ("history","📜 పురాణం"),
            ("contact","📞 సంప్రదించండి"),
            ("change_lang","🌐 భాష మార్చండి")
        ]

    buttons = [
        {"type":"reply","reply":{"id":i,"title":t}}
        for i,t in options[:3]
    ]

    send_buttons(phone, text, buttons)

# =====================================================
# NAVIGATION
# =====================================================

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

    if selected == "accommodation":
        start_accommodation_booking(phone)
        return

    send_main_menu(phone)

# =====================================================
# ACCOMMODATION BOOKING (FULL FLOW)
# =====================================================

def start_accommodation_booking(phone):

    flow_sessions[phone] = {
        "step": "room_type"
    }

    send_buttons(
        phone,
        "Select Room Type:",
        [
            {"type":"reply","reply":{"id":"room_nonac","title":"Non-AC ₹300"}},
            {"type":"reply","reply":{"id":"room_ac","title":"AC ₹800"}},
            {"type":"reply","reply":{"id":"room_dorm","title":"Dormitory ₹100"}}
        ]
    )

# =====================================================
# RAZORPAY PAYMENT LINK
# =====================================================

def create_payment_link(amount, booking_id):

    payment = razorpay_client.payment_link.create({
        "amount": amount * 100,
        "currency": "INR",
        "description": "Temple Booking",
        "reference_id": booking_id,
        "callback_url": "https://your-domain.com",
        "callback_method": "get",
        "upi_link": True
    })

    return payment["short_url"], payment["id"]

# =====================================================
# RAZORPAY WEBHOOK
# =====================================================

@app.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = await request.json()

    if payload["event"] == "payment_link.paid":
        payment = payload["payload"]["payment_link"]["entity"]
        booking_id = payment["reference_id"]

        bookings.update_one(
            {"booking_id": booking_id},
            {"$set": {"status": "paid"}}
        )

    return {"status": "ok"}

# =====================================================
# REFUND
# =====================================================

@app.post("/admin/refund/{booking_id}")
async def refund_booking(booking_id: str):

    booking = bookings.find_one({"booking_id": booking_id})

    if not booking:
        raise HTTPException(status_code=404)

    razorpay_client.payment.refund(booking["razorpay_payment_id"])

    bookings.update_one(
        {"booking_id": booking_id},
        {"$set": {"status": "refunded"}}
    )

    return {"status": "refund initiated"}
