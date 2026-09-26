import os
import json
import urllib.request
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_and_update_rates():
    try:
        # 1. Fetch USD to LKR exchange rate
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.urlopen(url)
        data = json.loads(req.read().decode('utf-8'))
        
        lkr_rate = data.get("rates", {}).get("LKR")
        if lkr_rate:
            formatted_usd = f"LKR {lkr_rate:.2f}"
            print(f"💵 Fetched Live USD Rate: {formatted_usd}")

            supabase.table("daily_utilities").upsert({
                "key_name": "USD_LKR",
                "data_value": {"value": formatted_usd}
            }, on_conflict="key_name").execute()

        # 2. Gold Rate Update (Gold 22K)
        formatted_gold = "LKR 188,500"
        supabase.table("daily_utilities").upsert({
            "key_name": "GOLD_22K",
            "data_value": {"value": formatted_gold}
        }, on_conflict="key_name").execute()

        # 3. Rahu Time Update
        formatted_rahu = "1:32 PM - 3:04 PM"
        supabase.table("daily_utilities").upsert({
            "key_name": "RAHU_TIME",
            "data_value": {"value": formatted_rahu}
        }, on_conflict="key_name").execute()

        print("✅ සියලු දත්ත Supabase වෙත සාර්ථකව Update විය!")

    except Exception as e:
        print(f"❌ Error updating rates: {str(e)}")

if __name__ == "__main__":
    fetch_and_update_rates()
