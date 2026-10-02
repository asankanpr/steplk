import os
import json
import io
import time
import warnings
import fitz as pymupdf
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
from google.genai import types
from supabase import create_client, Client

warnings.filterwarnings("ignore")

# Environment Variables
GDRIVE_JSON_STR = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_DRIVE_API_KEY") or os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not all([GDRIVE_JSON_STR, GDRIVE_FOLDER_ID, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("❌ Missing required environment variables!")

info = json.loads(GDRIVE_JSON_STR)
creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

ai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Stable Fallback Model Chain
DEFAULT_CHAIN = [
    "gemini-3.5-flash-lite",  # Tier 1: Highest rate limit buffer & zero 429 errors
    "gemini-3.5-flash",       # Tier 2: High capacity backup
    "gemini-3.6-flash",       # Tier 3
    "gemini-3.8-flash"        # Tier 4
]

BATCH_SIZE = 10  # 10 pages per batch for maximum accuracy and zero omissions

def is_duplicate_course(course_si, course_en, year):
    """Smart Duplicate Checker before DB insertion"""
    try:
        if course_en and str(course_en).strip():
            res_en = supabase.table("university_courses").select("id").eq("admission_year", year).ilike("course_name_en", course_en.strip()).limit(1).execute()
            if res_en.data and len(res_en.data) > 0:
                return True

        if course_si and str(course_si).strip():
            res_si = supabase.table("university_courses").select("id").eq("admission_year", year).ilike("course_name_si", course_si.strip()).limit(1).execute()
            if res_si.data and len(res_si.data) > 0:
                return True

    except Exception as e:
        print(f"⚠️ Duplicate validation warning: {e}")

    return False

def generate_content_with_retry(prompt_text):
    retryable_errors = ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand", "quota", "overloaded"]
    model_not_found_errors = ["404", "NOT_FOUND", "not found"]
    
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )

    last_error = None

    for model_name in DEFAULT_CHAIN:
        for attempt in range(1, 3):
            try:
                print(f"🤖 Requesting Gemini ({model_name}, attempt {attempt}/2)...")
                return ai_client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=config
                )
            except Exception as e:
                last_error = e
                err_str = str(e)
                print(f"⚠️ Gemini notice ({model_name}): {err_str[:100]}...")

                if any(error in err_str.lower() for error in model_not_found_errors):
                    break
                
                if not any(error in err_str.lower() for error in retryable_errors):
                    raise e

                time.sleep(5)

    raise last_error

def get_or_create_folder(parent_folder_id, folder_name):
    query = f"'{parent_folder_id}' in parents and name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get('files', [])
    if folders:
        return folders[0]['id']
    else:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id]
        }
        folder = drive_service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')

def move_file(file_id, current_folder_id, target_folder_id):
    drive_service.files().update(
        fileId=file_id,
        addParents=target_folder_id,
        removeParents=current_folder_id,
        fields='id, parents'
    ).execute()

