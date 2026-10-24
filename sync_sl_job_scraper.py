import os
import json
import time
import re
import datetime
from datetime import timezone, timedelta
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import pymupdf  # PDF to Image Converter
from PIL import Image
import io
from google import genai
from google.genai import types
from supabase import create_client, Client

# Environment Variables
SRC_SUPABASE_URL = os.getenv("SRC_SUPABASE_URL")
SRC_SUPABASE_KEY = os.getenv("SRC_SUPABASE_SERVICE_ROLE_KEY")

DEST_SUPABASE_URL = os.getenv("DEST_SUPABASE_URL") or os.getenv("SUPABASE_URL")
DEST_SUPABASE_KEY = os.getenv("DEST_SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

GEMINI_API_KEY = os.getenv("GEMINI_JOB_SCRAPER_KEY") or os.getenv("GEMINI_API_KEY")

if not all([SRC_SUPABASE_URL, SRC_SUPABASE_KEY, DEST_SUPABASE_URL, DEST_SUPABASE_KEY, GEMINI_API_KEY]):
    raise ValueError("❌ Missing required environment variables! Please check GitHub Secrets.")

supabase_src: Client = create_client(SRC_SUPABASE_URL, SRC_SUPABASE_KEY)
supabase_dest: Client = create_client(DEST_SUPABASE_URL, DEST_SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

SL_TZ = timezone(timedelta(hours=5, minutes=30))

# AI Fallback Model Chain
DEFAULT_CHAIN = [
    "gemini-3.8-flash",       
    "gemini-3.6-flash",       
    "gemini-3.5-flash",       
    "gemini-3.5-flash-lite",  
    "gemini-3.1-flash-lite"   
]

HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,pdf;q=0.8,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,si;q=0.8'
}

# 🔴 SMART FILTER: Blacklisted Domains & Keywords (Updated with localhost)
JUNK_DOMAINS = [
    'googletagmanager', 'google-analytics', 'facebook.com/tr', 'youtube.com', 
    'vimeo.com', 'fonts.googleapis', 'twitter.com', 'localhost', '127.0.0.1', 
    'test.com', 'example.com'
]
JUNK_IMAGE_KEYWORDS = ['logo', 'icon', 'banner', 'thumb', 'avatar', 'bg', 'background', 'footer', 'header', 'social', 'button', 'qr', 'ratesheet', 'advertise', 'payment']

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
        expiration_limit = today_sl - datetime.timedelta(days=90)
        if closing_date < expiration_limit:
            return True
    except ValueError:
        pass
    return False

def is_duplicate_post(title, organization, pdf_url, closing_date=None):
    try:
        if pdf_url:
            res_url = supabase_dest.table("posts").select("id").eq("pdf_url", pdf_url).limit(1).execute()
            if res_url.data and len(res_url.data) > 0:
                return True

        if title and organization and title != "Job Vacancy" and organization != "N/A":
            query = supabase_dest.table("posts").select("id").ilike("title", title).ilike("organization", organization)
            if closing_date:
                query = query.eq("closing_date", closing_date)
            res_meta = query.limit(1).execute()
            if res_meta.data and len(res_meta.data) > 0:
                return True
    except Exception as e:
        print(f"⚠️ Duplicate check validation warning: {e}")
    return False

def get_url_with_ssl_fallback(url, timeout=15):
    try:
        return requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
    except requests.exceptions.SSLError as e:
        print(f"⚠️ SSL Verification failed for {url}. Attempting insecure fallback...")
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return requests.get(url, headers=HTTP_HEADERS, timeout=timeout, verify=False)
    except requests.exceptions.RequestException as e:
        print(f"❌ Network Error for {url}: {e}")
        raise

def is_valid_flyer_image(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
        
        if width < 300 or height < 300:
            print(f"   🚫 [SMART REJECT] Image too small ({width}x{height}). Likely a logo/icon.")
            return False
            
        aspect_ratio = width / height
        if aspect_ratio > 3.0 or aspect_ratio < 0.3:
            print(f"   🚫 [SMART REJECT] Extreme Aspect Ratio ({aspect_ratio:.2f}). Likely a banner.")
            return False
            
        return True
    except Exception as e:
        print(f"   🚫 [SMART REJECT] Image validation error (Corrupt Image): {e}")
        return False 

def resolve_actual_file_url(url):
    if not url or not isinstance(url, str):
        return None
    
    if any(domain in url.lower() for domain in JUNK_DOMAINS):
        print(f"   🚫 [SMART REJECT] Blocked junk domain: {url}")
        return None

    lower_url = url.lower()
    if any(lower_url.endswith(ext) for ext in ['.pdf', '.jpg', '.jpeg', '.png', '.webp']):
        if any(keyword in lower_url for keyword in JUNK_IMAGE_KEYWORDS):
            print(f"   🚫 [SMART REJECT] Direct URL contains junk keyword: {url}")
            return None
        return url

    try:
        print(f"🔍 Inspecting webpage for embedded document: {url}")
        res = get_url_with_ssl_fallback(url) 
        if res and res.status_code == 200:
            content_type = res.headers.get('Content-Type', '').lower()
            if 'application/pdf' in content_type or 'image/' in content_type:
                return url
            
            soup = BeautifulSoup(res.text, 'html.parser')
            page_title = soup.title.string.lower() if soup.title and soup.title.string else ""
            valid_title_keywords = ['vacancy', 'vacancies', 'career', 'job', 'post', 'application', 'recruit', 'ඇබෑර්තු', 'රැකියා']
            if not any(kw in page_title for kw in valid_title_keywords):
                print(f"   ⚠️ [WARNING] Page title '{page_title}' does not seem job-related. Still checking cautiously.")

            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                if '.pdf' in href.lower() and not any(kw in href.lower() for kw in ['guideline', 'policy']):
                    found_url = urljoin(url, href)
                    print(f"🎯 Found PDF link inside HTML: {found_url}")
                    return found_url
            
            possible_images = []
            for img in soup.find_all(['img', 'embed', 'iframe'], src=True):
                src = img.get('src', '')
                src_lower = src.lower()
                
                if any(bad_word in src_lower for bad_word in JUNK_IMAGE_KEYWORDS):
                    continue
                if any(domain in src_lower for domain in JUNK_DOMAINS):
                    continue
                
                if any(ext in src_lower for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                    full_url = urljoin(url, src)
                    if 'wp-content/plugins' in full_url or 'social-media' in full_url:
                        continue
                    possible_images.append(full_url)
            
            if possible_images:
                best_image_url = possible_images[-1] 
                print(f"🎯 Found High-Probability Job Flyer Image: {best_image_url}")
                return best_image_url
                
    except Exception as e:
        print(f"⚠️ Webpage link resolution warning: {e}")
        
    return None

def convert_pdf_to_images(pdf_bytes, max_pages=3):
    images = []
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total_pages = min(len(doc), max_pages)
        print(f"🖼️ Converting PDF ({total_pages} pages) to High-Res Images for OCR...")
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("jpeg")
            images.append(img_bytes)
    except Exception as e:
        print(f"⚠️ PDF to Image conversion failed: {e}")
    return images

def extract_with_gemini_vision(image_bytes_list):
    prompt = """
    You are an expert Sri Lankan Job Advertisement, Course Notice, and Public Exam OCR Specialist.
    Carefully inspect the attached document image(s) from top to bottom.

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
    contents = []
    for img_bytes in image_bytes_list:
        contents.append(genai.types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))
    contents.append(prompt)

    config = types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json")
    last_error = None

    for model_name in DEFAULT_CHAIN:
        try:
            print(f"🤖 Calling AI Vision Model: {model_name}...")
            response = ai_client.models.generate_content(model=model_name, contents=contents, config=config)
            data = json.loads(response.text.strip())
            print(f"✅ Extraction Successful with Model [{model_name}]")
            return data
        except Exception as e:
            last_error = e
            print(f"⚠ Model [{model_name}] failed: {e}. Trying fallback model...")
            time.sleep(2)

    raise RuntimeError(f"❌ All Gemini models in DEFAULT_CHAIN failed! Last error: {last_error}")

def process_sl_job_scraper_vacancies():
    print("🔄 `sl-job-scraper` (SMART SYNC) process ආරම්භ විය...")
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

            print(f"\n📄 Processing ID {vac_id}: {title} ({company})...")

            # PRE-CHECK 1: Expiration & Source DB Duplicates check
            if clean_closing and is_post_expired(clean_closing):
                print(f"⏩ [Pre-Check] ID {vac_id} - Post Expired ({clean_closing}). Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            if is_duplicate_post(title, company, None, None):
                print(f"⏩ [Pre-Check] ID {vac_id} - ({title} | {company}) දැනටමත් Admin Panel හි ඇත. Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            extracted_data = {}
            has_gemini_error = False

            raw_target_url = file_link if (file_link and str(file_link).startswith("http")) else web_link
            target_file_url = resolve_actual_file_url(raw_target_url)

            if not target_file_url or 'localhost' in target_file_url.lower():
                print(f"🚫 [SMART REJECT] ID {vac_id} - Invalid or Localhost URL. Marking as Junk.")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            # PRE-CHECK 2: AI එකට යවන්න කලින් URL එක Admin panel එකේ තියෙනවද බලන්න
            if is_duplicate_post(None, None, target_file_url, None):
                print(f"⏩ [Pre-Check] ID {vac_id} - Document URL දැනටමත් Admin Panel හි ඇත. Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            try:
                print(f"📥 Downloading document/file from: {target_file_url}")
                file_res = get_url_with_ssl_fallback(target_file_url, timeout=25) 
                
                if file_res and file_res.status_code == 200:
                    file_bytes = file_res.content
                    is_pdf = ".pdf" in target_file_url.lower() or "application/pdf" in file_res.headers.get("Content-Type", "").lower()
                    
                    if not is_pdf:
                        if not is_valid_flyer_image(file_bytes):
                            supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                            total_skipped += 1
                            continue
                    
                    images_to_process = []
                    if is_pdf:
                        images_to_process = convert_pdf_to_images(file_bytes, max_pages=3)
                    else:
                        images_to_process = [file_bytes]

                    if images_to_process:
                        print(f"📸 Sending {len(images_to_process)} image(s) to Gemini Vision OCR...")
                        extracted_data = extract_with_gemini_vision(images_to_process)
                    else:
                        print("⚠️ No valid images extracted from document.")
                        has_gemini_error = True
                else:
                    print(f"⚠️ Download failed HTTP Status: {file_res.status_code if file_res else 'Unknown'}")
                    has_gemini_error = True
            except Exception as e:
                print(f"⚠️ Extraction Error for ID {vac_id}: {e}")
                has_gemini_error = True

            # 🔴 FAIL-SAFE: Error ආවත් is_processed = True කර ලූප් එක නතර කිරීම
            if has_gemini_error:
                print(f"⏳ Processing error for ID {vac_id}. Marking as processed to prevent infinite loops.")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            is_valid_ad = extracted_data.get("is_valid_ad", True)
            if not is_valid_ad:
                reason = extracted_data.get("rejection_reason", "Not a job or course vacancy")
                print(f"🗑️ [JUNK REJECTED] ID {vac_id} - {reason}. Skipping & marking processed...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            gemini_date = clean_date_format(extracted_data.get("closing_date"))
            final_closing_date = gemini_date or clean_closing

            if final_closing_date and is_post_expired(final_closing_date):
                print(f"⏩ [Post-Check] ID {vac_id} - Extracted Date Expired ({final_closing_date}). Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

            final_title = extracted_data.get("title") or title
            final_org = extracted_data.get("organization") or company

            # FINAL CHECK: AI එකෙන් ගත් නිවැරදි නම සහ ආයතනය Database එකේ තිබේදැයි බැලීම
            if is_duplicate_post(final_title, final_org, target_file_url, final_closing_date):
                print(f"🔄 [DUPLICATE DETECTED] ID {vac_id} - ({final_title} | {final_org}) දැනටමත් පවතී. Skipping...")
                supabase_src.table("vacancies").update({"is_processed": True}).eq("id", vac_id).execute()
                total_skipped += 1
                continue

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
