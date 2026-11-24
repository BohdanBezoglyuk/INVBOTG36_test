"""Мониторинг новостей и сделок техно-компаний -> дайджесты в Telegram.
Запускается по расписанию в GitHub Actions (бесплатно, сервер не нужен)."""
import os, json, re, html, hashlib, time, calendar
from pathlib import Path
from urllib.parse import quote
import feedparser, requests, yfinance as yf

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = Path("state.json")
UA = {"User-Agent": "TechStockBot/1.0 (contact: your_email@example.com)"}  # SEC требует контакт

MAX_AGE_H = int(os.getenv("MAX_AGE_HOURS", "24"))   # старше — игнорируем
MAX_MSGS = int(os.getenv("MAX_MESSAGES", "15"))       # максимум сообщений за запуск
PER_TICKER = int(os.getenv("MAX_PER_TICKER", "8"))   # новостей в одном дайджесте

DEFAULT = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "META": "Meta Platforms", "TSLA": "Tesla", "AMD": "AMD",
    "INTC": "Intel", "AVGO": "Broadcom", "ORCL": "Oracle", "CRM": "Salesforce",
    "ADBE": "Adobe", "TSM": "TSMC", "PLTR": "Palantir", "QCOM": "Qualcomm",
}
ALIASES = {"GOOGL": ["Google"], "META": ["Meta", "Facebook"], "TSM": ["Taiwan Semiconductor"],
           "AMZN": ["AWS"], "MSFT": ["Azure"], "NVDA": ["Nvidia"]}
if os.getenv("TICKERS"):
    COMPANIES = {t.strip().upper(): DEFAULT.get(t.strip().upper(), t.strip().upper())
                 for t in os.environ["TICKERS"].split(",") if t.strip()}
else:
    COMPANIES = DEFAULT

DEAL = r"acqui|merger|buyout|takeover|stake|investment|partnership|contract|agreement|deal|joint venture"
FIN = r"earnings|revenue|guidance|buyback|repurchase|dividend|forecast|profit|upgrade|downgrade|price target|outlook|results"
POS = r"beat|surge|record|raises|upgrade|jump|soar|win|expands|approval|rall"
NEG = r"miss|plunge|cut|slash|downgrade|lawsuit|probe|investigation|fall|layoff|recall|ban|short report"
NOISE = re.compile(r"black friday|% off|discount|coupon|promo|gift guide|should you buy|if you invest|"
                   r"how to play|better buy|could have \$|best stocks to buy", re.I)
SEC_ITEMS = {"1.01": "Существенное соглашение", "2.01": "Завершена сделка/поглощение",
             "2.02": "Финансовые результаты", "7.01": "Reg FD раскрытие", "8.01": "Прочее событие",
             "5.02": "Смена руководства", "3.02": "Выпуск акций"}

def h(s): return hashlib.md5(s.encode()).hexdigest()

def title_key(t):
    t = re.sub(r"\s[-–|]\s[^-–|]+$", "", t)              # убрать " - Источник"
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", "", t.lower())).strip()

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"seen": [], "init": False}

def save_state(st):
    st["seen"] = st["seen"][-3000:]
    STATE_FILE.write_text(json.dumps(st))

def fetch(url):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        return feedparser.parse(r.content).entries
    except Exception as e:
        print("fetch error", url, e)
        return []

