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
# Opcjonalny klucz Groq (jeśli dodasz go w przyszłości do Secrets na GitHubie, zadziała jako turbodoładowany zapas)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

client = genai.Client(api_key=GEMINI_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- 2. FUNKCJE BAZY DANYCH ---
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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": False}, timeout=10)


# --- 3. POBIERANIE NEWSÓW I AI ---
def get_bankier_news_for_company(symbol, seen_links):
    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={symbol}"
    try:
        response = requests.get(profile_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
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
            if len(new_articles) >= 2:
                break

    return new_articles

def summarize_article(company, title, text):
    prompt = f"""
    Jesteś analitykiem giełdowym. Przygotuj krótkie podsumowanie artykułu ze strony Bankier.pl dotyczącego spółki {company}.
    Format odpowiedzi:
    - Sentyment dla {company}: [Pozytywny 🟢 / Neutralny 🟡 / Negatywny 🔴]
    - 2-3 krótkie podpunkty z najważniejszymi informacjami (liczby, fakty, decyzje).
    Tytuł: {title}
    Treść: {text[:5000]}
    """
    
    # 1. Próbujemy modeli Google Gemini z rosnącym czasem oczekiwania (Exponential Backoff)
    models = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.0-flash"]
    for model_name in models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                if response and response.text:
                    return response.text
            except Exception as e:
                print(f"Błąd modelu {model_name} (próba {attempt+1}): {e}")
                # Jeśli to błąd 404 (model niedostępny dla konta), od razu przechodzimy do kolejnego modelu
                if "404" in str(e):
                    break
                time.sleep(6 * (attempt + 1))

    # 2. Opcjonalny zapas przez Groq API (Llama 3.3 70B), jeśli dodasz GROQ_API_KEY
    if GROQ_API_KEY:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3
                },
                timeout=15
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Błąd Groq: {e}")

    return "⚠️ _Serwery AI są chwilowo przeciążone – kliknij w link poniżej, aby przeczytać całość._"


# --- 4. GŁÓWNA PĘTLA PROGRAMU ---
def main():
    companies = get_companies()
    seen_links = get_seen_links()
    sent_count = 0
    
    for company in companies:
        articles = get_bankier_news_for_company(company, seen_links)
        
        for article in articles:
            downloaded = trafilatura.fetch_url(article["url"])
            article_text = trafilatura.extract(downloaded) if downloaded else article["title"]
            
            summary = summarize_article(company, article["title"], article_text or article["title"])
            msg = f"🏢 *[{company}]* {article['title']}\n\n{summary}\n\n🔗 [Czytaj na Bankier.pl]({article['url']})"
            
            send_telegram_message(msg)
            save_seen_link(article["url"])
            sent_count += 1
            
            # Krótka przerwa między artykułami, żeby nie przekroczyć darmowego limitu zapytań na minutę w AI!
            time.sleep(4)

    # Jeśli po sprawdzeniu wszystkich spółek nie było żadnego nowego artykułu, dajemy znać na czacie:
    if sent_count == 0:
        spolki_str = ", ".join(companies) if companies else "brak"
        send_telegram_message(f"📭 *Raport kontrolny:* Sprawdzono Bankier.pl dla ({spolki_str}) – brak nowych artykułów.")

if __name__ == "__main__":
    main()
