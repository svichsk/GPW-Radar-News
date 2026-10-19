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

# Limit artykułów na jedno uruchomienie (chroni przed blokadą limitów darmowego API)
MAX_ARTICLES_PER_RUN = 6

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


# --- 2. AUTOMATYCZNE WYKRYWANIE DOSTĘPNYCH MODELI GROQ ---
def get_available_groq_models():
    """Odpytuje API Groq o aktualną listę działających modeli tekstowych dla Twojego klucza."""
    if not GROQ_API_KEY:
        print("Brak klucza GROQ_API_KEY w środowisku.")
        return []

    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "User-Agent": HEADERS["User-Agent"]},
            timeout=10
        )
        if r.status_code != 200:
            print(f"Nie udało się pobrać listy modeli Groq ({r.status_code}): {r.text}")
            return []

        all_models = [m["id"] for m in r.json().get("data", [])]
        print(f"Wszystkie modele na Twoim koncie Groq: {all_models}")

        # Odrzucamy modele do audio (whisper, tts), moderacji (guard) i embeddingów
        excluded_keywords = ["whisper", "tts", "guard", "embed", "playai", "orpheus", "vision", "safeguard"]
        chat_models = [
            m for m in all_models
            if not any(bad in m.lower() for bad in excluded_keywords)
        ]

        # Sortujemy tak, aby modele z rodziny llama, qwen, deepseek lub gemma były na pierwszym miejscu
        preferred = [m for m in chat_models if any(p in m.lower() for p in ["llama", "qwen", "deepseek", "gemma"])]
        others = [m for m in chat_models if m not in preferred]
        
        final_list = preferred + others
        print(f"Wybrane modele tekstowe Groq do użycia: {final_list[:4]}")
        return final_list[:4]
    except Exception as e:
        print(f"Błąd podczas pobierania listy modeli Groq: {e}")
        return []

# Pobieramy listę aktywnych modeli Groq raz przy starcie bota
ACTIVE_GROQ_MODELS = get_available_groq_models()


# --- 3. FUNKCJE BAZY DANYCH I TELEGRAMA ---
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
    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=10
    )
    if r.status_code == 200:
        return True

    r_fallback = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
        timeout=10
    )
    return r_fallback.status_code == 200


# --- 4. POBIERANIE NEWSÓW Z BANKIER.PL ---
def get_bankier_news_for_company(raw_symbol, seen_links):
    symbol = SYMBOL_ALIASES.get(raw_symbol.upper(), raw_symbol.upper())
    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={symbol}"
    
    try:
        response = requests.get(profile_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return [], symbol
    except Exception as e:
        print(f"Błąd połączenia z Bankierem dla {symbol}: {e}")
        return [], symbol

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup.find_all(["aside", "footer", "nav"]):
        tag.decompose()

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
    return new_articles, symbol


# --- 5. POBIERANIE TREŚCI I DATY PUBLIKACJI ARTYKUŁU ---
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


# --- 6. PODSUMOWANIE AI (DYNAMICZNY GROQ + AKTUALNE GEMINI 3.8) ---
def summarize_article(company, title, text):
    prompt = f"""
    Jesteś analitykiem giełdowym. Przygotuj krótkie podsumowanie artykułu ze strony Bankier.pl dotyczącego spółki {company}.
    Odpowiedz wyłącznie po polsku. Format odpowiedzi (nie używaj gwiazdek do pogrubiania, używaj myślników):
    - Sentyment dla {company}: [Pozytywny 🟢 / Neutralny 🟡 / Negatywny 🔴]
    - 2-3 krótkie podpunkty z najważniejszymi informacjami (liczby, fakty, decyzje).
    Tytuł: {title}
    Treść: {text[:4500]}
    """
    
    # KROK A: Próbujemy automatycznie wykrytych modeli z Twojego konta Groq
    for groq_model in ACTIVE_GROQ_MODELS:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": HEADERS["User-Agent"]
                },
                json={
                    "model": groq_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3
                },
                timeout=15
            )
            if r.status_code == 200:
                summary_text = r.json()["choices"][0]["message"]["content"]
                # Niektóre modele typu reasoning (np. DeepSeek/Qwen) dodają znaczniki <think>...</think>, które odcinamy
                if "</think>" in summary_text:
                    summary_text = summary_text.split("</think>")[-1].strip()
                return summary_text, f"Groq ({groq_model})"
            else:
                print(f"Groq ({groq_model}) zwrócił status {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Wyjątek Groq ({groq_model}): {e}")

    # KROK B: Zapasowo próbujemy aktualnych modeli z rodziny Gemini 3.8 (w tym wersji lite i pro)
    gemini_models = ["gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-3.8-pro"]
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
                time.sleep(6 * (attempt + 1))

    return "⚠️ Serwery AI są chwilowo przeciążone – kliknij w link poniżej, aby przeczytać całość.", "Brak (Przeciążenie API)"


# --- 7. GŁÓWNA PĘTLA PROGRAMU ---
def main():
    companies = get_companies()
    seen_links = get_seen_links()
    sent_count = 0
    print(f"Start bota. Obserwowane spółki: {companies}")
    
    for raw_company in companies:
        if sent_count >= MAX_ARTICLES_PER_RUN:
            print("Osiągnięto limit artykułów na jedno uruchomienie – reszta w kolejnym cyklu.")
            break

        articles, valid_symbol = get_bankier_news_for_company(raw_company, seen_links)

        for article in articles:
            if sent_count >= MAX_ARTICLES_PER_RUN:
                break

            article_text, article_date = get_article_content_and_date(article["url"], article["title"])
            summary, used_model = summarize_article(valid_symbol, article["title"], article_text)
            
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
            
            if send_telegram_message(msg):
                save_seen_link(article["url"])
                sent_count += 1
            
            time.sleep(4)

    if sent_count == 0:
        spolki_str = ", ".join(companies) if companies else "brak"
        send_telegram_message(f"📭 <b>Raport kontrolny:</b> Sprawdzono Bankier.pl dla ({html.escape(spolki_str)}) – brak nowych artykułów.")

if __name__ == "__main__":
    main()