def too_old(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return bool(t) and (time.time() - calendar.timegm(t)) > MAX_AGE_H * 3600

def mentions(ticker, name, text):
    if re.search(rf"\b{re.escape(ticker)}\b", text):
        return True
    return any(re.search(rf"\b{re.escape(n)}\b", text, re.I) for n in [name] + ALIASES.get(ticker, []))

def collect(ticker, name):
    items = []
    sec = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=include&count=10&output=atom"
    for e in fetch(sec):
        if too_old(e): continue
        summ = re.sub("<[^>]+>", " ", e.get("summary", ""))
        label = next((v for k, v in SEC_ITEMS.items() if f"Item {k}" in summ), "8-K")
        items.append(("sec", f"{label}: {e.get('title','')}", e.link, summ))
    q = quote(f'"{name}" stock (acquisition OR earnings OR deal OR buyback OR guidance) when:1d')
    for e in fetch(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")[:8]:
        if not too_old(e): items.append(("news", e.title, e.link, ""))
    for e in fetch(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US")[:6]:
        if not too_old(e): items.append(("news", e.title, e.link, e.get("summary", "")))
    return items

def analyze(ticker, name, src, title, extra):
    """Возвращает (иконки, эмодзи-настроение) или None, если новость не нужна."""
    text = f"{title} {extra}"
    if src == "sec":
        icons = ["📄"]
    else:
        if NOISE.search(text) or not mentions(ticker, name, text):
            return None
        t = text.lower()
        icons = []
        if re.search(DEAL, t): icons.append("🤝")
        if re.search(FIN, t): icons.append("💰")
        if not icons: return None
    t = text.lower()
    p, n = bool(re.search(POS, t)), bool(re.search(NEG, t))
    return "".join(icons), ("🟢" if p and not n else "🔴" if n and not p else "⚪")

def price_info(ticker):
    try:
        c = yf.Ticker(ticker).history(period="1y")["Close"].dropna()
        p, hi = c.iloc[-1], c.max()
        d1, off_hi, vs_ma = (p / c.iloc[-2] - 1) * 100, (p / hi - 1) * 100, (p / c.tail(50).mean() - 1) * 100
        flag = "✅ ниже максимума/средней — зона интереса" if (off_hi <= -15 or vs_ma <= -3) else "⏳ цена не выглядит дешёвой"
        return f"💵 ${p:.2f} ({d1:+.1f}% за день) | от 52н. макс: {off_hi:.1f}% | к MA50: {vs_ma:+.1f}%\n{flag}"
    except Exception as e:
        print("price error", ticker, e)
        return "💵 цена недоступна"

def send(text):
    for _ in range(4):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                              json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": True}, timeout=20)
        except Exception as e:
            print("send error", e); time.sleep(3); continue
        if r.status_code == 429:  # лимит Telegram — ждём и повторяем
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
            continue
        if not r.ok:
            print("telegram error", r.status_code, r.text); return False
        time.sleep(1.2)
        return True
    return False

def main():
    st = load_state()
    seen = set(st["seen"])
    first_run = not st["init"]
    batches = {}  # ticker -> [(icons, mood, title, link, link_hash, title_hash)]
    taken_titles = set()

    for ticker, name in COMPANIES.items():
        for src, title, link, extra in collect(ticker, name):
            lh, th = h(link), h(title_key(title))
            if lh in seen or th in seen or th in taken_titles:
                continue
            res = analyze(ticker, name, src, title, extra)
            if not res:
                continue
            taken_titles.add(th)
            batches.setdefault(ticker, []).append((*res, title, link, lh, th))

    if first_run:  # первый запуск: тихо запоминаем всё, что есть сейчас
        for items in batches.values():
            for it in items:
                st["seen"] += [it[4], it[5]]
        st["init"] = True
        save_state(st)
        send(f"✅ Бот запущен. Слежу за: {', '.join(COMPANIES)}")
        return

    sent = 0
    for ticker, items in sorted(batches.items(), key=lambda kv: (not any(i[0] == "📄" for i in kv[1]), -len(kv[1]))):
        items.sort(key=lambda i: i[0] != "📄")  # официальные 8-K всегда сверху
        if sent >= MAX_MSGS:
            break  # остальное уйдёт в следующем запуске (не помечено как seen)
        shown = items[:PER_TICKER]
        lines = [f"{m} {i} <a href=\"{html.escape(l, quote=True)}\">{html.escape(t)}</a>"
                 for i, m, t, l, _, _ in shown]
        extra = f"\n…ещё {len(items) - len(shown)} — придут следующим сообщением" if len(items) > len(shown) else ""
        msg = (f"<b>{ticker}</b> · {len(items)} нов.\n\n" + "\n\n".join(lines) + extra +
               f"\n\n{price_info(ticker)}\n<i>Не инвестсовет. Проверяйте перед покупкой.</i>")
        if send(msg):
            for it in shown:                     # помечаем только показанные; остальное уйдёт в следующем дайджесте
                st["seen"] += [it[4], it[5]]
            save_state(st)                       # сохраняем после КАЖДОГО сообщения
            sent += 1
    save_state(st)
    print("messages sent:", sent, "| tickers with news:", len(batches))

if __name__ == "__main__":
    main()
