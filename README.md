# Tech Stock News Bot (Telegram, бесплатно)

Бот каждые ~10 минут проверяет SEC 8-K, Google News и Yahoo Finance по техно-компаниям,
фильтрует сделки и финансовые новости и шлёт их в Telegram вместе с ценой и сигналом «дёшево/нет».

## Запуск за 5 минут
1. В Telegram: @BotFather → `/newbot` → получи **TELEGRAM_TOKEN**.
2. Напиши своему боту `/start`, затем открой `https://api.telegram.org/bot<TOKEN>/getUpdates` → возьми `chat.id` = **TELEGRAM_CHAT_ID**.
3. Создай репозиторий на GitHub (приватный подойдёт), залей все файлы этой папки.
4. Settings → Secrets and variables → Actions:
   - Secrets: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
   - (опц.) Variables: `TICKERS` = `AAPL,NVDA,AMD,...`
5. Вкладка Actions → monitor → **Run workflow**. Дальше работает сам.

Замени email в `UA` внутри `bot.py` (требование SEC).
