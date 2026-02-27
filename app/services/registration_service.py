from datetime import datetime
from app.services.whatsapp_service import send_text
import logging

logger = logging.getLogger("TempleBot")

registration_sessions = {}


def start_registration(phone, devotees_collection, send_main_menu):
    if devotees_collection.find_one({"phone": phone}):
        send_text(phone, "🙏 You are already registered.")
        send_main_menu(phone)
        return {"status": "already_registered"}

    registration_sessions[phone] = {"step": "name", "data": {}}
    send_text(phone, "📝 Enter Full Name:\n(Type 'cancel' anytime to stop)")
    return {"status": "registration_started"}


def handle_registration(phone, text, devotees_collection, send_main_menu):
    if text.lower() == "cancel":
        registration_sessions.pop(phone, None)
        send_text(phone, "Registration cancelled.")
        send_main_menu(phone)
        return {"status": "cancelled"}

    session = registration_sessions.get(phone)
    if not session:
        return {"status": "no_session"}

    step = session["step"]
    data = session["data"]

    if step == "name":
        data["name"] = text
        session["step"] = "gotram"
        send_text(phone, "Enter Gotram (or type no):")
        return

    if step == "gotram":
        data["gotram"] = text if text.lower() != "no" else "Not Provided"
        session["step"] = "address"
        send_text(phone, "Enter Address:")
        return

    if step == "address":
        data["address"] = text
        session["step"] = "mobile"
        send_text(phone, "Enter Mobile:")
        return

    if step == "mobile":
        data["mobile"] = text
        session["step"] = "email"
        send_text(phone, "Enter Email (or type no):")
        return

    if step == "email":
        data["email"] = text if text.lower() != "no" else "Not Provided"

        devotees_collection.insert_one({
            "phone": phone,
            "full_name": data["name"],
            "gotram": data["gotram"],
            "address": data["address"],
            "mobile": data["mobile"],
            "email": data["email"],
            "registered_at": datetime.utcnow()
        })

        registration_sessions.pop(phone, None)

        send_text(phone, "🎉 Registration Successful!\nMay Lord Shiva bless you 🙏")
        send_main_menu(phone)

        return {"status": "registered"}
