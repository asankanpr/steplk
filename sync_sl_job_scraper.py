import os
import json
import time
import re
import datetime
from datetime import timezone, timedelta
import requests
from google import genai
from google.genai import types
from supabase import create_client, Client

# Environment Variables (Single හෝ Dual Project Support)
# 1. Source DB (vacancies table තියෙන project එක)
SRC_SUPABASE_URL = os.getenv("SRC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SRC_SUPABASE_KEY = os.getenv("SRC_SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

# 2. Destination DB (අපේ main 'posts' table එක තියෙන project එක)
DEST_SUPABASE_URL = os.getenv("DEST_SUPABASE_URL") or os.getenv("SUPABASE_URL")
DEST_SUPABASE_KEY = os.getenv("DEST_SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([SRC_SUPABASE_URL, SRC_SUPABASE_KEY, DEST_SUPABASE_URL, DEST_SUPABASE_KEY, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables for Supabase or Gemini.")

# Supabase Clients දෙක නිර්මාණය කිරීම
supabase_src: Client = create_client(SRC_SUPABASE_URL, SRC_SUPABASE_KEY)
supabase_dest: Client = create_client(DEST_SUPABASE_URL, DEST_SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ශ්‍රී ලංකාවේ වේලාව (UTC + 5:30)
SL_TZ = timezone(timedelta(hours=5, minutes=30))

def is_post_expired(closing_date_str):
    if not closing_date_str or not isinstance(closing_date_str, str):
        return False

    cleaned = closing_date_str.strip()
    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', cleaned)
    if date_match:
        try:
            y, m, d = map(int, date_match.groups())
            closing_date = datetime.date(y, m, d)
            today_sl = datetime.datetime.now(SL_TZ).date()

            if closing_date < today_sl:
                return True
        except ValueError:
            pass

    return False

def extract_with_gemini(file_bytes, mime_type):
    prompt = """
    You are a Sri Lankan Job Advertisement OCR Specialist.
    Extract accurate details into a strict JSON object:
    {
      "title": "Exact post title in Sinhala or English",
      "organization": "Company or Institute Name",
      "category": "Government Job / Private Job / Course / කඩඉම් විභාග",
      "meq_level": "OL / AL / NVQ / DEGREE / NONE",
      "closing_date": "YYYY-MM-DD or null",
      "salary_code": "Salary code or null",
      "salary_amount": "Salary amount or null",
      "description": "Brief summary in Sinhala",
      "qualifications": "Key requirements in Sinhala"
    }
    Respond strictly with valid JSON.
    """

    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )

    max_retries = 3
    delays = [10, 20, 30]

    for attempt in range(1, max_retries + 1):
        try:
            time.sleep(3)
            response = ai_client.models.generate_content(
                model='gemini-3.8-flash',
                contents=[genai.types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
                config=config
            )
            return json.loads(response.text.strip())
        except Exception as e:
            err_str = str(e)
            print(f"⚠️ Gemini Extract Retry ({attempt}/{max_retries}): {err_str}")
            if ("503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str) and attempt < max_retries:
                time.sleep(delays[attempt - 1])
            else:
                raise e

def process_sl_job_scraper_vacancies():
    print("🔄 `sl-job-scraper` sync process ආරම්භ විය...")

    # 1. Source DB එකෙන් Unprocessed Vacancies ලබා ගැනීම
    res = supabase_src.table("vacancies").select("*").eq("is_processed", False).order("id", desc=False).limit(5).execute()
    vacancies = res.data or []

    if not vacancies:
        print("📁 Sync කිරීමට නව Vacancies නොමැත.")
        return

    print(f"🔄 හමුවූ නව Vacancies ගණන: {len(vacancies)}")

    for vac in vacancies:
        vac_id = vac["id"]
        company = vac.get("company_name", "N/A")
        title = vac.get("post_title", "Job Vacancy")
        closing = vac.get("closing_date")
        web_link = vac.get("web_link", "")
        file_link = vac.get("file_link")
        is_file = vac.get("is_file", False)

        print(f"📄 Processing ID {vac_id}: {title} ({company})...")

        # Pre-Check
        if closing and is_post_expired(closing):
            print(f"⏩ [Pre-Check] ID {vac_id} - Post Expired ({closing}). Skipping...")
            supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
            continue

        extracted_data = {}
        has_gemini_error = False

        if is_file and file_link and str(file_link).startswith("http"):
            try:
                file_res = requests.get(file_link, timeout=15)
                if file_res.status_code == 200:
                    file_bytes = file_res.content
                    mime_type = "application/pdf" if str(file_link).lower().endswith(".pdf") else "image/jpeg"
                    extracted_data = extract_with_gemini(file_bytes, mime_type)
                else:
                    print(f"⚠️ File download HTTP Status: {file_res.status_code}")
            except Exception as e:
                print(f"⚠️ Gemini/File Error for ID {vac_id}: {e}")
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    has_gemini_error = True

        if has_gemini_error:
            print(f"⏳ 503 Overload නිසා ID {vac_id} මඟහැර පසුවට තබන ලදී.")
            continue

        final_closing_date = extracted_data.get("closing_date") or closing

        # Post-Check
        if final_closing_date and is_post_expired(final_closing_date):
            print(f"⏩ [Post-Check] ID {vac_id} - Extracted Date Expired ({final_closing_date}). Skipping...")
            supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
            continue

        post_payload = {
            "title": extracted_data.get("title") or title,
            "organization": extracted_data.get("organization") or company,
            "category": extracted_data.get("category") or "Government Job",
            "meq_level": extracted_data.get("meq_level") or "NONE",
            "closing_date": final_closing_date,
            "salary_code": extracted_data.get("salary_code"),
            "salary_amount": extracted_data.get("salary_amount"),
            "description": extracted_data.get("description") or f"වැඩිවිස්තර සඳහා: {web_link}",
            "qualifications": extracted_data.get("qualifications") or "සඳහන් නැත",
            "pdf_url": file_link if is_file else web_link,
            "status": "PENDING",
            "source": "SL_JOB_SCRAPER"
        }

        try:
            # 2. Destination DB එකට Insert කිරීම
            supabase_dest.table("posts").insert(post_payload).execute()

            # 3. Source DB එකේ is_processed = true ලෙස mark කිරීම
            supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
            print(f"✅ Success: ID {vac_id} -> Admin Pending List එකට එක් විය.")

        except Exception as db_err:
            print(f"❌ DB Insert Error for ID {vac_id}: {db_err}")

if __name__ == "__main__":
    process_sl_job_scraper_vacancies()
