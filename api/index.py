import os
import requests
from fastapi import FastAPI, Request
from supabase import create_client, Client

# Vercel automatycznie uruchomi tę aplikację
app = FastAPI()

# Pobieramy klucze z ukrytych zmiennych (dodamy je za chwilę w Vercelu)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
MY_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", 0))

# Podłączamy się do naszej bazy danych
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_message(chat_id: int, text: str):
    """Funkcja odsyłająca wiadomości na Telegrama"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

# TUTAJ DODALIŚMY DODATKOWE ŚCIEŻKI (KROK 2):
@app.post("/api/webhook")
@app.post("/api/index")
@app.post("/")
async def telegram_webhook(request: Request):
    """Główny endpoint - tutaj Telegram uderza z nową wiadomością"""
    try:
        data = await request.json()
    except Exception:
        return {"status": "error"}

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    # Ignorujemy, jeśli to nie jest tekst (np. ktoś wysłał zdjęcie)
    if not chat_id or not text:
        return {"status": "ignored"}

    # ZABEZPIECZENIE: Gadamy tylko z Tobą!
    if chat_id != MY_CHAT_ID:
        return {"status": "unauthorized"}

    # Rozpoznajemy komendy
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].upper().strip() if len(parts) > 1 else ""

    if command == "/dodaj" and argument:
        try:
            # Zapisujemy do Supabase (SQL w chmurze!)
            supabase.table("companies").insert({"symbol": argument}).execute()
            send_message(chat_id, f"✅ Dodano spółkę *{argument}* do bazy Supabase!")
        except Exception as e:
            # Jeśli wpiszesz dwa razy to samo, zadziała nasza reguła UNIQUE z Supabase
            send_message(chat_id, f"ℹ️ Spółka *{argument}* już jest obserwowana (lub wystąpił błąd).")
            
    elif command == "/usun" and argument:
        # Usuwamy z bazy
        supabase.table("companies").delete().eq("symbol", argument).execute()
        send_message(chat_id, f"🗑️ Usunięto spółkę *{argument}* z bazy.")
        
    elif command == "/lista":
        # Pobieramy listę (SELECT symbol FROM companies)
        response = supabase.table("companies").select("symbol").execute()
        companies_list = [row["symbol"] for row in response.data]
        sp
