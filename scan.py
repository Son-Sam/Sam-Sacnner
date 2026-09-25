import requests
import time
import json
import os
import re
from datetime import datetime

# ============ تنظیمات (اینجا تغییر بده) ============
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

RATIO_THRESHOLD = 0.5
MIN_VOLUME_USD = 50000000
MIN_MARKET_CAP_USD = 0
TOP_N_COINS = 500
INTERVAL_MINUTES = 30
STATE_FILE = "previous_coins.json"
# =====================================================

SCANNER_URL = "https://scanner.tradingview.com/coin/scan"
COLUMNS = ["name", "close", "market_cap_calc", "24h_vol_cmc", "TechRating_1D", "altrank", "galaxyscore", "crypto_total_rank", "description", "24h_vol_to_market_cap"]


def format_number(n):
    n = float(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.2f}K"
    return f"{n:.2f}"


def tech_rating_label(value):
    if value is None:
        return "N/A"
    if isinstance(value, str):
        return value
    if value >= 0.5:
        return "Strong Buy"
    if value >= 0.1:
        return "Buy"
    if value > -0.1:
        return "Neutral"
    if value > -0.5:
        return "Sell"
    return "Strong Sell"


def tradingview_link(name):
    base = name[:-3] if name.upper().endswith("USD") else name
    symbol = f"BINANCE:{base}USDT.P"
    return f"https://www.tradingview.com/chart/?symbol={symbol}"


def slugify(full_name):
    slug = full_name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def coinmarketcap_link(full_name):
    return f"https://coinmarketcap.com/currencies/{slugify(full_name)}/"


def cryptorank_link(full_name):
    return f"https://cryptorank.io/price/{slugify(full_name)}"


def fetch_batch(start, count=100):
    payload = {
        "columns": COLUMNS,
        "filter": [
            {"left": "24h_vol_to_market_cap", "operation": "greater", "right": RATIO_THRESHOLD},
        ],
        "sort": {"sortBy": "crypto_total_rank", "sortOrder": "asc"},
        "markets": ["coin"],
        "range": [start, start + count],
    }
    r = requests.post(SCANNER_URL, json=payload, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def get_top_coins():
    results = []
    for start in range(0, TOP_N_COINS, 100):
        batch = fetch_batch(start)
        if not batch:
            break
        results.extend(batch)
        time.sleep(0.3)
    return results


def find_high_ratio_coins(coins, threshold=RATIO_THRESHOLD):
    flagged = []
    for c in coins:
        name, close, mcap, vol, tech, altrank, galaxy, rank, description, ratio = c["d"]
        if not mcap or not vol or mcap <= 0 or ratio is None:
            continue
        if rank is not None and rank > TOP_N_COINS:
            continue
        if mcap < MIN_MARKET_CAP_USD:
            continue
        if vol < MIN_VOLUME_USD:
            continue
        flagged.append((name, ratio, mcap, vol, tech, altrank, galaxy, rank, description))
    return sorted(flagged, key=lambda x: x[1], reverse=True)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})


def send_in_chunks(all_lines, max_len=3800):
    chunk = ""
    for block in all_lines:
        candidate = (chunk + "\n\n" + block) if chunk else block
        if len(candidate) > max_len:
            send_telegram_message(chunk)
            chunk = block
        else:
            chunk = candidate
    if chunk:
        send_telegram_message(chunk)


def load_previous_names():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f)), False
    return set(), True


def save_current_names(names):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(names), f)


def format_coin(name, ratio, mcap, vol, tech, altrank, galaxy, rank, description, is_new=False):
    link = tradingview_link(name)
    tag = "🆕 " if is_new else ""
    tech_label = tech_rating_label(tech)
    altrank_str = f"{altrank:.0f}" if altrank is not None else "N/A"
    galaxy_str = f"{galaxy:.0f}" if galaxy is not None else "N/A"
    rank_str = f"#{rank:.0f}" if rank is not None else ""

    cmc_str = f"[CMC]({coinmarketcap_link(description)})" if description else ""
    cr_str = f"[CR]({cryptorank_link(description)})" if description else ""

    return (
        f"{tag}[{name}]({link}) {rank_str} {cmc_str} {cr_str} v/cap: {ratio:.2f}\n"
        f"Vol: ${format_number(vol)} | MCap: ${format_number(mcap)}\n"
        f"T Rating: {tech_label} | AltRank: {altrank_str} | G Score: {galaxy_str}"
    )


def run_once():
    previous_names, first_run = load_previous_names()

    coins = get_top_coins()
    print(f"[{datetime.now():%H:%M:%S}] تعداد کوین دریافت‌شده: {len(coins)}")

    flagged = find_high_ratio_coins(coins)

    if not flagged:
        print("هیچ کوینی با نسبت > آستانه پیدا نشد.")
        save_current_names(set())
        return

    current_names = {c[0] for c in flagged}
    new_names = set() if first_run else (current_names - previous_names)

    new_coins = [c for c in flagged if c[0] in new_names]
    old_coins = [c for c in flagged if c[0] not in new_names]

    lines = [
        f"*GitHub تنظیمات اسکن:*\n"
        f"min v/cap: {RATIO_THRESHOLD} | min v: ${format_number(MIN_VOLUME_USD) if MIN_VOLUME_USD else 0}\n"
        f"min cap: ${format_number(MIN_MARKET_CAP_USD) if MIN_MARKET_CAP_USD else 0} | "
        f"top: {TOP_N_COINS} | interval m: {INTERVAL_MINUTES}\n"
        f"تعداد کوین‌های یافت‌شده: {len(flagged)} ({len(new_coins)} جدید)\n"
    ]

    for c in new_coins:
        lines.append(format_coin(*c, is_new=True))
    if new_coins and old_coins:
        lines.append("➖➖➖➖➖➖➖➖➖➖")
    for c in old_coins:
        lines.append(format_coin(*c))

    send_in_chunks(lines)

    print(f"{len(flagged)} کوین پیدا شد ({len(new_coins)} جدید) و به تلگرام ارسال شد.")
    save_current_names(current_names)


def main():
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"خطا: {e}")
        print(f"در حال انتظار برای {INTERVAL_MINUTES} دقیقه...\n")
        time.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
