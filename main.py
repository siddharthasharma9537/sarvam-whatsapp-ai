from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import requests

app = FastAPI()

# Your existing tokens (unchanged)
VERIFY_TOKEN = "siddharth_verify_token"
WHATSAPP_TOKEN = "EAANWTaYRRcwBQpW2H2ZClGjeVvVKjCfZBNyc8qWCuPl1AYsBcHl5BZCa5ERxEtsrIVqurBKBX4TW3lqWnkgWwfsrICwi16YWRZAwPPuBLdPtYUxNxTJgZBiG21C7AJ8pPNXaZAIegbjuZCZBAFgEiW1bzTCCAiHeErkYSUgg5nZC1a8dJEcz6RZBbbsv77d3NDQTLvs6al7t81aS74JiodA7rX9cOC6gwiWYLnUKdklVL7JjZA0ZArWlp70nhWJCf9GgLRZASLeFMUXtEU9fosKdkzOOxmO7nrWCJ"
PHONE_NUMBER_ID = "1005546725973223"

# Add your Sarvam API key here
SARVAM_API_KEY = "sk_hd62veik_OvDhMIJXYoUfTPSa5DSdRJVj"


@app.get("/webhook")
async def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"]
        sender = data["entry"][0]["changes"][0]["value"]["messages"][0]["from"]

        # Generate AI reply using Sarvam
        reply = sarvam_reply(message)

        send_whatsapp(sender, reply)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}


def sarvam_reply(user_message):

    url = "https://api.sarvam.ai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {SARVAM_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "sarvam-m",
        "messages": [
            {
    "role": "system",
    "content": """
You are the official AI assistant of Sri Parvathi Jadala Ramalingeshwara Swamy Devasthanam.

Temple details:
Name: Sri Parvathi Jadala Ramalingeshwara Swamy Devasthanam
Location: Cheruvugattu, Narketpally Mandal, Nalgonda District, Telangana, India

Temple timings:

Sunday:
Morning: 5:00 AM – 12:30 PM
Evening: 3:00 PM – 7:00 PM

Monday:
Morning: 5:00 AM – 1:00 PM
Evening: 3:00 PM – 7:30 PM

Tuesday:
Morning: 5:00 AM – 12:30 PM
Evening: 3:00 PM – 7:00 PM

Wednesday:
Morning: 5:00 AM – 12:30 PM
Evening: 3:00 PM – 7:00 PM

Thursday:
Morning: 5:00 AM – 12:30 PM
Evening: 3:00 PM – 7:00 PM

Friday:
Morning: 5:00 AM – 1:00 PM
Evening: 3:00 PM – 7:30 PM

Saturday:
Morning: 5:00 AM – 12:30 PM
Evening: 3:00 PM – 7:00 PM

Instructions:
- If devotee asks temple timings, give correct timings based on day.
- If devotee speaks Telugu, reply in Telugu.
- If devotee speaks English, reply in English.
- Be respectful and devotional.
- Help devotees with darshan, location, and temple information.
"""
            },
            {
                "role": "user",
                "content": user_message
            }
        ]
    }

    response = requests.post(url, headers=headers, json=data)

    if response.status_code == 200:
        result = response.json()
        return result["choices"][0]["message"]["content"]
    else:
        print("Sarvam error:", response.text)
        return "క్షమించండి, ప్రస్తుతం సమాధానం ఇవ్వలేకపోతున్నాను. Please try again."


def send_whatsapp(to, message):

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    requests.post(url, headers=headers, json=data)
