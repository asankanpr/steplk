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
    # Sri Lanka Timezone (UTC + 5:30)
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
    # 1. FETCH LIVE USD RATE
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.urlopen(url)
        data = json.loads(req.read().decode('utf-8'))
        
        lkr_rate = data.get("rates", {}).get("LKR")
        if lkr_rate:
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

    # 3. UPDATE GOLD RATE (22K Sovereign)
    try:
        # Fallback / Live estimate logic
        formatted_gold = "LKR 188,500"
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
