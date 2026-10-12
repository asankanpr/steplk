import datetime
import json
import os
import re
from bs4 import BeautifulSoup
import requests
from supabase import Client, create_client

# Supabase Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = (
    create_client(SUPABASE_URL, SUPABASE_KEY) 
    if SUPABASE_URL and SUPABASE_KEY else None
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# ==========================================
# 1. Fetch Exchange Rates (USD & EUR to LKR)
# ==========================================
def fetch_exchange_rates():
    usd_rate = 300.00
    eur_rate = 330.00
    
    # 1. Primary API (open.er-api)
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            data = res.json()
            usd_rate = round(data["rates"]["LKR"], 2)
            eur_to_usd = data["rates"]["EUR"]
            eur_rate = round(usd_rate / eur_to_usd, 2)
            return {"USD_LKR": f"LKR {usd_rate:.2f}", "EUR_LKR": f"LKR {eur_rate:.2f}", "raw_usd": usd_rate}
    except Exception as e:
        print(f"⚠️ Primary ER-API Error: {e}")

    # 2. Backup API (exchangerate-api)
    try:
        url_backup = "https://api.exchangerate-api.com/v4/latest/USD"
        res_backup = requests.get(url_backup, timeout=15)
        if res_backup.status_code == 200:
            data_backup = res_backup.json()
            usd_rate = round(data_backup["rates"]["LKR"], 2)
            eur_rate = round(usd_rate / data_backup["rates"]["EUR"], 2)
            return {"USD_LKR": f"LKR {usd_rate:.2f}", "EUR_LKR": f"LKR {eur_rate:.2f}", "raw_usd": usd_rate}
    except Exception as e:
        print(f"⚠️ Backup Exchange API Error: {e}")

    return {"USD_LKR": f"LKR {usd_rate:.2f}", "EUR_LKR": f"LKR {eur_rate:.2f}", "raw_usd": usd_rate}

