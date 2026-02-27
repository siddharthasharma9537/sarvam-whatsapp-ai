import os
import json
from datetime import datetime, date
import logging

logger = logging.getLogger("TempleBot")

SPECIAL_DAYS = []

try:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    FILE_PATH = os.path.join(BASE_DIR, "data", "special_days_2026.json")

    with open(FILE_PATH, "r", encoding="utf-8") as f:
        SPECIAL_DAYS = json.load(f)

    logger.info("Special days 2026 dataset loaded successfully (TithiService).")

except Exception as e:
    logger.error(f"Tithi dataset load failed: {e}")


def get_next_tithi(tithi_type: str):
    if not SPECIAL_DAYS:
        return None

    today = date.today()
    upcoming = []

    for event in SPECIAL_DAYS:

        if event.get("tithi_type") != tithi_type:
            continue

        try:
            event_date = datetime.strptime(
                event["date_iso"], "%Y-%m-%d"
            ).date()
        except Exception:
            continue

        if event_date >= today:
            upcoming.append((event_date, event))

    if not upcoming:
        return None

    upcoming.sort(key=lambda x: x[0])
    return upcoming[0][1]
