import os
import json
from datetime import datetime
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from supabase import create_client

app = FastAPI()

# Najpopularniejsze spółki podpowiadane przy /dodaj
POPULAR_COMPANIES = [
    "CDPROJEKT", "PKNORLEN", "PKOBP", "PEKAO",
    "PZU", "KGHM", "ALLEGRO", "DINOPL",
    "XTB", "LPP", "KRUK", "ZABKA"
]

# Mapowanie nazw z Bankier.pl / GPW na tickery giełdowe (Yahoo Finance .WA)
GPW_TO_TICKER = {
    "PKNORLEN": "PKN.WA", "ORLEN": "PKN.WA", "PKN": "PKN.WA",
    "CDPROJEKT": "CDR.WA", "CDR": "CDR.WA",
    "PKOBP": "PKO.WA", "PKO": "PKO.WA",
    "PEKAO": "PEO.WA", "PEO": "PEO.WA",
    "PZU": "PZU.WA",
    "KGHM": "KGH.WA", "KGH": "KGH.WA",
    "ALLEGRO": "ALE.WA", "ALE": "ALE.WA",
    "DINOPL": "DNP.WA", "DINO": "DNP.WA", "DNP": "DNP.WA",
    "XTB": "XTB.WA",
    "LPP": "LPP.WA",
    "KRUK": "KRU.WA", "KRU": "KRU.WA",
    "ZABKA": "ZAB.WA", "ZAB": "ZAB.WA",
    "CYBERFLKS": "CBF.WA", "CYBERFOLKS": "CBF.WA", "CBF": "CBF.WA",
    "ASSECOPOL": "ACP.WA", "ASSECO": "ACP.WA", "ACP": "ACP.WA",
    "BUDIMEX": "BDX.WA", "BDX": "BDX.WA",
    "KETY": "KTY.WA", "KTY": "KTY.WA",
    "MBANK": "MBK.WA", "MBK": "MBK.WA",
    "SANTANDER": "SPL.WA", "SPL": "SPL.WA",
    "ALIOR": "ALR.WA", "ALR": "ALR.WA",
    "JSW": "JSW.WA",
    "PGE": "PGE.WA",
    "TAURONPE": "TPE.WA", "TPE": "TPE.WA",
    "ORANGEPL": "OPL.WA", "OPL": "OPL.WA",
    "CCC": "CCC.WA",
    "PEPCO": "PCO.WA", "PCO": "PCO.WA",
    "BENEFIT": "BFT.WA", "BFT": "BFT.WA",
    "SYNEKTIK": "SNT.WA", "SNT": "SNT.WA",
    "11BIT": "11B.WA", "11B": "11B.WA",
    "PLAYWAY": "PLW.WA", "PLW": "PLW.WA",
    "TEN": "TEN.WA",
    "HUUUGE": "HUG.WA", "HUG": "HUG.WA",
    "GPW": "GPW.WA",
    "INTERCARS": "CAR.WA", "CAR": "CAR.WA",
    "AUTOPARTN": "APR.WA", "APR": "APR.WA",
    "DOMDEV": "DOM.WA", "DOM": "DOM.WA",
    "DEVELIA": "DVL.WA", "DVL": "DVL.WA"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


# --- 1. FUNKCJE POMOCNICZE TELEGRAMA ---
def send_message(token: str, chat_id: int, text: str, reply_markup: dict = None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload, timeout=10)

def answer_callback(token: str, callback_id: str, text: str = ""):
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_id, "text": text}, timeout=10)


