from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
import logging
import os
import hmac
import hashlib
import json
from datetime import datetime, timedelta

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
processed_messages = None
send_main_menu = None
send_language_selection = None
admin_users = None
admin_sessions = None
offerings = None
membership_audit_logs = None


def init_dependencies(
    verify_token,
    devotees_collection,
    sessions_collection,
    processed_collection,
    admin_users_collection,
    admin_sessions_collection,
    offerings_collection,
    membership_audit_logs_collection,
    send_main_menu_func,
    send_language_selection_func,
):
    global VERIFY_TOKEN
    global devotees
    global sessions
    global processed_messages
    global admin_users
    global admin_sessions
    global offerings
    global membership_audit_logs
    global send_main_menu
    global send_language_selection

    VERIFY_TOKEN = verify_token
    devotees = devotees_collection
    sessions = sessions_collection
    processed_messages = processed_collection
    admin_users = admin_users_collection
    admin_sessions = admin_sessions_collection
    offerings = offerings_collection
    membership_audit_logs = membership_audit_logs_collection
    send_main_menu = send_main_menu_func
    send_language_selection = send_language_selection_func


# =====================================================
# SIGNATURE VERIFICATION
# =====================================================

def verify_signature(request: Request, body: bytes) -> bool:
    signature = request.headers.get("X-Hub-Signature-256")

    # If APP_SECRET is not configured, skip verification (development mode)
    if not APP_SECRET:
        logger.warning("APP_SECRET not set — skipping signature verification")
        return True

    if not signature:
        logger.warning("Missing signature header")
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

        message_id = message.get("id")

        if message_id and processed_messages is not None:
            if processed_messages.find_one({"message_id": message_id}):
                logger.info(f"Duplicate message ignored: {message_id}")
                return {"status": "duplicate"}

            processed_messages.insert_one({
                "message_id": message_id
            })

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

    # -------------------------------------------------
    # ADMIN LOGIN (admin <personal_key>)
    # -------------------------------------------------
    if text.lower().startswith("admin "):
        parts = text.strip().split(" ", 1)
        if len(parts) != 2:
            send_text(sender, "Invalid admin format.")
            return

        key = parts[1].strip()
        key_hash = hashlib.sha256(key.encode()).hexdigest()

        admin = admin_users.find_one({
            "phone": sender,
            "active": True
        })

        if not admin or admin.get("personal_key_hash") != key_hash:
            membership_audit_logs.insert_one({
                "phone": sender,
                "action": "admin_login_failed",
                "timestamp": datetime.utcnow()
            })
            send_text(sender, "Access denied.")
            return

        expires_at = datetime.utcnow() + timedelta(minutes=10)

        admin_sessions.update_one(
            {"phone": sender},
            {"$set": {
                "active": True,
                "activated_at": datetime.utcnow(),
                "last_action": datetime.utcnow(),
                "expires_at": expires_at
            }},
            upsert=True
        )

        membership_audit_logs.insert_one({
            "phone": sender,
            "action": "admin_login_success",
            "timestamp": datetime.utcnow()
        })

        send_text(sender, "🛕 Admin mode activated.")
        return


    # -------------------------------------------------
    # CHECK ACTIVE ADMIN SESSION
    # -------------------------------------------------
    active_session = admin_sessions.find_one({
        "phone": sender,
        "active": True,
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if active_session:
        admin_sessions.update_one(
            {"phone": sender},
            {"$set": {"last_action": datetime.utcnow()}}
        )

        # -----------------------------
        # EXIT ADMIN MODE
        # -----------------------------
        if text.strip().lower() == "exit":
            admin_sessions.update_one(
                {"phone": sender},
                {"$set": {"active": False}, "$unset": {"key_change_step": ""}}
            )

            membership_audit_logs.insert_one({
                "phone": sender,
                "action": "admin_logout",
                "timestamp": datetime.utcnow()
            })

            send_text(sender, "Admin mode exited.")
            return

        # -----------------------------
        # INITIATE KEY CHANGE
        # -----------------------------
        if text.strip().lower() == "change_key":
            admin_sessions.update_one(
                {"phone": sender},
                {"$set": {"key_change_step": "verify_old"}}
            )
            send_text(sender, "Enter current key:")
            return

        # -----------------------------
        # INITIATE ADMIN CREATION (DEV ADMIN ONLY)
        # -----------------------------
        admin_record = admin_users.find_one({"phone": sender})

        if text.strip().lower() == "create_admin":
            if not admin_record or admin_record.get("role") != "dev_admin":
                send_text(sender, "Only Dev Admin can create new admins.")
                return

            admin_sessions.update_one(
                {"phone": sender},
                {"$set": {"admin_create_step": "enter_phone"}}
            )
            send_text(sender, "Enter new admin phone number (without +):")
            return

        # -----------------------------
        # HANDLE ADMIN CREATION FLOW
        # -----------------------------
        create_step = active_session.get("admin_create_step")

        if create_step == "enter_phone":
            new_phone = text.strip()

            if admin_users.find_one({"phone": new_phone}):
                send_text(sender, "Admin with this phone already exists.")
                return

            admin_sessions.update_one(
                {"phone": sender},
                {"$set": {"admin_create_step": "enter_role", "new_admin_phone": new_phone}}
            )
            send_text(sender, "Enter role (admin / super_admin):")
            return

        if create_step == "enter_role":
            role = text.strip().lower()

            if role not in ["admin", "super_admin"]:
                send_text(sender, "Invalid role. Enter 'admin' or 'super_admin'.")
                return

            admin_sessions.update_one(
                {"phone": sender},
                {"$set": {"admin_create_step": "enter_key", "new_admin_role": role}}
            )
            send_text(sender, "Enter temporary personal key for new admin:")
            return

        if create_step == "enter_key":
            new_key_hash = hashlib.sha256(text.strip().encode()).hexdigest()

            new_phone = active_session.get("new_admin_phone")
            new_role = active_session.get("new_admin_role")

            admin_users.insert_one({
                "phone": new_phone,
                "role": new_role,
                "personal_key_hash": new_key_hash,
                "key_last_changed": datetime.utcnow(),
                "created_at": datetime.utcnow(),
                "active": True
            })

            membership_audit_logs.insert_one({
                "phone": sender,
                "action": "admin_created",
                "timestamp": datetime.utcnow(),
                "details": {"created_admin": new_phone}
            })

            admin_sessions.update_one(
                {"phone": sender},
                {"$unset": {"admin_create_step": "", "new_admin_phone": "", "new_admin_role": ""}}
            )

            send_text(sender, f"Admin {new_phone} created successfully.")
            return

        # -----------------------------
        # HANDLE KEY CHANGE FLOW
        # -----------------------------
        step = active_session.get("key_change_step")

        if step == "verify_old":
            current_hash = hashlib.sha256(text.strip().encode()).hexdigest()

            admin = admin_users.find_one({"phone": sender})

            if not admin or admin.get("personal_key_hash") != current_hash:
                send_text(sender, "Incorrect current key.")
                return

            admin_sessions.update_one(
                {"phone": sender},
                {"$set": {"key_change_step": "enter_new"}}
            )
            send_text(sender, "Enter new key:")
            return

        if step == "enter_new":
            new_hash = hashlib.sha256(text.strip().encode()).hexdigest()

            admin_users.update_one(
                {"phone": sender},
                {"$set": {
                    "personal_key_hash": new_hash,
                    "key_last_changed": datetime.utcnow()
                }}
            )

            membership_audit_logs.insert_one({
                "phone": sender,
                "action": "key_changed",
                "timestamp": datetime.utcnow()
            })

            # Invalidate session after key change
            admin_sessions.update_one(
                {"phone": sender},
                {"$set": {"active": False}, "$unset": {"key_change_step": ""}}
            )

            send_text(sender, "Key updated successfully. Please login again.")
            return

        # -----------------------------
        # DEFAULT ADMIN RESPONSE
        # -----------------------------
        send_text(sender, "Admin command received.")
        return

    if sender in registration_sessions:
        return handle_registration(sender, text, devotees, send_main_menu)

    lower = text.strip().lower()

    if lower in ["hi", "hello", "namaste", "start"]:
        from app.services.session_service import get_language

        lang = get_language(sender, sessions)

        # If no language set yet → new user
        if not lang:
            send_language_selection(sender)
            return

        # Existing user → go to main menu
        send_main_menu(sender)
        return

    if lower in ["menu", "main menu"]:
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
