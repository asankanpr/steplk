import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_sri_lanka_rahu_time():
    """ශ්‍රී ලංකා වේලාවට අනුව අදාළ දවසේ රාහු කාලය සොයාගැනීම"""
    sl_tz = timezone(timedelta(hours=5, minutes=30))
    sl_now = datetime.now(sl_tz)
    day_of_week = sl_now.weekday() # 0 = Monday, 6 = Sunday

    rahu_schedule = {
        0: "7:30 AM - 9:00 AM",   # Monday
        1: "3:00 PM - 4:30 PM",   # Tuesday
        2: "12:00 PM - 1:30 PM",  # Wednesday
        3: "1:30 PM - 3:00 PM",   # Thursday
        4: "10:30 AM - 12:00 PM", # Friday
        5: "9:00 AM - 10:30 AM",  # Saturday
        6: "4:30 PM - 6:00 PM"    # Sunday
    }

    return rahu_schedule.get(day_of_week, "1:32 PM - 3:04 PM")

def fetch_and_update_rates():
    lkr_rate = 302.50 # Default fallback exchange rate

    # 1. FETCH LIVE USD RATE
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.urlopen(url)
        data = json.loads(req.read().decode('utf-8'))
        
        fetched_lkr = data.get("rates", {}).get("LKR")
        if fetched_lkr:
            lkr_rate = fetched_lkr
            formatted_usd = f"LKR {lkr_rate:.2f}"
            print(f"💵 Live USD Rate: {formatted_usd}")

            supabase.table("daily_utilities").upsert({
                "key_name": "USD_LKR",
                "data_value": {"value": formatted_usd}
            }, on_conflict="key_name").execute()
    except Exception as e:
        print(f"❌ Error updating USD rate: {str(e)}")

    # 2. UPDATE DYNAMIC RAHU TIME
    try:
        rahu_time = get_sri_lanka_rahu_time()
        print(f"⏰ Today's Rahu Time: {rahu_time}")

        supabase.table("daily_utilities").upsert({
            "key_name": "RAHU_TIME",
            "data_value": {"value": rahu_time}
        }, on_conflict="key_name").execute()
    except Exception as e:
        print(f"❌ Error updating Rahu time: {str(e)}")

    # 3. FETCH LIVE GOLD RATE (22K Sovereign / පවුම)
    try:
        formatted_gold = "LKR 298,500" # Fallback to SL market 22K average
        try:
            gold_url = "https://api.gold-api.com/price/XAU"
            req_gold = urllib.request.Request(gold_url, headers={'User-Agent': 'Mozilla/5.0'})
            res_gold = urllib.request.urlopen(req_gold)
            gold_data = json.loads(res_gold.read().decode('utf-8'))
            price_per_oz = gold_data.get("price")
            
            if price_per_oz and lkr_rate:
                # 1 Ounce = 31.1035g | 1 Sovereign (පවුම) = 8g | 22K = 22/24 purity
                sovereign_22k_lkr = (price_per_oz / 31.1035) * 8 * (22 / 24) * lkr_rate
                formatted_gold = f"LKR {int(sovereign_22k_lkr):,}"
        except Exception as g_err:
            print(f"⚠️ Live Gold API fetch failed, using fallback market price: {str(g_err)}")

        print(f"🪙 Today's Gold 22K Rate: {formatted_gold}")

        supabase.table("daily_utilities").upsert({
            "key_name": "GOLD_22K",
            "data_value": {"value": formatted_gold}
        }, on_conflict="key_name").execute()
    except Exception as e:
        print(f"❌ Error updating Gold rate: {str(e)}")

    print("✅ Daily utilities update completed successfully!")

if __name__ == "__main__":
    fetch_and_update_rates()
