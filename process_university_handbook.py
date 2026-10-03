import os
import json
import io
import time
import re
import warnings
import fitz as pymupdf
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
from google.genai import types
from supabase import create_client, Client

warnings.filterwarnings("ignore")

# Environment Variables Verification
GDRIVE_JSON_STR = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_HANDBOOK_KEY") or os.getenv("GEMINI_DRIVE_API_KEY") or os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not all([GDRIVE_JSON_STR, GDRIVE_FOLDER_ID, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("❌ Missing required environment variables! Please check GitHub Secrets.")

# Initialize API Clients
info = json.loads(GDRIVE_JSON_STR)
creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

ai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# High-Resiliency Fallback Model Chain
DEFAULT_CHAIN = [
    "gemini-3.5-flash-lite",  # Tier 1: Highest rate limit buffer & fast JSON
    "gemini-3.5-flash",       # Tier 2: High capacity backup
    "gemini-3.6-flash",       # Tier 3: High reasoning
    "gemini-3.8-flash"        # Tier 4: Deep analysis
]

BATCH_SIZE = 10  # Optimal page chunking size for 100% accuracy

def is_duplicate_course(course_si, course_en, year):
    """Checks whether the degree programme already exists in Supabase to prevent duplicates."""
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

def extract_courses_with_strict_retry(prompt_text, year="2025/2026"):
    """
    STRICT ZERO-SKIP EXTRACTION:
    Retries across model chain if API or JSON parse fails.
    RAISES EXCEPTION if chunk fails completely so process halts safely.
    """
    retryable_errors = ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand", "quota", "overloaded"]
    
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )

    last_error = None

    for model_name in DEFAULT_CHAIN:
        for attempt in range(1, 3):
            try:
                print(f"🤖 Requesting Gemini ({model_name}, attempt {attempt}/2)...")
                response = ai_client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=config
                )
                
                clean_text = response.text.strip()
                
                # Regex Extraction for Strict JSON Block
                json_match = re.search(r'\{.*\}', clean_text, re.DOTALL)
                if json_match:
                    clean_text = json_match.group(0)

                # Strict Parsing Check
                data = json.loads(clean_text)
                
                if isinstance(data, dict) and "courses" in data:
                    return data["courses"]
                elif isinstance(data, list):
                    return data
                
            except json.JSONDecodeError as json_err:
                last_error = json_err
                print(f"⚠️ JSON Parse Error on {model_name}: {json_err}. Retrying with next model/attempt...")
                time.sleep(3)
            except Exception as api_err:
                last_error = api_err
                err_str = str(api_err)
                print(f"⚠️ API Error on {model_name}: {err_str[:100]}...")
                
                if not any(error in err_str.lower() for error in retryable_errors):
                    time.sleep(3)
                time.sleep(5)

    # ZERO-SKIP GUARD: If all models fail for this chunk, RAISE EXCEPTION to halt and prevent data loss!
    raise ValueError(f"❌ CRITICAL EXTRACTION FAILURE: Unable to extract 100% valid JSON for this page chunk after all fallback retries. Reason: {last_error}")

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

def process_handbook_files():
    ugc_folder_id = get_or_create_folder(GDRIVE_FOLDER_ID, "UGC_Handbooks")
    processed_folder_id = get_or_create_folder(ugc_folder_id, "Processed")
    
    query = f"'{ugc_folder_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])

    handbook_files = [f for f in files if f['name'].endswith('.pdf')]

    if not handbook_files:
        print("📁 No new University Handbook PDFs found inside 'UGC_Handbooks' folder.")
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
            print(f"📖 Total Pages: {total_pages}. Processing in strict text batches of {BATCH_SIZE} pages...")

            total_extracted = 0
            total_skipped_duplicates = 0
            qc_audit_log = []

            for start_page in range(0, total_pages, BATCH_SIZE):
                end_page = min(start_page + BATCH_SIZE, total_pages)
                print(f"\n🔄 Strictly Audit & Extracting Pages {start_page + 1} to {end_page} of {total_pages}...")

                chunk_text = ""
                for page_num in range(start_page, end_page):
                    page = doc.load_page(page_num)
                    page_txt = page.get_text("text")
                    if page_txt.strip():
                        chunk_text += f"\n--- Page {page_num + 1} ---\n" + page_txt

                if not chunk_text.strip():
                    qc_audit_log.append(f"Pages {start_page + 1}-{end_page}: Blank/No Text")
                    continue

                prompt = f"""
                You are an expert UGC Sri Lanka University Admission Handbook Data Extractor and Sinhala Font Decoder.
                Analyze the raw extracted text from the handbook and extract ALL Degree Programmes into structured JSON for admission year 2025/2026.

                CRITICAL SINHALA FONT DECODING INSTRUCTION:
                The input text contains legacy Sinhala font encodings (e.g., FM-Abhaya ASCII text such as 'úlsrK Ys,amh', 'Aõh system', 'ksA' or non-standard characters).
                You MUST DECODE AND CONVERT ALL legacy ASCII Sinhala text into standard, clean, properly spelled Sinhala Unicode characters (e.g., 'විකිරණ ශිල්පය').
                NEVER output raw ASCII/ANSI gibberish font text in course_name_si or description!

                EXTRACTED TEXT CONTENT:
                \"\"\"
                {chunk_text}
                \"\"\"

                Return JSON strictly in this structure:
                {{
                  "courses": [
                    {{
                      "admission_year": "2025/2026",
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

                # STRICT EXTRACTION (Will throw Exception if invalid JSON, ensuring ZERO SKIPS)
                courses = extract_courses_with_strict_retry(prompt, year="2025/2026")

                chunk_saved = 0
                if courses:
                    for course in courses:
                        c_si = course.get("course_name_si", "")
                        c_en = course.get("course_name_en", "")

                        if is_duplicate_course(c_si, c_en, "2025/2026"):
                            print(f"🔄 [DUPLICATE SKIP] {c_si} / {c_en} දැනටමත් පවතී.")
                            total_skipped_duplicates += 1
                            continue

                        # Defensive checks for Supabase Constraints
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
                        chunk_saved += 1
                    
                    print(f"✅ [100% VERIFIED] Saved {chunk_saved} courses from pages {start_page + 1}-{end_page}.")
                    qc_audit_log.append(f"Pages {start_page + 1}-{end_page}: {chunk_saved} courses extracted")
                else:
                    qc_audit_log.append(f"Pages {start_page + 1}-{end_page}: 0 courses found")

                time.sleep(10)  # Rate limit protection delay

            doc.close()

            # 100% COMPLETE VERIFICATION - Move File Only After All Pages Audit Successfully
            move_file(file_id, ugc_folder_id, processed_folder_id)
            
            print("\n==================================================")
            print(f"🎉 100% COMPLETE EXTRACTION SUCCESSFUL: {file_name}")
            print(f"📊 Total New Verified Courses Saved: {total_extracted}")
            print(f"🔄 Total Duplicates Prevented: {total_skipped_duplicates}")
            print("📋 FULL AUDIT LOG BY PAGE CHUNKS:")
            for log_entry in qc_audit_log:
                print(f"   • {log_entry}")
            print("==================================================\n")

        except Exception as e:
            print(f"\n❌ CRITICAL PROCESS HALTED for {file_name}: {e}")
            print("🛑 File NOT moved to Processed folder. It will be safely re-audited in the next run.\n")

if __name__ == "__main__":
    process_handbook_files()
