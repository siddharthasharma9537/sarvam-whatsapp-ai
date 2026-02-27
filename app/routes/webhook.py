from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
import logging
import os
import hmac
import hashlib
import json

from app.services.whatsapp_service import normalize_phone, send_text, send_image
from app.services.tithi_service import get_next_tithi
from app.services.registration_service import (
    start_registration,
    handle_registration,
    registration_sessions
)

# =====================================================
# ROUTER & GLOBALS
# =====================================================

router = APIRouter()
logger = logging.getLogger("TempleBot")

APP_SECRET = os.getenv("APP_SECRET")
VERIFY_TOKEN = None
devotees = None
sessions = None
send_main_menu = None
send_language_selection = None


def init_dependencies(
    verify_token,
    devotees_collection,
    sessions_collection,
    send_main_menu_func,
    send_language_selection_func,
):
    global VERIFY_TOKEN, devotees, sessions, send_main_menu, send_language_selection

    VERIFY_TOKEN = verify_token
    devotees = devotees_collection
    sessions = sessions_collection
    send_main_menu = send_main_menu_func
    send_language_selection = send_language_selection_func


# =====================================================
# SIGNATURE VERIFICATION
# =====================================================

def verify_signature(request: Request, body: bytes) -> bool:
    signature = request.headers.get("X-Hub-Signature-256")

    if not signature or not APP_SECRET:
        logger.warning("Missing signature or APP_SECRET")
        return False

    expected_signature = "sha256=" + hmac.new(
        APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_signature, signature)


# =====================================================
# VERIFY ENDPOINT
# =====================================================

@router.get("/webhook")
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

@router.post("/webhook")
async def webhook(request: Request):
    body = await request.body()

    if not verify_signature(request, body):
        logger.warning("Invalid webhook signature")
        return {"status": "invalid signature"}

    data = json.loads(body)
    logger.info(f"WEBHOOK RECEIVED: {data}")

    try:
        entry = data.get("entry", [])
        if not entry:
            return {"status": "no entry"}

        value = entry[0]["changes"][0]["value"]

        if "messages" not in value:
            return {"status": "no message"}

        message = value["messages"][0]
        sender = normalize_phone(message["from"])

        if message.get("type") == "text":
            handle_text(sender, message["text"]["body"])

        elif message.get("type") == "interactive":
            interactive = message.get("interactive", {})
            list_reply = interactive.get("list_reply")
            if list_reply:
                handle_navigation(sender, list_reply.get("id"))

    except Exception:
        logger.exception("Webhook processing error")

    return {"status": "ok"}


# =====================================================
# TEXT HANDLER
# =====================================================

def handle_text(sender: str, text: str):

    if sender in registration_sessions:
        return handle_registration(sender, text, devotees, send_main_menu)

    lower = text.strip().lower()

    if lower in ["hi", "hello", "namaste", "start", "menu", "main menu"]:
        send_main_menu(sender)
        return

    send_text(sender, "Please use menu options.")


# =====================================================
# NAVIGATION HANDLER
# =====================================================

def handle_navigation(phone: str, selected: str):

    if not selected:
        return

    if selected in ["lang_en", "lang_tel"]:
        from app.services.session_service import set_language

        lang = "en" if selected == "lang_en" else "tel"
        set_language(phone, lang, sessions)
        send_main_menu(phone)
        return

    if selected == "change_lang":
        send_language_selection(phone)
        return

    if selected == "next_tithi":
        amavasya = get_next_tithi("amavasya")
        pournami = get_next_tithi("pournami")

        if not amavasya and not pournami:
            send_text(phone, "No upcoming tithis found.")
            send_main_menu(phone)
            return

        message = ""
        if amavasya:
            message += f"🌑 Next Amavasya:\n{amavasya['date_iso']}\n\n"
        if pournami:
            message += f"🌕 Next Pournami:\n{pournami['date_iso']}"

        send_text(phone, message.strip())
        send_main_menu(phone)
        return

    if selected == "register":
        start_registration(phone, devotees, send_main_menu)
        return

    if selected == "history":
        from app.services.session_service import get_language

        lang = get_language(phone, sessions)

        if lang == "tel":
            send_image(
                phone,
                "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20TEL%20(1).PNG",
                "స్థలపురాణము",
            )
        else:
            send_image(
                phone,
                "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20ENG%20(1).PNG",
                "Temple History",
            )

        send_main_menu(phone)
        return

    send_text(phone, "Invalid option selected.")
    send_main_menu(phone)
