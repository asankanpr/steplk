import datetime
import json
import os
import re
from bs4 import BeautifulSoup
import requests
from supabase import Client, create_client

# Supabase Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY"
)  # Service Role Key for writing
supabase: Client = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY
    else None
)

# Request Headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# ==========================================
# 1. Fetch Exchange Rates (USD & EUR to LKR)
# ==========================================


def fetch_exchange_rates():
    """Exchange Rates API භාවිතයෙන් USD සහ EUR වල LKR අගයන් ලබා ගනී."""
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        res = requests.get(url, timeout=10)
        data = res.json()

        if data.get("result") == "success":
            lkr_rate = round(data["rates"]["LKR"], 2)
            eur_to_usd = data["rates"]["EUR"]
            eur_lkr_rate = round(lkr_rate / eur_to_usd, 2)

            return {
                "USD_LKR": f"LKR {lkr_rate:.2f}",
                "EUR_LKR": f"LKR {eur_lkr_rate:.2f}",
            }
    except Exception as e:
        print(f"⚠️ Exchange Rate Fetch Error: {e}")

    return {"USD_LKR": "LKR 302.50", "EUR_LKR": "LKR 331.10"}  # Fallback


# ==========================================
# 2. Fetch Gold Prices (22K & 24K Sovereign)
# ==========================================


def fetch_gold_prices():
    """ලංකාවේ රත්තරන් පවුමක (22K & 24K) මිල Web Scrape කරයි."""
    try:
        url = "https://goldprice.lk/"
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        # Scrape price elements
        text_content = soup.get_text()

        # Regex search for 22K and 24K sovereign prices in LKR
        gold_22k_match = re.search(
            r"22\s*K[^\d]*([\d,]{6,7})", text_content, re.IGNORECASE
        )
        gold_24k_match = re.search(
            r"24\s*K[^\d]*([\d,]{6,7})", text_content, re.IGNORECASE
        )

        gold_22k = (
            f"LKR {gold_22k_match.group(1)}"
            if gold_22k_match
            else "LKR 188,500"
        )
        gold_24k = (
            f"LKR {gold_24k_match.group(1)}"
            if gold_24k_match
            else "LKR 205,000"
        )

        return {"GOLD_22K": gold_22k, "GOLD_24K": gold_24k}

    except Exception as e:
        print(f"⚠️ Gold Price Scrape Error: {e}")
        return {
            "GOLD_22K": "LKR 188,500",
            "GOLD_24K": "LKR 205,000",
        }  # Fallback


# ==========================================
# 3. Calculate Astrological Data (Rahu Kalaya & Subha Disawa)
# ==========================================


def get_astrological_data():
    """දිනපතා රාහු කාලය සහ සුබ දිශාව ගණනය කරයි."""
    # Standard Astrological Rahu Kalaya Rules for Sri Lanka
    rahu_schedule = {
        0: {"time": "4:30 PM - 6:00 PM", "disawa": "දකුණ"},  # Sunday
        1: {"time": "7:30 AM - 9:00 AM", "disawa": "වයඹ"},  # Monday
        2: {"time": "3:00 PM - 4:30 PM", "disawa": "නැගෙනහිර"},  # Tuesday
        3: {"time": "12:00 PM - 1:30 PM", "disawa": "නිරිත"},  # Wednesday
        4: {"time": "1:30 PM - 3:00 PM", "disawa": "උතුර"},  # Thursday
        5: {"time": "10:30 AM - 12:00 PM", "disawa": "ගිනිකොන"},  # Friday
        6: {"time": "9:00 AM - 10:30 AM", "disawa": "බස්නාහිර"},  # Saturday
    }

    # Sri Lanka Day of Week
    today_weekday = datetime.datetime.now().weekday()
    astro = rahu_schedule.get(
        today_weekday, {"time": "1:30 PM - 3:04 PM", "disawa": "දකුණ"}
    )

    return {"RAHU_TIME": astro["time"], "SUBHA_DISAWA": astro["disawa"]}


# ==========================================
# 4. Supabase Upsert Execution
# ==========================================


def sync_daily_utilities_to_supabase():
    print("🔄 Daily Utilities Scraper ආරම්භ විය...")

    rates = fetch_exchange_rates()
    gold = fetch_gold_prices()
    astro = get_astrological_data()

    payloads = [
        {"key_name": "USD_LKR", "data_value": {"value": rates["USD_LKR"]}},
        {"key_name": "EUR_LKR", "data_value": {"value": rates["EUR_LKR"]}},
        {"key_name": "GOLD_22K", "data_value": {"value": gold["GOLD_22K"]}},
        {"key_name": "GOLD_24K", "data_value": {"value": gold["GOLD_24K"]}},
        {"key_name": "RAHU_TIME", "data_value": {"value": astro["RAHU_TIME"]}},
        {
            "key_name": "SUBHA_DISAWA",
            "data_value": {"value": astro["SUBHA_DISAWA"]},
        },
    ]

    for item in payloads:
        try:
            if supabase:
                supabase.table("daily_utilities").upsert(
                    item, on_conflict="key_name"
                ).execute()
                print(f"✅ Upsert Success: {item['key_name']} -> {item['data_value']['value']}")
            else:
                print(
                    f"ℹ️ Local Test: {item['key_name']} ="
                    f" {item['data_value']['value']}"
                )
        except Exception as e:
            print(f"❌ Upsert Error on {item['key_name']}: {e}")

    print("🎉 සියලු දෛනික දත්ත Supabase වෙත යාවත්කාලීන විය!")


if __name__ == "__main__":
    sync_daily_utilities_to_supabase()