# ==========================================
# 2. Fetch Gold Prices (22K & 24K Sovereign)
# ==========================================
def fetch_gold_prices(usd_to_lkr):
    # ක්‍රමය 1: goldpricesrilanka.com වෙබ් අඩවියෙන් සජීවීව කියවීම
    try:
        url = "https://goldpricesrilanka.com/"
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            text_content = soup.get_text()
            
            # 22K සහ 24K පවුමක (Pawn) මිල Regex මඟින් හඳුනා ගැනීම
            gold_22k_match = re.search(r"22K[^\d]*(\d{1,3}(?:,\d{3})+)", text_content, re.IGNORECASE)
            gold_24k_match = re.search(r"24K[^\d]*(\d{1,3}(?:,\d{3})+)", text_content, re.IGNORECASE)

            if gold_22k_match and gold_24k_match:
                print(f"🪙 Local Scraper Success: 22K -> LKR {gold_22k_match.group(1)}")
                return {
                    "GOLD_22K": f"LKR {gold_22k_match.group(1)}",
                    "GOLD_24K": f"LKR {gold_24k_match.group(1)}"
                }
    except Exception as e:
        print(f"⚠️ Local Gold Site Scrape Error: {e}")

    # ක්‍රමය 2: ලෝක රන් අවුන්ස මිල (Global XAU) මඟින් ගණිතමයව ගණනය කිරීම (100% Fail-Safe)
    try:
        print("🔄 Switching to Global Gold API Calculation...")
        gold_url = "https://api.gold-api.com/price/XAU"
        res = requests.get(gold_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if res.status_code == 200:
            data = res.json()
            price_per_oz = data.get("price")
            if price_per_oz and usd_to_lkr:
                # 1 Troy Ounce = 31.1034768g | 1 Sovereign (පවුම) = 8g
                sovereign_24k_lkr = (price_per_oz / 31.1034768) * 8 * usd_to_lkr
                sovereign_22k_lkr = sovereign_24k_lkr * (22 / 24)
                
                return {
                    "GOLD_22K": f"LKR {int(sovereign_22k_lkr):,}",
                    "GOLD_24K": f"LKR {int(sovereign_24k_lkr):,}"
                }
    except Exception as e:
        print(f"⚠️ Global Gold API Error: {e}")

    # සියල්ල අසාර්ථක වුවහොත් වර්තමාන වෙළඳපොළට ගැළපෙන Fallback එක
    return {"GOLD_22K": "LKR 328,500", "GOLD_24K": "LKR 358,000"}

# ==========================================
# 3. Calculate Astrological Data
# ==========================================
def get_astrological_data():
    rahu_schedule = {
        0: {"time": "7:30 AM - 9:00 AM", "disawa": "වයඹ", "warnaya": "සුදු (White)"},              # සඳුදා
        1: {"time": "3:00 PM - 4:30 PM", "disawa": "ගිනිකොන", "warnaya": "රතු (Red)"},               # අඟහරුවාදා
        2: {"time": "12:00 PM - 1:30 PM", "disawa": "නිරිත", "warnaya": "කොළ (Green)"},             # බදාදා
        3: {"time": "1:30 PM - 3:00 PM", "disawa": "නැගෙනහිර", "warnaya": "කහ / රන් (Yellow / Gold)"}, # බ්‍රහස්පතින්දා
        4: {"time": "10:30 AM - 12:00 PM", "disawa": "ඊශාන", "warnaya": "විසිතුරු / ලා වර්ණ (Light)"}, # සිකුරාදා
        5: {"time": "9:00 AM - 10:30 AM", "disawa": "දකුණ", "warnaya": "නිල් (Blue)"},              # සෙනසුරාදා
        6: {"time": "4:30 PM - 6:00 PM", "disawa": "උතුර", "warnaya": "තඹ / රතු (Copper / Red)"}       # ඉරිදා
    }
    sl_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now_sl = datetime.datetime.now(sl_tz)
    today_weekday = now_sl.weekday()
    
    astro = rahu_schedule.get(today_weekday, {"time": "1:30 PM - 3:00 PM", "disawa": "දකුණ", "warnaya": "සුදු"})
    
    return {
        "RAHU_TIME": astro["time"], 
        "SUBHA_DISAWA": astro["disawa"],
        "SUBHA_WARNAYA": astro["warnaya"] # 🔴 අලුතින් එක් කළ සුබ වර්ණය
    }

# ==========================================
# 4. Supabase Upsert Execution
# ==========================================
def sync_daily_utilities_to_supabase():
    print("🔄 Daily Utilities Scraper ආරම්භ විය...")
    
    rates = fetch_exchange_rates()
    gold = fetch_gold_prices(rates.get("raw_usd", 300.00))
    astro = get_astrological_data()

    payloads = [
        {"key_name": "USD_LKR", "data_value": {"value": rates["USD_LKR"]}},
        {"key_name": "EUR_LKR", "data_value": {"value": rates["EUR_LKR"]}},
        {"key_name": "GOLD_22K", "data_value": {"value": gold["GOLD_22K"]}},
        {"key_name": "GOLD_24K", "data_value": {"value": gold["GOLD_24K"]}},
        {"key_name": "RAHU_TIME", "data_value": {"value": astro["RAHU_TIME"]}},
        {"key_name": "SUBHA_DISAWA", "data_value": {"value": astro["SUBHA_DISAWA"]}},
        {"key_name": "SUBHA_WARNAYA", "data_value": {"value": astro["SUBHA_WARNAYA"]}}, # 🔴 අලුත් Payload එක
    ]

    for item in payloads:
        try:
            if supabase:
                supabase.table("daily_utilities").upsert(item, on_conflict="key_name").execute()
                print(f"✅ Upsert Success: {item['key_name']} -> {item['data_value']['value']}")
            else:
                print(f"ℹ️ Local Test: {item['key_name']} = {item['data_value']['value']}")
        except Exception as e:
            print(f"❌ Upsert Error on {item['key_name']}: {e}")

    print("🎉 සියලු දෛනික දත්ත Supabase වෙත යාවත්කාලීන විය!")

if __name__ == "__main__":
    sync_daily_utilities_to_supabase()
