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

# urllib3 SSL Warnings පාලනය කිරීම
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Environment Variables
SRC_SUPABASE_URL = os.getenv("SRC_SUPABASE_URL")
SRC_SUPABASE_KEY = os.getenv("SRC_SUPABASE_SERVICE_ROLE_KEY")

DEST_SUPABASE_URL = os.getenv("DEST_SUPABASE_URL") or os.getenv("SUPABASE_URL")
DEST_SUPABASE_KEY = os.getenv("DEST_SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([SRC_SUPABASE_URL, SRC_SUPABASE_KEY, DEST_SUPABASE_URL, DEST_SUPABASE_KEY, GEMINI_API_KEY]):
    raise ValueError("❌ Missing required environment variables! Please check GitHub Secrets.")

supabase_src: Client = create_client(SRC_SUPABASE_URL, SRC_SUPABASE_KEY)
supabase_dest: Client = create_client(DEST_SUPABASE_URL, DEST_SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

SL_TZ = timezone(timedelta(hours=5, minutes=30))

# AI Fallback Model Chain
DEFAULT_CHAIN = [
    "gemini-3.8-flash",       # Tier 1: Primary Model (High Accuracy)
    "gemini-3.6-flash",       # Tier 2: Fast & Reliable Backup
    "gemini-3.5-flash",       # Tier 3: Workhorse Backup
    "gemini-3.5-flash-lite",  # Tier 4: Google Recommended Lite Model
    "gemini-3.1-flash-lite"   # Tier 5: High Rate Limit Buffer
]

# Custom Headers - Govt/Sri Lankan Sites Block වීම වැළැක්වීමට
HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,pdf;q=0.8,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,si;q=0.8'
}

def clean_date_format(date_str):
    if not date_str or not isinstance(date_str, str):
        return None
    
    cleaned = date_str.strip()
    if cleaned.upper() in ["N/A", "NONE", "NULL", ""]:
        return None

    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', cleaned)
    if date_match:
        return date_match.group(1)
    
    return None

def is_post_expired(closing_date_str):
    if not closing_date_str:
        return False

    try:
        y, m, d = map(int, closing_date_str.split('-'))
        closing_date = datetime.date(y, m, d)
        today_sl = datetime.datetime.now(SL_TZ).date()

        if closing_date < today_sl:
            return True
    except ValueError:
        pass

    return False

def is_duplicate_post(title, organization, pdf_url, closing_date=None):
    """
    Target Database එකෙහි දැනටමත් මෙම Job / Course post එක පවතීදැයි Title, Organization, PDF URL මඟින් පරීක්ෂා කරයි.
    """
    try:
        # Check 1: exact PDF URL match
        if pdf_url:
            res_url = supabase_dest.table("posts").select("id").eq("pdf_url", pdf_url).limit(1).execute()
            if res_url.data and len(res_url.data) > 0:
                return True

        # Check 2: Title and Organization match
        if title and organization:
            query = supabase_dest.table("posts").select("id").ilike("title", title).ilike("organization", organization)
            if closing_date:
                query = query.eq("closing_date", closing_date)
            res_meta = query.limit(1).execute()
            if res_meta.data and len(res_meta.data) > 0:
                return True

    except Exception as e:
        print(f"⚠️ Duplicate check validation warning: {e}")

    return False

def extract_with_gemini(file_bytes, mime_type):
    prompt = """
    You are an expert Sri Lankan Job Advertisement, Course Notice, and Public Exam Classifier/OCR Specialist.
    Carefully inspect the attached document (PDF or Image) from top to bottom.

    CRITICAL VALIDATION RULE (is_valid_ad):
    Analyze if this document is a VALID recruitment notice, course admission, or public exam notice.
    
    - SET "is_valid_ad": true ONLY IF it is one of the following:
      1. Job Vacancy / Employment Notice (රැකියා පුරප්පාඩු).
      2. Course, Higher Education, Diploma, University, or Vocational Training Admission Notice (පාඨමාලා / අධ්‍යාපන ඇතුළත් කරගැනීම්).
      3. Efficiency Bar Exam or Government Competitive Examination Notice (කඩඉම් / තරඟ විභාග).

    - SET "is_valid_ad": false IF it is ANY of the following JUNK/IRRELEVANT notices:
      - Procurement, Tender notices, Bidding announcements (ටෙන්ඩර්, ලංසු කැඳවීම්).
      - Land, Vehicle, Property Auction or Bank foreclosures (වෙන්දේසි, රාජසන්තක කිරීම්).
      - General public announcements, circulars, meeting notices, press releases, or political news.
      - Blurry, unreadable, blank, or incomplete documents without clear title or issuing authority.

    Required Strict JSON format:
    {
      "is_valid_ad": true,
      "rejection_reason": "Provide reason if is_valid_ad is false, else null",
      "title": "Exact job position, course title, or exam name in Sinhala or English (Do NOT use generic titles like 'Vacancy')",
      "organization": "Exact Ministry, Department, University, Institute, or Company Name",
      "category": "Must be one of: 'Government Job', 'Semi-Govt Job', 'Private Job', 'Course', 'කඩඉම් විභාග'",
      "meq_level": "Must be one of: 'OL', 'AL', 'NVQ', 'DEGREE', 'POST_GRAD', 'NONE'",
      "closing_date": "Closing Date in YYYY-MM-DD format if mentioned in document, else null",
      "salary_code": "Salary code if present (e.g. MN-1, SL-1), else null",
      "salary_amount": "Salary scale or monthly stipend, else null",
      "description": "Clear summary of the advertisement or course details in Sinhala.",
      "qualifications": "Educational & experience qualifications or course entry requirements in clear Sinhala bullet points."
    }
    Respond strictly with valid JSON.
    """

    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )

    last_error = None

    # Fallback Model Chain එක හරහා Call කිරීම
    for model_name in DEFAULT_CHAIN:
        try:
            print(f"🤖 Calling AI Model: {model_name}...")
            response = ai_client.models.generate_content(
                model=model_name,
                contents=[genai.types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
                config=config
            )
            data = json.loads(response.text.strip())
            print(f"✅ Extraction Successful with Model [{model_name}]")
            return data
        except Exception as e:
            last_error = e
            print(f"⚠️ Model [{model_name}] failed/rate-limited: {e}. Trying fallback model...")
            time.sleep(2)

    raise RuntimeError(f"❌ All Gemini models in DEFAULT_CHAIN failed! Last error: {last_error}")

def is_valid_file_url(url):
    """URL එක PDF එකක්ද නැතහොත් Image එකක්ද යන්න පරීක්ෂා කරයි."""
    if not url or not isinstance(url, str):
        return False
    lower_url = url.lower()
    return any(ext in lower_url for ext in ['.pdf', '.png', '.jpg', '.jpeg', '.webp']) or '/vacancies/' in lower_url or 'paper-advertisement' in lower_url

def process_sl_job_scraper_vacancies():
    print("🔄 `sl-job-scraper` sync process ආරම්භ විය...")

    total_processed = 0
    total_skipped = 0
    batch_num = 1
    max_batches = 30

    while batch_num <= max_batches:
        res = supabase_src.table("vacancies").select("*").eq("is_processed", False).order("id", desc=False).limit(15).execute()
        vacancies = res.data or []

        if not vacancies:
            print(f"✨ සියලුම Unprocessed Vacancies Process කර අවසන්! (එකතුව: {total_processed}, Reject/Skip වූ ගණන: {total_skipped})")
            break

        print(f"\n📦 Batch {batch_num}: Vacancies {len(vacancies)}ක් Process කරමින් පවතී...")

        for vac in vacancies:
            vac_id = vac["id"]
            company = vac.get("company_name", "N/A")
            title = vac.get("post_title", "Job Vacancy")
            raw_closing = vac.get("closing_date")
            clean_closing = clean_date_format(raw_closing)
            
            web_link = vac.get("web_link", "") or ""
            file_link = vac.get("file_link") or ""
            is_file = vac.get("is_file", False)

            print(f"\n📄 Processing ID {vac_id}: {title} ({company})...")

            # Pre-Check: Expired Posts
            if clean_closing and is_post_expired(clean_closing):
                print(f"⏩ [Pre-Check] ID {vac_id} - Post Expired ({clean_closing}). Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            extracted_data = {}
            has_gemini_error = False

            # PDF / File URL එක තීරණය කිරීම
            target_file_url = None
            if is_file and file_link and str(file_link).startswith("http"):
                target_file_url = file_link
            elif is_valid_file_url(web_link):
                target_file_url = web_link
            elif file_link and is_valid_file_url(file_link):
                target_file_url = file_link

            if target_file_url:
                try:
                    print(f"📥 Downloading document from: {target_file_url}")
                    file_res = requests.get(target_file_url, headers=HTTP_HEADERS, timeout=20, verify=False)
                    if file_res.status_code == 200:
                        file_bytes = file_res.content
                        mime_type = "application/pdf" if ".pdf" in target_file_url.lower() else "image/jpeg"
                        
                        extracted_data = extract_with_gemini(file_bytes, mime_type)
                    else:
                        print(f"⚠️ File download failed HTTP Status: {file_res.status_code}")
                except Exception as e:
                    print(f"⚠️ Gemini Extract Error for ID {vac_id}: {e}")
                    has_gemini_error = True

            # Gemini Error එකක් ආවොත් (All models failed) පසුවට තබයි
            if has_gemini_error:
                print(f"⏳ All Models failed/overloaded. ID {vac_id} මඟහැර පසුවට තබන ලදී.")
                continue

            # AI Junk Detection Verification
            is_valid_ad = extracted_data.get("is_valid_ad", True)
            if not is_valid_ad:
                reason = extracted_data.get("rejection_reason", "Not a job or course vacancy")
                print(f"🗑️ [JUNK REJECTED] ID {vac_id} - {reason}. Skipping & marking processed...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            gemini_date = clean_date_format(extracted_data.get("closing_date"))
            final_closing_date = gemini_date or clean_closing

            # Post-Check Expired Date
            if final_closing_date and is_post_expired(final_closing_date):
                print(f"⏩ [Post-Check] ID {vac_id} - Extracted Date Expired ({final_closing_date}). Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            final_title = extracted_data.get("title") or title
            final_org = extracted_data.get("organization") or company

            # Duplicate Check
            if is_duplicate_post(final_title, final_org, target_file_url, final_closing_date):
                print(f"🔄 [DUPLICATE DETECTED] ID {vac_id} - ({final_title} | {final_org}) දැනටමත් පවතී. Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            # Destination Table Insert Payload
            post_payload = {
                "title": final_title,
                "organization": final_org,
                "category": extracted_data.get("category") or "Government Job",
                "meq_level": extracted_data.get("meq_level") or "NONE",
                "closing_date": final_closing_date,
                "salary_code": extracted_data.get("salary_code"),
                "salary_amount": extracted_data.get("salary_amount"),
                "description": extracted_data.get("description") or f"වැඩිවිස්තර සඳහා: {web_link or target_file_url}",
                "qualifications": extracted_data.get("qualifications") or "සඳහන් නැත",
                "pdf_url": target_file_url or web_link,
                "status": "PENDING",
                "source": "SL_JOB_SCRAPER"
            }

            try:
                supabase_dest.table("posts").insert(post_payload).execute()
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                print(f"✅ Success: ID {vac_id} ({final_title}) -> Admin Pending List එකට එක් විය.")
                total_processed += 1

            except Exception as db_err:
                print(f"❌ DB Insert Error for ID {vac_id}: {db_err}")

        batch_num += 1
        time.sleep(2)

    print(f"\n🎉 Sync ක්‍රියාවලිය සම්පූර්ණයි! එකතු කළ ගණන: {total_processed} | Reject/Skip කළ ගණන: {total_skipped}")

if __name__ == "__main__":
    process_sl_job_scraper_vacancies()
