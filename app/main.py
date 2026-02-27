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

devotees.create_index("phone", unique=True)
bookings.create_index("booking_id", unique=True)
sessions.create_index("phone", unique=True)   # 👈 ADD THIS LINE

# =====================================================
# STARTUP VALIDATION
# =====================================================

@app.on_event("startup")
async def startup_checks():
    try:
        client.admin.command("ping")
        logger.info("MongoDB connection established successfully.")
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

    if lang == "tel":
        send_list(
            phone,
            "ప్రధాన మెను:",
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
            "Main Menu:",
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
    send_main_menu,
    send_language_selection
)
