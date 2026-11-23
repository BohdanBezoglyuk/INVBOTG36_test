"""Мониторинг новостей и сделок техно-компаний -> уведомления в Telegram.
Запускается по расписанию в GitHub Actions (бесплатно, сервер не нужен)."""
import os, json, re, html, hashlib
from pathlib import Path
from urllib.parse import quote
import feedparser, requests, yfinance as yf

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path("state.json")
UA = {"User-Agent": "TechStockBot/1.0 (contact: grims111111@gmail.com)"}  # SEC требует контакт

DEFAULT = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "META": "Meta Platforms", "TSLA": "Tesla", "AMD": "AMD",
    "INTC": "Intel", "AVGO": "Broadcom", "ORCL": "Oracle", "CRM": "Salesforce",
    "ADBE": "Adobe", "TSM": "TSMC", "PLTR": "Palantir", "QCOM": "Qualcomm",
}
if os.getenv("TICKERS"):  # например: "AAPL,NVDA,AMD"
    COMPANIES = {t.strip().upper(): DEFAULT.get(t.strip().upper(), t.strip().upper())
                 for t in os.environ["TICKERS"].split(",") if t.strip()}
else:
    COMPANIES = DEFAULT

DEAL = r"acqui|merger|buyout|takeover|stake|investment|partnership|contract|agreement|deal|joint venture"
FIN = r"earnings|revenue|guidance|buyback|repurchase|dividend|forecast|profit|upgrade|downgrade|price target|outlook|results"
POS = r"beat|surge|record|raises|upgrade|jump|soar|win|expands|approval"
NEG = r"miss|plunge|cut|downgrade|lawsuit|probe|investigation|fall|layoff|recall|ban"
SEC_ITEMS = {"1.01": "Существенное соглашение", "2.01": "Завершена сделка/поглощение",
             "2.02": "Финансовые результаты", "7.01": "Reg FD раскрытие", "8.01": "Прочее событие",
             "5.02": "Смена руководства", "3.02": "Выпуск акций"}

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": [], "init": False}

def save_state(s):
    s["seen"] = s["seen"][-3000:]
    STATE_FILE.write_text(json.dumps(s))

def fetch(url):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        return feedparser.parse(r.content).entries
    except Exception as e:
        print("fetch error", url, e)
        return []

def classify(text):
    t = text.lower()
    tags = []
    if re.search(DEAL, t): tags.append("🤝 Сделка")
    if re.search(FIN, t): tags.append("💰 Финансы")
    mood = "🟢" if re.search(POS, t) and not re.search(NEG, t) else "🔴" if re.search(NEG, t) and not re.search(POS, t) else "⚪"
    return tags, mood

def price_info(ticker):
    try:
        h = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
        p, hi = h.iloc[-1], h.max()
        d1 = (p / h.iloc[-2] - 1) * 100
        off_hi = (p / hi - 1) * 100
        ma50 = h.tail(50).mean()
        vs_ma = (p / ma50 - 1) * 100
        flag = "✅ цена ниже средней/от максимума — зона интереса" if (off_hi <= -15 or vs_ma <= -3) else "⏳ цена не выглядит дешёвой"
        return (f"💵 ${p:.2f} ({d1:+.1f}% за день)\n"
                f"📉 от 52н. максимума: {off_hi:.1f}% | к MA50: {vs_ma:+.1f}%\n{flag}")
    except Exception as e:
        print("price error", ticker, e)
        return "💵 цена недоступна"

def send(text):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                        "disable_web_page_preview": True}, timeout=20)

def collect(ticker, name):
    items = []
    # 1) Официальные 8-K из SEC EDGAR (прямые новости от компании)
    sec = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=include&count=10&output=atom"
    for e in fetch(sec):
        summ = re.sub("<[^>]+>", " ", e.get("summary", ""))
        label = next((v for k, v in SEC_ITEMS.items() if f"Item {k}" in summ), "8-K")
        items.append(("📄 SEC 8-K", f"{label}: {e.get('title','')}", e.link, summ))
    # 2) Новости по сделкам/финансам (Google News)
    q = quote(f'"{name}" stock (acquisition OR earnings OR deal OR buyback OR guidance) when:1d')
    for e in fetch(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")[:8]:
        items.append(("📰 Новость", e.title, e.link, ""))
    # 3) Yahoo Finance RSS
    for e in fetch(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US")[:6]:
        items.append(("📰 Yahoo", e.title, e.link, e.get("summary", "")))
    return items

def main():
    st = load_state()
    seen = set(st["seen"])
    first_run = not st["init"]
    new_msgs = 0
    for ticker, name in COMPANIES.items():
        sent_price = None
        for src, title, link, extra in collect(ticker, name):
            uid = hashlib.md5(link.encode()).hexdigest()
            if uid in seen:
                continue
            seen.add(uid); st["seen"].append(uid)
            if first_run:
                continue  # при первом запуске не спамим старым
            tags, mood = classify(title + " " + extra)
            if src.startswith("📄"):
                tags = tags or ["📄 Официальное раскрытие"]
            if not tags:
                continue  # только сделки и финансы
            if sent_price is None:
                sent_price = price_info(ticker)
            send(f"{mood} <b>{ticker}</b> · {src}\n{' '.join(tags)}\n\n"
                 f"{html.escape(title)}\n\n{sent_price}\n\n<a href=\"{link}\">Читать</a>\n"
                 f"<i>Не инвестсовет. Проверяйте перед покупкой.</i>")
            new_msgs += 1
    if first_run:
        send(f"✅ Бот запущен. Слежу за: {', '.join(COMPANIES)}")
        st["init"] = True
    save_state(st)
    print("sent:", new_msgs)

if __name__ == "__main__":
    main()
