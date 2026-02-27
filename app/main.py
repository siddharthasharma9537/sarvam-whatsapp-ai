from fastapi import FastAPI
from pymongo import MongoClient
import os
import logging
import razorpay

from app.services.whatsapp_service import send_list
from app.routes.webhook import router as webhook_router, init_dependencies

# =====================================================
# APP INIT
# =====================================================

app = FastAPI()
app.include_router(webhook_router)

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

if not all([VERIFY_TOKEN, WHATSAPP_TOKEN, PHONE_NUMBER_ID, MONGODB_URI]):
    raise Exception("Missing required environment variables")

# =====================================================
# DATABASE
# =====================================================

client = MongoClient(MONGODB_URI)
db = client["sohum_db"]

devotees = db["devotees"]
bookings = db["bookings"]
sessions = db["sessions"]   # 👈 ADD THIS LINE

devotees.create_index("phone", unique=True)
bookings.create_index("booking_id", unique=True)
sessions.create_index("phone", unique=True)   # 👈 ADD THIS LINE

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
async def health():
    return {"status": "alive"}

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