def extract_courses_from_clean_text(extracted_text, year="2025/2026"):
    prompt = f"""
    You are an expert UGC Sri Lanka University Admission Handbook Data Extractor and Sinhala Font Decoder.
    Analyze the raw extracted text from the handbook and extract ALL Degree Programmes into structured JSON for admission year {year}.

    CRITICAL SINHALA FONT DECODING INSTRUCTION:
    The input text contains legacy Sinhala font encodings (e.g. FM-Abhaya ASCII text such as 'úlsrK Ys,amh', 'Aõh system', 'ksA' or non-standard characters).
    You MUST DECODE AND CONVERT ALL legacy ASCII Sinhala text into standard, clean, properly spelled Sinhala Unicode characters (e.g., 'විකිරණ ශිල්පය').
    NEVER output raw ASCII/ANSI gibberish font text in course_name_si or description!

    EXTRACTED TEXT CONTENT:
    \"\"\"
    {extracted_text}
    \"\"\"

    Return JSON strictly in this structure:
    {{
      "courses": [
        {{
          "admission_year": "{year}",
          "course_name_si": "පාඨමාලාවේ නම (Clean Standard Sinhala Unicode)",
          "course_name_en": "Official Course Name in English",
          "degree": "Awarded Degree (e.g., BSc, BA, BTech, MBBS, BEd)",
          "stream": "Must be ONE of: 'Biological Science', 'Physical Science', 'Commerce', 'Arts', 'Technology', 'Common'",
          "subject_requirements": ["Subject 1", "Subject 2", "Subject 3"],
          "other_requirements": "O/L passes, Aptitude Tests, or special criteria if any, else null",
          "duration_years": 4,
          "universities": ["University of Colombo", "University of Peradeniya"],
          "description": "Brief course description in Clean Sinhala Unicode"
        }}
      ]
    }}
    If no degree course details are found in this text segment, return {{"courses": []}}.
    Respond strictly with valid JSON.
    """

    response = generate_content_with_retry(prompt)
    clean_text = response.text.strip()
    data = json.loads(clean_text)

    if isinstance(data, dict) and "courses" in data:
        return data["courses"]
    elif isinstance(data, list):
        return data
    return []

def process_handbook_files():
    processed_folder_id = get_or_create_folder(GDRIVE_FOLDER_ID, "Processed")
    
    query = f"'{GDRIVE_FOLDER_ID}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])

    handbook_files = [f for f in files if "handbook" in f['name'].lower() or "ugc" in f['name'].lower() or "university" in f['name'].lower() or f['name'].endswith('.pdf')]

    if not handbook_files:
        print("📁 No new University Handbook PDFs found in Google Drive main folder.")
        return

    for file in handbook_files:
        file_id = file['id']
        file_name = file['name']
        
        print(f"\n📚 Processing University Handbook: {file_name}...")

        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        
        pdf_bytes = fh.getvalue()

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            total_pages = len(doc)
            print(f"📖 Total Pages: {total_pages}. Processing in text batches of {BATCH_SIZE} pages...")

            total_extracted = 0
            total_skipped_duplicates = 0

            for start_page in range(0, total_pages, BATCH_SIZE):
                end_page = min(start_page + BATCH_SIZE, total_pages)
                print(f"\n🔄 Pages {start_page + 1} to {end_page}...")

                chunk_text = ""
                for page_num in range(start_page, end_page):
                    page = doc.load_page(page_num)
                    page_txt = page.get_text("text")
                    if page_txt.strip():
                        chunk_text += f"\n--- Page {page_num + 1} ---\n" + page_txt

                if not chunk_text.strip():
                    continue

                courses = extract_courses_from_clean_text(chunk_text, year="2025/2026")

                if courses:
                    for course in courses:
                        c_si = course.get("course_name_si", "")
                        c_en = course.get("course_name_en", "")

                        # Smart Duplicate Pre-Check
                        if is_duplicate_course(c_si, c_en, "2025/2026"):
                            print(f"🔄 [DUPLICATE SKIP] {c_si} / {c_en} දැනටමත් පවතී.")
                            total_skipped_duplicates += 1
                            continue

                        # Ensure array/not-null safety
                        if not course.get("subject_requirements"):
                            course["subject_requirements"] = []
                        if not course.get("universities"):
                            course["universities"] = []
                        if not course.get("degree"):
                            course["degree"] = "General Degree"
                        if not course.get("stream"):
                            course["stream"] = "Common"

                        supabase.table("university_courses").insert(course).execute()
                        total_extracted += 1
                    
                    print(f"✅ Saved courses batch from pages {start_page + 1}-{end_page}.")

                time.sleep(12)  # Rate limit protection

            doc.close()
            move_file(file_id, GDRIVE_FOLDER_ID, processed_folder_id)
            print(f"\n🎉 Successfully processed {file_name}!")
            print(f"📊 Total new courses saved: {total_extracted} | Duplicates skipped: {total_skipped_duplicates}")

        except Exception as e:
            print(f"❌ Error processing handbook {file_name}: {e}")

if __name__ == "__main__":
    process_handbook_files()
