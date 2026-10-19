import os
import time
import html
import requests
from bs4 import BeautifulSoup
import trafilatura
from google import genai
from supabase import create_client, Client

# --- 1. KONFIGURACJA ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

SYMBOL_ALIASES = {
    "CYBERFOLKS": "CYBERFLKS",
    "CYBER_FOLKS": "CYBERFLKS",
    "CBF": "CYBERFLKS",
    "CDR": "CDPROJEKT",
    "ORLEN": "PKNORLEN",
    "PKO": "PKOBP",
    "DINO": "DINOPL",
    "ASSECO": "ASSECOPOL"
}

client = genai.Client(api_key=GEMINI_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- 2. FUNKCJE BAZY DANYCH I TELEGRAMA ---
def get_companies():
    response = supabase.table("companies").select("symbol").execute()
    return [row["symbol"] for row in response.data]

def get_seen_links():
    response = supabase.table("seen_links").select("url").order("id", desc=True).limit(300).execute()
    return [row["url"] for row in response.data]

def save_seen_link(url):
    try:
        supabase.table("seen_links").insert({"url": url}).execute()
    except Exception as e:
        print(f"Info przy zapisie linku: {e}")

def send_telegram_message(text):
    """Wysyła wiadomość w trybie HTML (odpornym na znaki _ i *). Zwraca True, jeśli się udało."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Próba 1: Wysyłka z eleganckim formatowaniem HTML
    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=10
    )
    if r.status_code == 200:
        return True

    print(f"Błąd formatowania HTML Telegrama ({r.status_code}): {r.text}. Ponawiam jako zwykły tekst...")
    
    # Próba 2 (Awaryjna): Jeśli cokolwiek w tekście przeszkadza Telegramowi, wysyłamy bez parse_mode
    r_fallback = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
        timeout=10
    )
    if r_fallback.status_code == 200:
        return True

    print(f"Krytyczny błąd wysyłki na Telegram: {r_fallback.text}")
    return False


# --- 3. PRECYZYJNE POBIERANIE NEWSÓW Z BANKIER.PL ---
def get_bankier_news_for_company(raw_symbol, seen_links):
    symbol = SYMBOL_ALIASES.get(raw_symbol.upper(), raw_symbol.upper())
    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={symbol}"
    
    try:
        response = requests.get(profile_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return [], symbol, False
    except Exception as e:
        print(f"Błąd połączenia z Bankierem dla {symbol}: {e}")
        return [], symbol, False

    soup = BeautifulSoup(response.text, "html.parser")

    # 1. Sprawdzamy, czy profil spółki faktycznie istnieje na Bankierze
    if not soup.find("div", class_="profilLast") and not soup.find(id="boxProfilHeader"):
        print(f"UWAGA: Symbol {symbol} nie istnieje na Bankier.pl!")
        return [], symbol, False

    # 2. Usuwamy z drzewa HTML paski boczne, stopkę oraz ramki z ogólnymi wiadomościami z kraju
    for tag in soup.find_all(["aside", "footer", "nav"]):
        tag.decompose()

    for box in soup.find_all("div", class_="box"):
        header = box.find(["h2", "h3", "div"], class_=lambda c: c and "header" in c.lower()) or box.find(["h2", "h3"])
        if header:
            h_text = header.get_text(" ", strip=True).lower()
            # Jeśli nagłówek ramki to ogólne "Najnowsze wiadomości" lub "Polecane", usuwamy tę ramkę
            if "najnowsze wiadomości" in h_text or "polecane" in h_text or "wiadomości dnia" in h_text:
                box.decompose()

    # 3. Zbieramy artykuły z oczyszczonej strony profilu spółki
    new_articles = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)

        if "/wiadomosc/" in href and href.endswith(".html") and len(title) > 15:
            full_url = href if href.startswith("http") else f"https://www.bankier.pl{href}"

            if full_url not in seen_links:
                new_articles.append({"title": title, "url": full_url})
                seen_links.append(full_url)
            if len(new_articles) >= 2:
                break

    print(f"Spółka {symbol}: znaleziono {len(new_articles)} nowych artykułów.")
    return new_articles, symbol, True


# --- 4. POBIERANIE TREŚCI I DATY PUBLIKACJI ARTYKUŁU ---
def get_article_content_and_date(url, fallback_title):
    article_date = "Brak daty"
    article_text = fallback_title

    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            extracted_text = trafilatura.extract(downloaded)
            if extracted_text:
                article_text = extracted_text

            soup = BeautifulSoup(downloaded, "html.parser")
            meta_date = (
                soup.find("meta", property="article:published_time")
                or soup.find("meta", attrs={"name": "publish-date"})
                or soup.find("time")
            )
            if meta_date:
                raw_date = meta_date.get("content") or meta_date.get("datetime") or meta_date.get_text(strip=True)
                if raw_date:
                    article_date = raw_date.replace("T", " ")[:16]

            if article_date == "Brak daty":
                metadata = trafilatura.extract_metadata(downloaded)
                if metadata and metadata.date:
                    article_date = str(metadata.date)
    except Exception as e:
        print(f"Błąd pobierania treści/daty artykułu: {e}")

    return article_text, article_date


# --- 5. PODSUMOWANIE AI (ZWRACA TEKST ORAZ NAZWĘ MODELU) ---
def summarize_article(company, title, text):
    prompt = f"""
    Jesteś analitykiem giełdowym. Przygotuj krótkie podsumowanie artykułu ze strony Bankier.pl dotyczącego spółki {company}.
    Format odpowiedzi (nie używaj gwiazdek do pogrubiania, używaj myślników):
    - Sentyment dla {company}: [Pozytywny 🟢 / Neutralny 🟡 / Negatywny 🔴]
    - 2-3 krótkie podpunkty z najważniejszymi informacjami (liczby, fakty, decyzje).
    Tytuł: {title}
    Treść: {text[:5000]}
    """
    
    # KROK A: Najpierw próbujemy Groq (Llama 3.3 70B)
    if GROQ_API_KEY:
        groq_model = "llama-3.3-70b-versatile"
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": groq_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3
                },
                timeout=15
            )
            if r.status_code == 200:
                summary_text = r.json()["choices"][0]["message"]["content"]
                return summary_text, f"Groq ({groq_model})"
            else:
                print(f"Groq zwrócił status {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Błąd połączenia z Groq: {e}")

    # KROK B: Zapasowo modele Google Gemini
    gemini_models = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.0-flash"]
    for model_name in gemini_models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                if response and response.text:
                    return response.text, f"Google ({model_name})"
            except Exception as e:
                print(f"Błąd modelu {model_name} (próba {attempt+1}): {e}")
                if "404" in str(e):
                    break
                time.sleep(5 * (attempt + 1))

    return "⚠️ Serwery AI są chwilowo przeciążone – kliknij w link poniżej, aby przeczytać całość.", "Brak (Przeciążenie API)"


# --- 6. GŁÓWNA PĘTLA PROGRAMU ---
def main():
    companies = get_companies()
    seen_links = get_seen_links()
    sent_count = 0
    print(f"Start bota. Obserwowane spółki: {companies}")
    
    for raw_company in companies:
        articles, valid_symbol, exists = get_bankier_news_for_company(raw_company, seen_links)
        
        if not exists:
            send_telegram_message(
                f"⚠️ <b>Uwaga:</b> Nie znaleziono profilu spółki <code>{html.escape(raw_company)}</code> na Bankier.pl!\n"
                f"Usuń ją komendą /usun {html.escape(raw_company)} i sprawdź oficjalny skrót GPW (np. dla cyber_Folks to CYBERFLKS)."
            )
            continue

        for article in articles:
            article_text, article_date = get_article_content_and_date(article["url"], article["title"])
            summary, used_model = summarize_article(valid_symbol, article["title"], article_text)
            
            # html.escape() zabezpiecza znaki specjalne (<, >, &), żeby HTML w Telegramie nigdy się nie zepsuł
            safe_title = html.escape(article["title"])
            safe_summary = html.escape(summary).replace("**", "")
            safe_model = html.escape(used_model)
            
            msg = (
                f"🏢 <b>[{valid_symbol}] {safe_title}</b>\n"
                f"📅 <i>Data publikacji: {article_date}</i>\n\n"
                f"{safe_summary}\n\n"
                f"🤖 <i>Model AI: {safe_model}</i>\n"
                f"🔗 <a href=\"{article['url']}\">Czytaj na Bankier.pl</a>"
            )
            
            # Zapisujemy link w bazie TYLKO wtedy, gdy Telegram potwierdzi wysłanie wiadomości!
            if send_telegram_message(msg):
                save_seen_link(article["url"])
                sent_count += 1
            
            time.sleep(3)

    if sent_count == 0:
        spolki_str = ", ".join(companies) if companies else "brak"
        send_telegram_message(f"📭 <b>Raport kontrolny:</b> Sprawdzono Bankier.pl dla ({html.escape(spolki_str)}) – brak nowych artykułów.")

if __name__ == "__main__":
    main()
