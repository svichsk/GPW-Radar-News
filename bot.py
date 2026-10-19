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
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# Słownik automatycznej korekty częstych nazw na oficjalne symbole Bankier.pl / GPW
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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": False},
        timeout=10
    )


# --- 3. PRECYZYJNE POBIERANIE NEWSÓW Z BANKIER.PL ---
def get_bankier_news_for_company(raw_symbol, seen_links):
    # Jeśli wpisano np. CYBERFOLKS, słownik automatycznie zamieni to na CYBERFLKS
    symbol = SYMBOL_ALIASES.get(raw_symbol.upper(), raw_symbol.upper())
    profile_url = f"https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={symbol}"
    
    try:
        response = requests.get(profile_url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return [], symbol, False
    except Exception:
        return [], symbol, False

    soup = BeautifulSoup(response.text, "html.parser")

    # ZABEZPIECZENIE 1: Sprawdzamy, czy strona faktycznie jest profilem spółki!
    # Każda strona notowań na Bankierze ma div z klasą "profilLast" (aktualny kurs) lub id="boxProfilHeader"
    if not soup.find("div", class_="profilLast") and not soup.find(id="boxProfilHeader"):
        print(f"UWAGA: Symbol {symbol} nie istnieje na Bankier.pl!")
        return [], symbol, False

    # ZABEZPIECZENIE 2: Szukamy wyłącznie w sekcjach wiadomości o spółce, ignorując pasek boczny!
    # Najpierw usuwamy z kodu HTML paski boczne, stopkę i nawigację, żeby nie łapać losowych newsów z kraju
    for aside in soup.find_all(["aside", "footer", "nav"]):
        aside.decompose()

    # Szukamy boksów należących bezpośrednio do profilu spółki (Wiadomości ze spółki / Komunikaty ESPI)
    search_containers = []
    for box in soup.find_all("div", class_="box"):
        header_text = box.get_text(" ", strip=True).lower()
        if "wiadomości" in header_text or "komunikaty" in header_text or "espi" in header_text:
            # Ignorujemy ogólne boksy redakcyjne typu "Najnowsze wiadomości" z prawej kolumny
            if "najnowsze wiadomości" not in header_text and "polecane" not in header_text:
                search_containers.append(box)

    # Jeśli Bankier zmienił nazwę boksu, bierzemy główną kolumnę profilu (bez prawej kolumny bocznej)
    if not search_containers:
        main_col = soup.find("div", id="boxProfil") or soup
        search_containers = [main_col]

    new_articles = []
    for container in search_containers:
        for a_tag in container.find_all("a", href=True):
            href = a_tag["href"]
            title = a_tag.get_text(strip=True)

            if "/wiadomosc/" in href and href.endswith(".html") and len(title) > 15:
                full_url = href if href.startswith("http") else f"https://www.bankier.pl{href}"

                if full_url not in seen_links:
                    new_articles.append({"title": title, "url": full_url})
                    seen_links.append(full_url)
                if len(new_articles) >= 2:
                    break
        if len(new_articles) >= 2:
            break

    return new_articles, symbol, True


# --- 4. POBIERANIE TREŚCI I DATY PUBLIKACJI ARTYKUŁU ---
def get_article_content_and_date(url, fallback_title):
    """Pobiera treść artykułu oraz dokładną datę jego publikacji."""
    article_date = "Brak daty"
    article_text = fallback_title

    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            # 1. Wyciągamy czysty tekst artykułu
            extracted_text = trafilatura.extract(downloaded)
            if extracted_text:
                article_text = extracted_text

            # 2. Próbujemy wyciągnąć dokładną datę i godzinę ze znaczników HTML Bankiera
            soup = BeautifulSoup(downloaded, "html.parser")
            meta_date = (
                soup.find("meta", property="article:published_time")
                or soup.find("meta", attrs={"name": "publish-date"})
                or soup.find("time")
            )
            if meta_date:
                raw_date = meta_date.get("content") or meta_date.get("datetime") or meta_date.get_text(strip=True)
                if raw_date:
                    # Zamieniamy format ISO "2026-10-19T14:35:00+02:00" na czytelne "2026-10-19 14:35"
                    clean_date = raw_date.replace("T", " ")[:16]
                    article_date = clean_date

            # 3. Jeśli znacznik HTML nie zadziałał, używamy ekstraktora metadanych z Trafilatury
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
    Format odpowiedzi:
    - Sentyment dla {company}: [Pozytywny 🟢 / Neutralny 🟡 / Negatywny 🔴]
    - 2-3 krótkie podpunkty z najważniejszymi informacjami (liczby, fakty, decyzje).
    Tytuł: {title}
    Treść: {text[:5000]}
    """
    
    # KROK A: Najpierw próbujemy Groq (Llama 3.3 70B) - jest ultraszybki i rzadko bywa przeciążony!
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

    # KROK B: Jeśli Groq nie zadziałał (lub brak klucza), próbujemy modeli Google Gemini
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

    return "⚠️ _Serwery AI są chwilowo przeciążone – kliknij w link poniżej, aby przeczytać całość._", "Brak (Przeciążenie API)"


# --- 6. GŁÓWNA PĘTLA PROGRAMU ---
def main():
    companies = get_companies()
    seen_links = get_seen_links()
    sent_count = 0
    
    for raw_company in companies:
        articles, valid_symbol, exists = get_bankier_news_for_company(raw_company, seen_links)
        
        # Jeśli wpisano nieistniejący symbol giełdowy, ostrzegamy na czacie!
        if not exists:
            send_telegram_message(
                f"⚠️ *Uwaga:* Nie znaleziono profilu spółki `{raw_company}` na Bankier.pl!\n"
                f"Usuń ją komendą `/usun {raw_company}` i sprawdź oficjalny skrót GPW (np. dla cyber_Folks to `CYBERFLKS`)."
            )
            continue

        for article in articles:
            # Pobieramy treść ORAZ datę publikacji artykułu
            article_text, article_date = get_article_content_and_date(article["url"], article["title"])
            
            # Pobieramy podsumowanie ORAZ nazwę modelu AI, który je stworzył
            summary, used_model = summarize_article(valid_symbol, article["title"], article_text)
            
            msg = (
                f"🏢 *[{valid_symbol}]* {article['title']}\n"
                f"📅 _Data publikacji: {article_date}_\n\n"
                f"{summary}\n\n"
                f"🤖 _Model AI: {used_model}_\n"
                f"🔗 [Czytaj na Bankier.pl]({article['url']})"
            )
            
            send_telegram_message(msg)
            save_seen_link(article["url"])
            sent_count += 1
            
            time.sleep(3)

    if sent_count == 0:
        spolki_str = ", ".join(companies) if companies else "brak"
        send_telegram_message(f"📭 *Raport kontrolny:* Sprawdzono Bankier.pl dla ({spolki_str}) – brak nowych artykułów.")

if __name__ == "__main__":
    main()
