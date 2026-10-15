import os
import requests
from fastapi import FastAPI, Request
from supabase import create_client

app = FastAPI()

def send_message(token: str, chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)

# Test w przeglądarce - pokaże Ci od razu, czy Vercel widzi Twoje zmienne środowiskowe!
@app.get("/")
@app.get("/api/index")
@app.get("/api/webhook")
def health_check():
    return {
        "status": "Serwer dziala!",
        "TELEGRAM_BOT_TOKEN_ustawiony": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
        "TELEGRAM_CHAT_ID_ustawiony": bool(os.environ.get("TELEGRAM_CHAT_ID")),
        "SUPABASE_URL_ustawiony": bool(os.environ.get("SUPABASE_URL")),
        "SUPABASE_KEY_ustawiony": bool(os.environ.get("SUPABASE_KEY")),
    }

@app.post("/")
@app.post("/api/index")
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        if not chat_id or not text:
            return {"status": "ignored"}

        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        supa_url = os.environ.get("SUPABASE_URL", "").strip()
        supa_key = os.environ.get("SUPABASE_KEY", "").strip()
        my_chat_id_str = os.environ.get("TELEGRAM_CHAT_ID", "0").strip()

        # Sprawdzamy autoryzację
        if str(chat_id) != my_chat_id_str:
            return {"status": "unauthorized"}

        # Jeśli brakuje kluczy Supabase na Vercelu, bot napisze Ci o tym na czacie!
        if not supa_url or not supa_key:
            send_message(token, chat_id, "⚠️ Błąd: Brakuje zmiennych SUPABASE_URL lub SUPABASE_KEY w ustawieniach Vercela!")
            return {"status": "missing_env"}

        # Tworzymy połączenie z bazą dopiero wewnątrz funkcji (bezpiecznie!)
        supabase = create_client(supa_url, supa_key)

        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].upper().strip() if len(parts) > 1 else ""

        if command == "/dodaj" and argument:
            try:
                supabase.table("companies").insert({"symbol": argument}).execute()
                send_message(token, chat_id, f"✅ Dodano spółkę *{argument}* do bazy Supabase!")
            except Exception:
                send_message(token, chat_id, f"ℹ️ Spółka *{argument}* już jest na liście.")

        elif command == "/usun" and argument:
            supabase.table("companies").delete().eq("symbol", argument).execute()
            send_message(token, chat_id, f"🗑️ Usunięto spółkę *{argument}* z bazy.")

        elif command == "/lista":
            response = supabase.table("companies").select("symbol").execute()
            companies_list = [row["symbol"] for row in response.data]
            spolki_tekst = ", ".join(companies_list) if companies_list else "Brak obserwowanych spółek."
            send_message(token, chat_id, f"📋 *Obserwowane spółki (z bazy):*\n{spolki_tekst}")

    except Exception as e:
        # Nawet jeśli cokolwiek pójdzie nie tak, nie zwracamy błędu 500!
        print(f"Błąd w webhooku: {e}")
        return {"status": "error", "details": str(e)}

    return {"status": "ok"}
