import os
import json
import requests
from bs4 import BeautifulSoup
import trafilatura
from google import genai
import time

# --- 1. KONFIGURACJA I ZMIENNE ŚRODOWISKOWE ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
GEMINI_KEY = os.environ["GEMINI_API_KEY"]

STATE_FILE = "state.json"

# Udajemy zwykłą przeglądarkę, aby serwery Bankiera nie odrzucały naszych zapytań
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

client = genai.Client(api_key=GEMINI_KEY)


# --- 2. OBSŁUGA STANU (NASZEJ MINI-BAZY DANYCH) ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_update_id": 0, "companies": ["CDPROJEKT", "PKNORLEN"], "seen_links": []}


def save_state(state):
    # Ograniczamy pamięć do 500 ostatnich linków, żeby plik nie puchł w nieskończoność
    state["seen_links"] = state["seen_links"][-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    requests.post(url, json=payload, timeout=10)


# --- 3. OBSŁUGA KOMEND Z TELEGRAMA (/dodaj, /usun, /lista) ---
def process_telegram_commands(state):
    # Parametr offset mówi Telegramowi: "daj mi tylko wiadomości nowsze niż last_update_id"
    offset = state["last_update_id"] + 1
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    
    try:
        response = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
        data = response.json()
    except Exception as e:
        print(f"Błąd podczas pobierania komend z Telegrama: {e}")
        return

    if not data.get("ok"):
        return

    for update in data.get("result", []):
        # Zawsze aktualizujemy ID ostatniego zdarzenia
        state["last_update_id"] = update["update_id"]
        
        message = update.get("message", {})
        sender_chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()

        # ZABEZPIECZENIE: Ignorujemy wiadomości od obcych ludzi, którzy znajdą naszego bota
        if sender_chat_id != CHAT_ID or not text:
            continue

        # Rozbijamy komendę na części, np. "/dodaj cdprojekt" -> ["/dodaj", "cdprojekt"]
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].upper().strip() if len(parts) > 1 else ""

        if command == "/dodaj" and argument:
            if argument not in state["companies"]:
                state["companies"].append(argument)
                send_telegram_message(f"✅ Dodano spółkę *{argument}* do obserwowanych.")
            else:
                send_telegram_message(f"ℹ️ Spółka *{argument}* jest już na liście.")

        elif command == "/usun" and argument:
            if argument in state["companies"]:
                state["companies"].remove(argument)
                send_telegram_message(f"🗑️ Usunięto spółkę *{argument}* z obserwowanych.")
            else:
                send_telegram_message(f"⚠️ Nie znaleziono spółki *{argument}* na liście.")

        elif command == "/lista":
            spolki = ", ".join(state["companies"]) if state["companies"] else "Brak"
            send_telegram_message(f"📋 *Obserwowane spółki:*\n{spolki}")


# --- 4. POBIERANIE NEWSÓW Z BANKIER.PL DLA DANEJ SPÓŁKI ---
def get_bankier_news_for_company(symbol, seen_links):
    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={symbol}"
    
    try:
        response = requests.get(profile_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
    except Exception as e:
        print(f"Błąd pobierania profilu {symbol}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    new_articles = []

    # Szukamy wszystkich linków <a> na stronie profilu spółki
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)

        # Artykuły na Bankierze mają w adresie "/wiadomosc/" i kończą się na ".html"
        if "/wiadomosc/" in href and href.endswith(".html") and len(title) > 15:
            full_url = href if href.startswith("http") else f"https://www.bankier.pl{href}"

            if full_url not in seen_links:
                new_articles.append({"title": title, "url": full_url})
                # Dodajemy od razu do zbioru, aby uniknąć duplikatów na tej samej stronie
                seen_links.append(full_url)

            # Maksymalnie 2 najnowsze artykuły na spółkę podczas jednego uruchomienia
            if len(new_articles) >= 2:
                break

    return new_articles


# --- 5. PODSUMOWANIE AI I GŁÓWNA PĘTLA PROGRAMU ---
def summarize_article(company, title, text):
    prompt = f"""
    Jesteś analitykiem giełdowym. Przygotuj krótkie podsumowanie artykułu ze strony Bankier.pl dotyczącego spółki {company}.
    Format odpowiedzi:
    - Sentyment dla {company}: [Pozytywny 🟢 / Neutralny 🟡 / Negatywny 🔴]
    - 2-3 krótkie podpunkty z najważniejszymi informacjami (liczby, wyniki, decyzje).
    
    Tytuł: {title}
    Treść: {text[:6000]}
    """
    
    # Lista modeli do wypróbowania po kolei (gdyby pierwszy był przeciążony)
    models_to_try = ["gemini-3.8-flash", "gemini-3.8-flash-lite"]
    
    for model_name in models_to_try:
        for attempt in range(3):  # 3 próby dla każdego modelu
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response.text
            except Exception as e:
                print(f"Próba {attempt + 1} dla {model_name} nieudana: {e}")
                time.sleep(5)  # Czekamy 5 sekund przed kolejną próbą
                
    # Jeśli po wszystkich próbach serwery Google nadal leżą, zwracamy komunikat awaryjny zamiast wywalać bota!
    return "⚠️ _Serwery AI są chwilowo przeciążone – kliknij w link poniżej, aby przeczytać pełny artykuł._"


def main():
    state = load_state()
    
    # Krok A: Najpierw sprawdzamy, czy wysłałeś nowe komendy na Telegramie
    process_telegram_commands(state)
    
    # Krok B: Przechodzimy przez aktualną listę obserwowanych spółek
    for company in state["companies"]:
        articles = get_bankier_news_for_company(company, state["seen_links"])
        
        for article in articles:
            downloaded = trafilatura.fetch_url(article["url"])
            article_text = trafilatura.extract(downloaded) if downloaded else article["title"]
            
            summary = summarize_article(company, article["title"], article_text or article["title"])
            
            msg = f"🏢 *[{company}]* {article['title']}\n\n{summary}\n\n🔗 [Czytaj na Bankier.pl]({article['url']})"
            send_telegram_message(msg)

    # Krok C: Zapisujemy zaktualizowany stan do pliku state.json
    save_state(state)


if __name__ == "__main__":
    main()
