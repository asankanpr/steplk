import os
import json
import io
import time
import warnings
import fitz as pymupdf  # Fast & accurate Sinhala/English Unicode text extractor
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
    raise ValueError("❌ Missing required environment variables! Please check GitHub Secrets.")

# Initialize Clients
info = json.loads(GDRIVE_JSON_STR)
creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

ai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 5-Tier Fallback Model Chain
DEFAULT_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

BATCH_SIZE = 15  # Text පමණක් බැවින් පිටු 15ක් එකවර ගත හැක

def generate_content_with_retry(prompt_text):
    retryable_errors = ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand", "quota", "overloaded"]
    model_not_found_errors = ["404", "NOT_FOUND", "not found"]
    
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )

    last_error = None

    for model_name in DEFAULT_CHAIN:
        for attempt in range(1, 4):
            try:
                print(f"🤖 Requesting Gemini ({model_name}, attempt {attempt}/3)...")
                return ai_client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=config
                )
            except Exception as e:
                last_error = e
                err_str = str(e)
                print(f"⚠️ Gemini notice ({model_name}): {err_str[:120]}...")

                if any(error in err_str.lower() for error in model_not_found_errors):
                    break
                
                if not any(error in err_str.lower() for error in retryable_errors):
                    raise e

                if attempt < 3:
                    time.sleep(10)

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
    You are an expert UGC Sri Lanka University Admission Handbook Data Extractor.
    Analyze the following raw extracted text from the handbook and extract ALL Degree Programmes mentioned into structured JSON for admission year {year}.

    EXTRACTED TEXT CONTENT:
    \"\"\"
    {extracted_text}
    \"\"\"

    Return JSON strictly in this structure:
    {{
      "courses": [
        {{
          "admission_year": "{year}",
          "course_name_si": "පාඨමාලාවේ නම (සිංහලෙන්)",
          "course_name_en": "Official Course Name in English",
          "degree": "Awarded Degree (e.g., BSc, BA, BTech, MBBS, BEd)",
          "stream": "Must be ONE of: 'Biological Science', 'Physical Science', 'Commerce', 'Arts', 'Technology', 'Common'",
          "subject_requirements": ["Subject 1", "Subject 2", "Subject 3"],
          "other_requirements": "O/L passes, Aptitude Tests, or special criteria if any, else null",
          "duration_years": 4,
          "universities": ["University of Colombo", "University of Peradeniya"],
          "description": "Brief course description in Sinhala"
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
        print("📁 No new University Handbook PDFs found in Google Drive.")
        return

    for file in handbook_files:
        file_id = file['id']
        file_name = file['name']
        
        print(f"\n📚 Processing University Handbook: {file_name}...")

        # Download File Bytes
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

            for start_page in range(0, total_pages, BATCH_SIZE):
                end_page = min(start_page + BATCH_SIZE, total_pages)
                print(f"\n🔄 Extracting Clean Text from Pages {start_page + 1} to {end_page}...")

                chunk_text = ""
                for page_num in range(start_page, end_page):
                    page = doc.load_page(page_num)
                    page_txt = page.get_text("text")
                    if page_txt.strip():
                        chunk_text += f"\n--- Page {page_num + 1} ---\n" + page_txt

                if not chunk_text.strip():
                    print(f"ℹ️ Pages {start_page + 1}-{end_page} contain no readable text. Skipping...")
                    continue

                courses = extract_courses_from_clean_text(chunk_text, year="2025/2026")

                if courses:
                    for course in courses:
                        # Defensive checks for Supabase NOT NULL Constraints
                        if not course.get("subject_requirements"):
                            course["subject_requirements"] = []
                        if not course.get("universities"):
                            course["universities"] = []
                        if not course.get("degree"):
                            course["degree"] = "General Degree"
                        if not course.get("stream"):
                            course["stream"] = "Common"

                        supabase.table("university_courses").insert(course).execute()
                    
                    total_extracted += len(courses)
                    print(f"✅ Saved {len(courses)} courses from pages {start_page + 1}-{end_page}.")
                else:
                    print(f"ℹ️ No degree courses found in pages {start_page + 1}-{end_page}.")

                # Rate Limit Safety Delay (විනාඩියක Pause එකක්)
                if end_page < total_pages:
                    print("⏳ Sleeping for 60 seconds to completely reset Gemini Free Tier TPM Quota...")
                    time.sleep(60)

            doc.close()
            move_file(file_id, GDRIVE_FOLDER_ID, processed_folder_id)
            print(f"\n🎉 Successfully processed {file_name}. Total degree courses added: {total_extracted}.")

        except Exception as e:
            print(f"❌ Error processing handbook {file_name}: {e}")
            print("⏳ Keeping file in main Drive folder for retry in next scheduled run.")

if __name__ == "__main__":
    process_handbook_files()
