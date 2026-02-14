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

# =====================================================
# DATABASE
# =====================================================

if not MONGODB_URI:
    raise Exception("MONGODB_URI not set in environment variables")

client = MongoClient(MONGODB_URI)
db = client["sohum_db"]
devotees_collection = db["devotees"]

# =====================================================
# TEMP REGISTRATION SESSION
# =====================================================

registration_sessions = {}

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

        # ===============================
        # TEXT MESSAGE
        # ===============================

        if msg_type == "text":

            text = message_obj["text"]["body"]
            text_lower = text.lower()

            # 🔥 FIXED: use registration_sessions
            if sender in registration_sessions:
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

        # ===============================
        # INTERACTIVE
        # ===============================

        elif msg_type == "interactive":

            interactive = message_obj.get("interactive", {})
            interactive_type = interactive.get("type")

            selected_id = None

            if interactive_type == "list_reply":
                selected_id = interactive.get("list_reply", {}).get("id")

            elif interactive_type == "button_reply":
                selected_id = interactive.get("button_reply", {}).get("id")

            if selected_id == "register":
                start_registration(sender)
            elif selected_id:
                reply = handle_button(selected_id)
                send_whatsapp(sender, reply)
            else:
                send_whatsapp(sender, "Please try again.")

        # ===============================
        # AUDIO MESSAGE
        # ===============================

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
# REGISTRATION FLOW
# =====================================================

def start_registration(phone):

    registration_sessions[phone] = {"step": "name"}

    send_whatsapp(
        phone,
        """🙏 Devotee Registration

For Temple Records and Seva Updates,
please share the following details:

1. Full Name
2. Gotram
3. Address
4. Mobile Number
5. Email (optional)

If any detail is not available, please type: no

Please enter your Full Name:"""
    )


def handle_registration(phone, text):

    user = registration_sessions.get(phone)
    if not user:
        return

    step = user["step"]

    if step == "name":
        user["full_name"] = text
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
        user["mobile_number"] = text
        user["step"] = "email"
        send_whatsapp(phone, "Please enter your Email (or type no):")
        return

    if step == "email":

        user["email"] = text if text.lower() != "no" else "Not Provided"
        user["step"] = "confirm"

        send_whatsapp(
            phone,
            f"""🙏 Please review your details:

Full Name: {user['full_name']}
Gotram: {user['gotram']}
Address: {user['address']}
Mobile: {user['mobile_number']}
Email: {user['email']}

━━━━━━━━━━━━━━━━━━

By submitting these details, you agree that
the information will be used only for
Temple records and temple-related communication.

Reply YES to confirm and register
Reply NO to cancel"""
        )
        return

    if step == "confirm":

        if text.lower() == "yes":

            devotees_collection.update_one(
                {"phone": phone},
                {"$set": {
                    "phone": phone,
                    "full_name": user["full_name"],
                    "gotram": user["gotram"],
                    "address": user["address"],
                    "mobile_number": user["mobile_number"],
                    "email": user["email"],
                    "consent": True,
                    "registered_at": datetime.utcnow(),
                    "last_updated": datetime.utcnow()
                }},
                upsert=True
            )

            registration_sessions.pop(phone, None)

            send_whatsapp(
                phone,
                "🙏 Registration successful!\n\nYou are now registered with the Temple."
            )

        else:
            registration_sessions.pop(phone, None)
            send_whatsapp(phone, "Registration cancelled.")

# =====================================================
# MENU
# =====================================================

def send_menu(to):
    headers = get_headers()

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": "🙏 Namaskaram!\nType register to register as devotee."}
    }

    requests.post(GRAPH_URL, headers=headers, json=data)

# =====================================================
# BUTTON HANDLER
# =====================================================

def handle_button(button):

    if button == "timings":
        return "Temple timings: Morning 5:00–12:30, Evening 3:00–7:00"

    if button == "location":
        return "https://maps.google.com/?q=17.17491,79.21219"

    return "Please choose valid option."

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
                    "text": f"You are temple assistant.\nUser: {user_message}"
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
# WHATSAPP SEND
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
