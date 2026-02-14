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

# Create unique index for phone (run safely)
try:
    devotees_collection.create_index("phone", unique=True)
except:
    pass


# =====================================================
# TEMP SESSION STORE
# =====================================================

registration_sessions = {}


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
    print("Incoming:", data)

    try:
        entry = data.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})

        if "messages" not in value:
            return {"status": "no message"}

        message_obj = value["messages"][0]
        sender = normalize_phone(message_obj.get("from"))
        msg_type = message_obj.get("type")

        print("Sender:", sender)
        print("Type:", msg_type)

        # =========================
        # TEXT MESSAGE
        # =========================

        if msg_type == "text":

            text = message_obj["text"]["body"]
            text_lower = text.strip().lower()

            # REGISTRATION FLOW ACTIVE
            if sender in registration_sessions:
                handle_registration(sender, text)
                return {"status": "registration handled"}

            # GREETING
            if text_lower in ["hi", "hello", "namaskaram", "menu", "start"]:

                existing = devotees_collection.find_one({"phone": sender})

                if existing and existing.get("registration_status") == "active":
                    send_whatsapp(
                        sender,
                        f"🙏 Namaskaram {existing.get('full_name','')}!\n\n"
                        "Welcome back to the Temple.\n"
                        "Type menu to view services."
                    )
                else:
                    send_menu(sender)

                return {"status": "greeting handled"}

            # REGISTER COMMAND
            if text_lower == "register":

                existing = devotees_collection.find_one({"phone": sender})

                if existing and existing.get("registration_status") == "active":
                    send_whatsapp(
                        sender,
                        "🙏 You are already registered with the Temple."
                    )
                else:
                    start_registration(sender)

                return {"status": "register handled"}

            # DEFAULT AI
            reply = gemini_reply(text)
            send_whatsapp(sender, reply)

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

            if selected_id == "register":

                existing = devotees_collection.find_one({"phone": sender})

                if existing and existing.get("registration_status") == "active":
                    send_whatsapp(sender, "🙏 You are already registered.")
                else:
                    start_registration(sender)

            elif selected_id:
                reply = handle_button(selected_id)
                send_whatsapp(sender, reply)

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
please share:

1. Full Name
2. Gotram
3. Address
4. Mobile Number
5. Email (optional)

If any detail is not available, type: no

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

Reply YES to confirm
Reply NO to cancel"""
        )
        return

    if step == "confirm":

        if text.lower() == "yes":

            try:
                devotees_collection.insert_one({
                    "phone": phone,
                    "full_name": user["full_name"],
                    "gotram": user["gotram"],
                    "address": user["address"],
                    "mobile_number": user["mobile_number"],
                    "email": user["email"],
                    "consent": True,
                    "registration_status": "active",
                    "registered_at": datetime.utcnow(),
                    "last_updated": datetime.utcnow()
                })

                send_whatsapp(
                    phone,
                    "🙏 Registration successful!\n\nYou are now registered with the Temple."
                )

            except Exception as e:
                print("Duplicate error:", e)
                send_whatsapp(
                    phone,
                    "🙏 You are already registered with the Temple."
                )

        else:
            send_whatsapp(phone, "Registration cancelled.")

        registration_sessions.pop(phone, None)


# =====================================================
# MENU
# =====================================================

def send_menu(to):

    headers = get_headers()

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Temple Services"},
            "body": {"text": "🙏 Namaskaram!\nPlease select:"},
            "action": {
                "button": "View Menu",
                "sections": [
                    {
                        "title": "Temple Information",
                        "rows": [
                            {"id": "timings", "title": "Temple Timings"},
                            {"id": "location", "title": "Temple Location"}
                        ]
                    },
                    {
                        "title": "Devotee Services",
                        "rows": [
                            {"id": "register", "title": "Register Devotee"}
                        ]
                    }
                ]
            }
        }
    }

    requests.post(GRAPH_URL, headers=headers, json=data)


# =====================================================
# BUTTON HANDLER
# =====================================================

def handle_button(button):

    if button == "timings":
        return "🕉 Temple timings:\nMorning 5:00–12:30\nEvening 3:00–7:00"

    if button == "location":
        return "📍 https://maps.google.com/?q=17.17491,79.21219"

    return "Please choose valid option."


# =====================================================
# GEMINI AI
# =====================================================

def gemini_reply(user_message):

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"

        data = {
            "contents": [{
                "parts": [{
                    "text": f"You are temple assistant.\nUser: {user_message}"
                }]
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

    response = requests.post(GRAPH_URL, headers=headers, json=data)
    print("Send response:", response.text)


def get_headers():
    return {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
