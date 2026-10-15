import os
import requests
from fastapi import FastAPI, Request
from supabase import create_client

app = FastAPI()

# Lista najpopularniejszych spółek z GPW podpowiadanych pod przyciskami
POPULAR_COMPANIES = [
    "CDPROJEKT", "PKNORLEN", "PKOBP", "PEKAO",
    "PZU", "KGHM", "ALLEGRO", "DINOPL",
    "XTB", "LPP", "KRUK", "ZABKA"
]

def send_message(token: str, chat_id: int, text: str, reply_markup: dict = None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload, timeout=10)

def answer_callback(token: str, callback_id: str, text: str):
    """Zamyka animację ładowania na przycisku po kliknięciu"""
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_id, "text": text}, timeout=10)

@app.get("/")
@app.get("/api/index")
@app.get("/api/webhook")
def health_check():
    return {"status": "Serwer dziala!"}

@app.post("/")
@app.post("/api/index")
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        supa_url = os.environ.get("SUPABASE_URL", "").strip()
        supa_key = os.environ.get("SUPABASE_KEY", "").strip()
        my_chat_id_str = os.environ.get("TELEGRAM_CHAT_ID", "0").strip()

        supabase = create_client(supa_url, supa_key)

        # --- A. OBSŁUGA KLIKNIĘCIA W PRZYCISK (Callback Query) ---
        if "callback_query" in data:
            cb = data["callback_query"]
            cb_id = cb["id"]
            chat_id = cb.get("message", {}).get("chat", {}).get("id")
            cb_data = cb.get("data", "")

            if str(chat_id) != my_chat_id_str:
                return {"status": "unauthorized"}

            if cb_data.startswith("ADD:"):
                symbol = cb_data.split(":", 1)[1]
                try:
                    supabase.table("companies").insert({"symbol": symbol}).execute()
                    answer_callback(token, cb_id, f"Dodano {symbol}!")
                    send_message(token, chat_id, f"✅ Dodano spółkę *{symbol}* do obserwowanych!")
                except Exception:
                    answer_callback(token, cb_id, f"{symbol} już jest na liście!")
                    send_message(token, chat_id, f"ℹ️ Spółka *{symbol}* już jest na liście.")

            elif cb_data.startswith("DEL:"):
                symbol = cb_data.split(":", 1)[1]
                supabase.table("companies").delete().eq("symbol", symbol).execute()
                answer_callback(token, cb_id, f"Usunięto {symbol}!")
                send_message(token, chat_id, f"🗑️ Usunięto spółkę *{symbol}* z bazy.")

            return {"status": "ok"}

        # --- B. OBSŁUGA ZWYKŁYCH WIADOMOŚCI TEKSTOWYCH ---
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        if not chat_id or not text or str(chat_id) != my_chat_id_str:
            return {"status": "ignored"}

        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].upper().strip() if len(parts) > 1 else ""

        if command == "/dodaj":
            if argument:
                try:
                    supabase.table("companies").insert({"symbol": argument}).execute()
                    send_message(token, chat_id, f"✅ Dodano spółkę *{argument}* do bazy!")
                except Exception:
                    send_message(token, chat_id, f"ℹ️ Spółka *{argument}* już jest obserwowana.")
            else:
                # Budujemy siatkę przycisków (po 3 w rzędzie)
                keyboard = []
                row = []
                for comp in POPULAR_COMPANIES:
                    row.append({"text": f"➕ {comp}", "callback_data": f"ADD:{comp}"})
                    if len(row) == 3:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)

                send_message(
                    token,
                    chat_id,
                    "👇 *Kliknij spółkę, aby dodać ją do obserwowanych*\n_(lub wpisz ręcznie np._ `/dodaj ASSECO`_)_:",
                    reply_markup={"inline_keyboard": keyboard}
                )

        elif command == "/usun":
            if argument:
                supabase.table("companies").delete().eq("symbol", argument).execute()
                send_message(token, chat_id, f"🗑️ Usunięto spółkę *{argument}* z bazy.")
            else:
                # Pobieramy aktualne spółki z bazy i robimy z nich przyciski do usunięcia
                response = supabase.table("companies").select("symbol").execute()
                companies_list = [r["symbol"] for r in response.data]
                if not companies_list:
                    send_message(token, chat_id, "Brak spółek do usunięcia.")
                else:
                    keyboard = [[{"text": f"❌ Usuń {c}", "callback_data": f"DEL:{c}"}] for c in companies_list]
                    send_message(
                        token,
                        chat_id,
                        "👇 *Kliknij spółkę, którą chcesz usunąć z listy:*",
                        reply_markup={"inline_keyboard": keyboard}
                    )

        elif command == "/lista":
            response = supabase.table("companies").select("symbol").execute()
            companies_list = [row["symbol"] for row in response.data]
            spolki_tekst = ", ".join(companies_list) if companies_list else "Brak obserwowanych spółek."
            send_message(token, chat_id, f"📋 *Obserwowane spółki:*\n{spolki_tekst}")

    except Exception as e:
        print(f"Błąd w webhooku: {e}")
        return {"status": "error"}

    return {"status": "ok"}
