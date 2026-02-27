from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
import logging

from app.services.whatsapp_service import normalize_phone, send_text
from app.services.tithi_service import get_next_tithi
from app.services.registration_service import (
    start_registration,
    handle_registration,
    registration_sessions
)

router = APIRouter()
logger = logging.getLogger("TempleBot")

# These will be injected from main.py
VERIFY_TOKEN = None
devotees = None
send_main_menu = None
send_language_selection = None
language_sessions = None


def init_dependencies(
    verify_token,
    devotees_collection,
    send_main_menu_func,
    send_language_selection_func,
    language_sessions_dict
):
    global VERIFY_TOKEN, devotees, send_main_menu, send_language_selection, language_sessions

    VERIFY_TOKEN = verify_token
    devotees = devotees_collection
    send_main_menu = send_main_menu_func
    send_language_selection = send_language_selection_func
    language_sessions = language_sessions_dict


# ===============================
# VERIFY
# ===============================

@router.get("/webhook")
async def verify(request: Request):
    if (
        request.query_params.get("hub.mode") == "subscribe"
        and request.query_params.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return PlainTextResponse(request.query_params.get("hub.challenge"))

    return PlainTextResponse("Verification failed", status_code=403)


# ===============================
# MAIN WEBHOOK
# ===============================

@router.post("/webhook")
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


# ===============================
# TEXT HANDLER
# ===============================

def handle_text(sender, text):

    if sender in registration_sessions:
        return handle_registration(sender, text, devotees, send_main_menu)

    lower = text.strip().lower()

    if lower in ["hi", "hello", "namaste", "start"]:
        send_main_menu(sender)
        return {"status": "menu"}

    if lower in ["menu", "main menu"]:
        send_main_menu(sender)
        return {"status": "menu"}

    send_text(sender, "Please use menu options.")
    return {"status": "unknown"}


# ===============================
# NAVIGATION
# ===============================

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

    if selected == "register":
        return start_registration(phone, devotees, send_main_menu)

    if selected == "history":
        lang = language_sessions.get(phone, "en")

        from app.services.whatsapp_service import send_image

        if lang == "tel":
            send_image(
                phone,
                "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20TEL%20(1).PNG",
                "స్థలపురాణము"
            )
        else:
            send_image(
                phone,
                "https://pub-d1d3a6c8900e4412aac6397524edd899.r2.dev/SPJRSD%20Temple%20History%20ENG%20(1).PNG",
                "Temple History"
            )

        send_main_menu(phone)
        return {"status": "history"}

    send_text(phone, "Invalid option selected.")
    send_main_menu(phone)
    return {"status": "unknown"}
