from datetime import datetime

def get_session(phone, sessions_collection):
    return sessions_collection.find_one({"phone": phone})


def set_language(phone, language, sessions_collection):
    sessions_collection.update_one(
        {"phone": phone},
        {
            "$set": {
                "language": language,
                "updated_at": datetime.utcnow()
            }
        },
        upsert=True
    )


def get_language(phone, sessions_collection):
    session = sessions_collection.find_one({"phone": phone})
    if session and "language" in session:
        return session["language"]
    return "en"
