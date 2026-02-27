from fastapi import FastAPI
from pymongo import MongoClient
import os
import logging
import razorpay

from app.services.whatsapp_service import send_list
from app.routes.webhook import router as webhook_router, init_dependencies

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import hashlib
from datetime import datetime, timedelta

print("MAIN FILE LOADED")


app = FastAPI()
app.include_router(webhook_router)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.error(f"HTTP error: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "Request failed", "detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
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

DEV_ADMIN_PHONE = os.getenv("DEV_ADMIN_PHONE")
DEV_ADMIN_KEY = os.getenv("DEV_ADMIN_KEY")

if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID, MONGODB_URI]):
    raise Exception("Missing required environment variables")

# =====================================================
# DATABASE
# =====================================================

client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000
)
db = client["sohum_db"]

devotees = db["devotees"]
bookings = db["bookings"]
sessions = db["sessions"]   # 👈 ADD THIS LINE
processed_messages = db["processed_messages"]
admin_users = db["admin_users"]
admin_sessions = db["admin_sessions"]
offerings = db["offerings"]
membership_audit_logs = db["membership_audit_logs"]

devotees.create_index("phone", unique=True)
bookings.create_index("booking_id", unique=True)
sessions.create_index("phone", unique=True)   # 👈 ADD THIS LINE
processed_messages.create_index("message_id", unique=True)
admin_users.create_index("phone", unique=True)
admin_sessions.create_index("phone", unique=True)
offerings.create_index("offering_id", unique=True)
offerings.create_index("phone")
membership_audit_logs.create_index([("phone", 1), ("timestamp", -1)])

# =====================================================
# STARTUP VALIDATION
# =====================================================

@app.on_event("startup")
async def startup_checks():
    try:
        client.admin.command("ping")
        logger.info("MongoDB connection established successfully.")

        # ------------------------------------------
        # AUTO-CREATE DEV ADMIN IF NOT EXISTS
        # ------------------------------------------
        if DEV_ADMIN_PHONE and DEV_ADMIN_KEY:
            existing_dev_admin = admin_users.find_one({
                "phone": DEV_ADMIN_PHONE,
                "role": "dev_admin"
            })

            if not existing_dev_admin:
                key_hash = hashlib.sha256(DEV_ADMIN_KEY.encode()).hexdigest()

                admin_users.insert_one({
                    "phone": DEV_ADMIN_PHONE,
                    "name": "Dev Admin",
                    "role": "dev_admin",
                    "personal_key_hash": key_hash,
                    "key_last_changed": datetime.utcnow(),
                    "created_at": datetime.utcnow(),
                    "active": True
                })

                logger.info("Dev admin auto-created successfully.")
            else:
                # Ensure correct role and active status
                admin_users.update_one(
                    {"phone": DEV_ADMIN_PHONE},
                    {"$set": {
                        "role": "dev_admin",
                        "active": True
                    }}
                )
                logger.info("Dev admin already exists. Role verified.")
        else:
            logger.warning("DEV_ADMIN_PHONE or DEV_ADMIN_KEY not set. Dev admin not auto-created.")
    except Exception as e:
        logger.error(f"MongoDB connection failed during startup: {e}")
        raise

# =====================================================
# RAZORPAY INIT
# =====================================================

razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    )

# =====================================================
# MENU FUNCTIONS
# =====================================================

def send_language_selection(phone):
    send_list(
        phone,
        "Choose Language:",
        [
            {"id": "lang_en", "title": "English 🇬🇧"},
            {"id": "lang_tel", "title": "తెలుగు 🇮🇳"}
        ]
    )


def send_main_menu(phone):
    from app.services.session_service import get_language
    lang = get_language(phone, sessions)

    header_en = "🛕 Sri Parvati Jadala Ramalingeshwara Swamy Temple\nCheruvugattu\n\nPlease choose an option:" 
    header_tel = "🛕 శ్రీ పార్వతి జడల రామలింగేశ్వర స్వామి దేవస్థానం\nచెరువుగట్టు\n\nదయచేసి ఒక ఎంపికను ఎంచుకోండి:" 

    if lang == "tel":
        send_list(
            phone,
            header_tel,
            [
                {"id": "register", "title": "📝 భక్తుడు నమోదు"},
                {"id": "history", "title": "📜 స్థలపురాణం"},
                {"id": "next_tithi", "title": "🌕 తదుపరి తిథి"},
                {"id": "change_lang", "title": "🌐 భాష మార్చండి"}
            ]
        )
    else:
        send_list(
            phone,
            header_en,
            [
                {"id": "register", "title": "📝 Register Devotee"},
                {"id": "history", "title": "📜 History"},
                {"id": "next_tithi", "title": "🌕 Know Next Tithi"},
                {"id": "change_lang", "title": "🌐 Change Language"}
            ]
        )

# =====================================================
# HEALTH
# =====================================================

@app.get("/")
async def root():
    return {"status": "alive"}


@app.get("/health")
async def health_check():
    try:
        client.admin.command("ping")
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "unhealthy", "database": "disconnected"}

# =====================================================
# DEPENDENCY INJECTION INTO ROUTER
# =====================================================

init_dependencies(
    VERIFY_TOKEN,
    devotees,
    sessions,
    processed_messages,
    admin_users,
    admin_sessions,
    offerings,
    membership_audit_logs,
    send_main_menu,
    send_language_selection
)
