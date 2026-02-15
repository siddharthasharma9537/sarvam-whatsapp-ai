from fastapi import FastAPI, Request, HTTPException
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
sevas_master = db["sevas_master"]
accommodation_master = db["accommodation_master"]
seva_bookings = db["seva_bookings"]
accommodation_bookings = db["accommodation_bookings"]
counters = db["counters"]

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
    requests.post(GRAPH_URL, headers=get_headers(), json=data)

# =====================================================
# BOOKING ID ENGINE
# =====================================================

def generate_booking_id(prefix):

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M")

    counter_doc = counters.find_one_and_update(
        {"_id": prefix},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )

    seq = counter_doc["seq"]
    return f"SPJRSD-{prefix}-{timestamp}-{seq:04d}"

# =====================================================
# HEALTH
# =====================================================

@app.api_route("/", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "alive"}
    
# =====================================================
# ADMIN
# =====================================================

@app.get("/admin/bookings/seva")
async def list_seva_bookings(date: str = None):
    query = {}
    if date:
        query["booking_date"] = date
    return list(seva_bookings.find(query, {"_id": 0}))

@app.get("/admin/bookings/accommodation")
async def list_accommodation_bookings(date: str = None):
    query = {}
    if date:
        query["booking_date"] = date
    return list(accommodation_bookings.find(query, {"_id": 0}))

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
# TEXT HANDLER
# =====================================================

def handle_text(sender, text):

    if sender in registration_sessions:
        handle_registration(sender, text)
        return {"status": "registration"}

    if sender in flow_sessions:
        handle_booking_flow(sender, text)
        return {"status": "flow"}

    if text.lower() in ["hi", "hello", "menu", "start"]:
        send_main_menu(sender)
        return {"status": "menu"}

    if text.lower() == "register":
        start_registration(sender)
        return {"status": "register"}

    # AI fallback only unknown
    reply = gemini_reply(text)
    send_text(sender, reply)
    return {"status": "ai"}

# =====================================================
# MAIN MENU
# =====================================================

def send_main_menu(phone):
    send_buttons(
        phone,
        "SPJRS Temple Services",
        [
            {"type":"reply","reply":{"id":"register","title":"Register Devotee"}},
            {"type":"reply","reply":{"id":"seva_booking","title":"Seva Booking"}},
            {"type":"reply","reply":{"id":"accommodation_booking","title":"Accommodation"}},
        ]
    )

# =====================================================
# NAVIGATION
# =====================================================

def handle_navigation(phone, selected_id):

    if selected_id == "register":
        start_registration(phone)

    elif selected_id == "seva_booking":
        start_seva_booking(phone)

    elif selected_id == "accommodation_booking":
        start_accommodation_booking(phone)

    return {"status": "navigation"}

# =====================================================
# SEVA BOOKING FLOW
# =====================================================

def start_seva_booking(phone):

    flow_sessions[phone] = {
        "flow": "seva",
        "step": "select_seva"
    }

    sevas = list(sevas_master.find({}, {"_id":0}))
    if not sevas:
        send_text(phone, "No sevas configured.")
        return

    buttons = [
        {"type":"reply","reply":{"id":s["code"],"title":s["name"]}}
        for s in sevas[:3]
    ]

    send_buttons(phone, "Select Seva:", buttons)

def handle_booking_flow(phone, text):

    session = flow_sessions[phone]

    if session["flow"] == "seva":

        if session["step"] == "select_date":
            session["date"] = text
            confirm_seva_booking(phone)

def confirm_seva_booking(phone):

    session = flow_sessions[phone]
    booking_id = generate_booking_id("SB")

    seva_bookings.insert_one({
        "booking_id": booking_id,
        "phone": phone,
        "seva": session["seva"],
        "booking_date": session["date"],
        "created_at": datetime.utcnow()
    })

    flow_sessions.pop(phone)
    send_text(phone, f"Seva booked successfully!\nBooking ID: {booking_id}")
    send_main_menu(phone)

# =====================================================
# ACCOMMODATION BOOKING FLOW
# =====================================================

def start_accommodation_booking(phone):

    flow_sessions[phone] = {
        "flow": "accommodation",
        "step": "select_room"
    }

    rooms = list(accommodation_master.find({}, {"_id":0}))
    if not rooms:
        send_text(phone, "No rooms configured.")
        return

    buttons = [
        {"type":"reply","reply":{"id":r["code"],"title":r["name"]}}
        for r in rooms[:3]
    ]

    send_buttons(phone, "Select Room Type:", buttons)

# =====================================================
# REGISTRATION
# =====================================================

def start_registration(phone):
    registration_sessions[phone] = {"step": "name"}
    send_text(phone, "Enter Full Name:")

def handle_registration(phone, text):

    session = registration_sessions[phone]
    step = session["step"]

    if step == "name":
        session["name"] = text
        session["step"] = "confirm"
        send_buttons(
            phone,
            f"Confirm registration for {text}?",
            [
                {"type":"reply","reply":{"id":"confirm_reg","title":"Confirm"}},
                {"type":"reply","reply":{"id":"cancel_reg","title":"Cancel"}}
            ]
        )

# =====================================================
# GEMINI
# =====================================================

def gemini_reply(text):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
        data = {"contents":[{"parts":[{"text":f"You are temple assistant.\nUser: {text}"}]}]}
        response = requests.post(url, json=data)
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except:
        return "Please try again."