# --- 2. POBIERANIE DANYCH GIEŁDOWYCH (KURS + HISTORIA DO WYKRESU) ---
def resolve_yahoo_ticker(symbol: str) -> str:
    clean = symbol.upper().strip()
    if clean in GPW_TO_TICKER:
        return GPW_TO_TICKER[clean]
    if clean.endswith(".WA"):
        return clean

    try:
        search_url = f"https://query2.finance.yahoo.com/v1/finance/search?q={clean}&quotesCount=5"
        r = requests.get(search_url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            for q in r.json().get("quotes", []):
                sym = q.get("symbol", "")
                if sym.endswith(".WA"):
                    return sym
    except Exception:
        pass

    return f"{clean}.WA"

def fetch_stock_data(symbol: str, range_str: str = "1y", interval: str = "1d"):
    ticker = resolve_yahoo_ticker(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_str}&interval={interval}"
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        result_list = data.get("chart", {}).get("result")
        if not result_list:
            return None

        res = result_list[0]
        meta = res.get("meta", {})
        timestamps = res.get("timestamp", [])
        quote = res.get("indicators", {}).get("quote", [{}])[0]

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])

        candles_by_date = {}
        for i, ts in enumerate(timestamps):
            if i < len(closes) and closes[i] is not None:
                c_val = round(float(closes[i]), 2)
                o_val = round(float(opens[i]), 2) if (i < len(opens) and opens[i] is not None) else c_val
                h_val = round(float(highs[i]), 2) if (i < len(highs) and highs[i] is not None) else c_val
                l_val = round(float(lows[i]), 2) if (i < len(lows) and lows[i] is not None) else c_val
                date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                candles_by_date[date_str] = {
                    "time": date_str,
                    "open": o_val,
                    "high": h_val,
                    "low": l_val,
                    "close": c_val
                }

        candles = sorted(candles_by_date.values(), key=lambda x: x["time"])

        price = meta.get("regularMarketPrice")
        if price is None and candles:
            price = candles[-1]["close"]

        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if len(candles) >= 2:
            prev_close = candles[-2]["close"]

        change = (price - prev_close) if (price is not None and prev_close) else 0.0
        change_pct = ((change / prev_close) * 100) if prev_close else 0.0

        return {
            "symbol": symbol.upper(),
            "ticker": ticker,
            "currency": meta.get("currency", "PLN"),
            "price": round(float(price), 2) if price is not None else 0.0,
            "change": round(float(change), 2),
            "change_pct": round(float(change_pct), 2),
            "day_high": round(float(meta.get("regularMarketDayHigh") or (candles[-1]["high"] if candles else 0)), 2),
            "day_low": round(float(meta.get("regularMarketDayLow") or (candles[-1]["low"] if candles else 0)), 2),
            "high_52w": round(float(meta.get("fiftyTwoWeekHigh") or 0), 2),
            "low_52w": round(float(meta.get("fiftyTwoWeekLow") or 0), 2),
            "candles": candles
        }
    except Exception as e:
        print(f"Błąd pobierania danych giełdowych dla {symbol}: {e}")
        return None


