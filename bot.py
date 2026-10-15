import os
import time
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

client = genai.Client(api_key=GEMINI_KEY)
# Łączymy się z bazą danych
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- 2. FUNKCJE BAZY DANYCH (Zamiast pliku state.json) ---
def get_companies():
    """Pobiera listę obserwowanych spółek z bazy"""
    response = supabase.table("companies").select("symbol").execute()
    return [row["symbol"] for row in response.data]

def get_seen_links():
    """Pobiera 200 ostatnich linków z bazy, żeby nie wysyłać ich ponownie"""
    response = supabase.table("seen_links").select("url").order("id", desc=True).limit(200).execute()
    return [row["url"] for row in response.data]

def save_seen_link(url):
    """Zapisuje nowy link do bazy"""
    try:
        supabase.table("seen_links").insert({"url": url}).execute()
    except Exception as e:
        pass # Jeśli link już jest (dzięki regule UNIQUE w SQL), po prostu to ignorujemy


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": False})


# --- 3. POBIERANIE NEWSÓW I AI ---
def get_bankier_news_for_company(symbol, seen_links):
    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={symbol}"
    try:
        response = requests.get(profile_url, headers=HEADERS, timeout=10)
        if response.status_code != 200: return []
    except Exception:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    new_articles = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        title = a_tag.get_text(strip=True)

        if "/wiadomosc/" in href and href.endswith(".html") and len(title) > 15:
            full_url = href if href.startswith("http") else f"https://www.bankier.pl{href}"

            if full_url not in seen_links:
                new_articles.append({"title": title, "url": full_url})
                seen_links.append(full_url)
            if len(new_articles) >= 2: break

    return new_articles

def summarize_article(company, title, text):
    prompt = f"""
    Jesteś analitykiem giełdowym. Przygotuj krótkie podsumowanie artykułu ze strony Bankier.pl dotyczącego spółki {company}.
    Format odpowiedzi:
    - Sentyment dla {company}: [Pozytywny 🟢 / Neutralny 🟡 / Negatywny 🔴]
    - 2-3 krótkie podpunkty z najważniejszymi informacjami.
    Tytuł: {title}\nTreść: {text[:6000]}
    """
    
    models = ["gemini-3.8-flash", "gemini-3.8-flash-lite"]
    for model_name in models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                return response.text
            except Exception:
                time.sleep(5)
    return "⚠️ _Serwery AI są chwilowo przeciążone – kliknij w link poniżej, aby przeczytać całość._"


# --- 4. GŁÓWNA PĘTLA PROGRAMU ---
def main():
    # Zamiast czytać plik state.json, pobieramy dane na żywo z Supabase
    companies = get_companies()
    seen_links = get_seen_links()
    
    for company in companies:
        articles = get_bankier_news_for_company(company, seen_links)
        
        for article in articles:
            downloaded = trafilatura.fetch_url(article["url"])
            article_text = trafilatura.extract(downloaded) if downloaded else article["title"]
            
            summary = summarize_article(company, article["title"], article_text or article["title"])
            msg = f"🏢 *[{company}]* {article['title']}\n\n{summary}\n\n🔗 [Czytaj na Bankier.pl]({article['url']})"
            
            send_telegram_message(msg)
            
            # Gdy wyślemy wiadomość, od razu zapisujemy link w bazie SQL!
            save_seen_link(article["url"])

if __name__ == "__main__":
    main()
