import requests
import logging
import os

logger = logging.getLogger("TempleBot")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

GRAPH_URL = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"


def normalize_phone(phone: str) -> str:
    phone = phone.replace("+", "")
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone


def whatsapp_request(payload: dict):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(GRAPH_URL, headers=headers, json=payload)

    logger.info(f"WhatsApp Status: {response.status_code}")
    logger.info(f"WhatsApp Response: {response.text}")

    return response


def send_text(phone: str, message: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    return whatsapp_request(payload)


def send_list(phone: str, text: str, rows: list):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": text},
            "action": {
                "button": "Select Option",
                "sections": [{
                    "title": "Temple Services",
                    "rows": rows
                }]
            }
        }
    }
    return whatsapp_request(payload)


def send_image(phone: str, image_url: str, caption: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {
            "link": image_url,
            "caption": caption
        }
    }
    return whatsapp_request(payload)