# --- 3. GENERATOR INTERAKTYWNEGO WYKRESU HTML (TRADINGVIEW) ---
def build_chart_html(symbol: str) -> HTMLResponse:
    stock = fetch_stock_data(symbol, range_str="5y", interval="1d")
    if not stock or not stock["candles"]:
        return HTMLResponse(
            f"<h2 style='color:white;background:#0f172a;padding:20px;font-family:sans-serif;'>"
            f"⚠️ Nie udało się pobrać danych wykresu dla spółki {symbol}.</h2>"
        )

    candles_json = json.dumps(stock["candles"])
    sign = "+" if stock["change"] >= 0 else ""
    color_hex = "#22c55e" if stock["change"] >= 0 else "#ef4444"

    html_content = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Wykres {stock['symbol']}</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: #0f172a;
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            height: 100vh;
            overflow: hidden;
        }}
        .header {{
            padding: 12px 16px;
            background: #1e293b;
            border-bottom: 1px solid #334155;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .title-box h1 {{ font-size: 18px; font-weight: 700; }}
        .title-box span {{ font-size: 12px; color: #94a3b8; }}
        .price-box {{ text-align: right; }}
        .price-box .current {{ font-size: 18px; font-weight: 700; }}
        .price-box .change {{ font-size: 13px; font-weight: 600; color: {color_hex}; }}
        .toolbar {{
            display: flex;
            justify-content: space-between;
            padding: 8px 12px;
            background: #0f172a;
            border-bottom: 1px solid #1e293b;
            gap: 6px;
        }}
        .btn-group {{ display: flex; gap: 4px; }}
        button {{
            background: #1e293b;
            color: #cbd5e1;
            border: 1px solid #334155;
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        button.active {{
            background: #3b82f6;
            color: #ffffff;
            border-color: #3b82f6;
        }}
        #legend {{
            padding: 6px 16px;
            font-size: 12px;
            color: #94a3b8;
            background: #0f172a;
            min-height: 26px;
            white-space: nowrap;
            overflow-x: auto;
        }}
        #chart-container {{ flex: 1; width: 100%; position: relative; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="title-box">
            <h1>🏢 {stock['symbol']}</h1>
            <span>{stock['ticker']} • GPW Warszawa</span>
        </div>
        <div class="price-box">
            <div class="current">{stock['price']:.2f} {stock['currency']}</div>
            <div class="change">{sign}{stock['change']:.2f} ({sign}{stock['change_pct']:.2f}%)</div>
        </div>
    </div>

    <div class="toolbar">
        <div class="btn-group">
            <button id="btn-1M" onclick="setRange(30, 'btn-1M')">1M</button>
            <button id="btn-3M" onclick="setRange(90, 'btn-3M')">3M</button>
            <button id="btn-6M" onclick="setRange(180, 'btn-6M')">6M</button>
            <button id="btn-1Y" class="active" onclick="setRange(365, 'btn-1Y')">1R</button>
            <button id="btn-MAX" onclick="setRange(9999, 'btn-MAX')">MAX</button>
        </div>
        <div class="btn-group">
            <button id="type-candle" class="active" onclick="setChartType('candle')">🕯️ Świece</button>
            <button id="type-area" onclick="setChartType('area')">📈 Linia</button>
        </div>
    </div>

    <div id="legend">👆 Przesuwaj palcem po wykresie lub przybliżaj dwoma palcami</div>
    <div id="chart-container"></div>

    <script>
        if (window.Telegram && window.Telegram.WebApp) {{
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();
        }}

        const allCandles = {candles_json};
        const allArea = allCandles.map(c => ({{ time: c.time, value: c.close }}));

        const container = document.getElementById('chart-container');
        const chart = LightweightCharts.createChart(container, {{
            width: container.clientWidth || window.innerWidth,
            height: container.clientHeight || (window.innerHeight - 130),
            layout: {{ background: {{ type: 'solid', color: '#0f172a' }}, textColor: '#94a3b8' }},
            grid: {{ vertLines: {{ color: '#1e293b' }}, horzLines: {{ color: '#1e293b' }} }},
            crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
            rightPriceScale: {{ borderColor: '#334155' }},
            timeScale: {{ borderColor: '#334155', timeVisible: false }}
        }});

        const candleSeries = chart.addCandlestickSeries({{
            upColor: '#22c55e', downColor: '#ef4444',
            borderUpColor: '#22c55e', borderDownColor: '#ef4444',
            wickUpColor: '#22c55e', wickDownColor: '#ef4444'
        }});

        const areaSeries = chart.addAreaSeries({{
            lineColor: '#3b82f6', topColor: 'rgba(59, 130, 246, 0.4)',
            bottomColor: 'rgba(59, 130, 246, 0.0)', lineWidth: 2, visible: false
        }});

        candleSeries.setData(allCandles);
        areaSeries.setData(allArea);

        function setRange(days, btnId) {{
            document.querySelectorAll('.toolbar .btn-group:first-child button').forEach(b => b.classList.remove('active'));
            document.getElementById(btnId).classList.add('active');
            if (days >= 9999 || allCandles.length === 0) {{
                chart.timeScale().fitContent();
                return;
            }}
            const lastItem = allCandles[allCandles.length - 1];
            const startIndex = Math.max(0, allCandles.length - Math.floor(days * 0.7));
            const firstItem = allCandles[startIndex];
            chart.timeScale().setVisibleRange({{ from: firstItem.time, to: lastItem.time }});
        }}

        function setChartType(type) {{
            document.getElementById('type-candle').classList.toggle('active', type === 'candle');
            document.getElementById('type-area').classList.toggle('active', type === 'area');
            candleSeries.applyOptions({{ visible: type === 'candle' }});
            areaSeries.applyOptions({{ visible: type === 'area' }});
        }}

        chart.subscribeCrosshairMove(param => {{
            const legend = document.getElementById('legend');
            if (!param.time || !param.seriesData) {{
                legend.innerHTML = '👆 Przesuwaj palcem po wykresie lub przybliżaj dwoma palcami';
                return;
            }}
            const data = param.seriesData.get(candleSeries);
            if (data) {{
                legend.innerHTML = `📅 <b>${{param.time}}</b> &nbsp;|&nbsp; O: <b>${{data.open}}</b> &nbsp; H: <b style="color:#22c55e">${{data.high}}</b> &nbsp; L: <b style="color:#ef4444">${{data.low}}</b> &nbsp; Z: <b>${{data.close}} PLN</b>`;
            }}
        }});

        window.addEventListener('resize', () => {{
            chart.applyOptions({{ width: container.clientWidth, height: container.clientHeight }});
        }});

        setRange(365, 'btn-1Y');
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


# --- 4. FORMATOWANIE KARTY NOTOWAŃ SPÓŁKI NA CZACIE ---
def send_company_quote_card(token: str, chat_id: int, symbol: str, base_url: str):
    stock = fetch_stock_data(symbol, range_str="1y", interval="1d")
    if not stock:
        send_message(token, chat_id, f"⚠️ Nie udało się pobrać aktualnego kursu dla *{symbol}*.")
        return

    icon = "🟢" if stock["change"] >= 0 else "🔴"
    sign = "+" if stock["change"] >= 0 else ""

    text = (
        f"🏢 *Notowania: {stock['symbol']}* (`{stock['ticker']}`)\n\n"
        f"💵 *Aktualny kurs:* `{stock['price']:.2f} {stock['currency']}`\n"
        f"{icon} *Zmiana dzienna:* `{sign}{stock['change']:.2f} {stock['currency']} ({sign}{stock['change_pct']:.2f}%)`\n"
        f"📊 *Zakres dzienny:* `{stock['day_low']:.2f} – {stock['day_high']:.2f} {stock['currency']}`\n"
        f"📅 *Zakres 52 tyg.:* `{stock['low_52w']:.2f} – {stock['high_52w']:.2f} {stock['currency']}`"
    )

    # Uderzamy bezpośrednio w /api/index?symbol=..., żeby ominąć przekierowania Vercela!
    webapp_url = f"{base_url}/api/index?symbol={stock['symbol']}"
    keyboard = {
        "inline_keyboard": [
            [{"text": f"📊 Interaktywny wykres {stock['symbol']}", "web_app": {"url": webapp_url}}],
            [{"text": "🔄 Odśwież kurs", "callback_data": f"PRICE:{stock['symbol']}"}]
        ]
    }
    send_message(token, chat_id, text, reply_markup=keyboard)


# --- 5. OBSŁUGA GET (WYKRES LUB STATUS SERWERA) ---
@app.get("/")
@app.get("/wykres")
@app.get("/api/wykres")
@app.get("/api/index")
@app.get("/api/webhook")
def handle_get_requests(symbol: str = None):
    # Jeśli w adresie jest ?symbol=..., od razu wyświetlamy interaktywny wykres!
    if symbol:
        return build_chart_html(symbol)
    return JSONResponse({"status": "Serwer dziala!"})


# --- 6. GŁÓWNY WEBHOOK TELEGRAMA (POST) ---
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

        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        base_url = f"https://{host}"

        supabase = create_client(supa_url, supa_key)

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

            elif cb_data.startswith("PRICE:"):
                symbol = cb_data.split(":", 1)[1]
                answer_callback(token, cb_id, f"Pobieram kurs {symbol}...")
                send_company_quote_card(token, chat_id, symbol, base_url)

            return {"status": "ok"}

        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        if not chat_id or not text or str(chat_id) != my_chat_id_str:
            return {"status": "ignored"}

        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].upper().strip() if len(parts) > 1 else ""

        if command in ["/kursy", "/kurs", "/wykres"]:
            if argument:
                send_company_quote_card(token, chat_id, argument, base_url)
            else:
                response = supabase.table("companies").select("symbol").execute()
                companies_list = [r["symbol"] for r in response.data]
                if not companies_list:
                    send_message(token, chat_id, "Brak obserwowanych spółek. Dodaj pierwszą komendą `/dodaj`!")
                else:
                    keyboard = []
                    row = []
                    for comp in companies_list:
                        row.append({"text": f"📈 {comp}", "callback_data": f"PRICE:{comp}"})
                        if len(row) == 2:
                            keyboard.append(row)
                            row = []
                    if row:
                        keyboard.append(row)

                    send_message(
                        token,
                        chat_id,
                        "📊 *Wybierz spółkę ze swojej listy, aby zobaczyć kurs i interaktywny wykres:*",
                        reply_markup={"inline_keyboard": keyboard}
                    )

        elif command == "/dodaj":
            if argument:
                try:
                    supabase.table("companies").insert({"symbol": argument}).execute()
                    send_message(token, chat_id, f"✅ Dodano spółkę *{argument}* do bazy!")
                except Exception:
                    send_message(token, chat_id, f"ℹ️ Spółka *{argument}* już jest obserwowana.")
            else:
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
                    "👇 *Kliknij spółkę, aby dodać ją do obserwowanych*\n_(lub wpisz ręcznie np._ `/dodaj ASSECOPOL`_)_:",
                    reply_markup={"inline_keyboard": keyboard}
                )

        elif command == "/usun":
            if argument:
                supabase.table("companies").delete().eq("symbol", argument).execute()
                send_message(token, chat_id, f"🗑️ Usunięto spółkę *{argument}* z bazy.")
            else:
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
