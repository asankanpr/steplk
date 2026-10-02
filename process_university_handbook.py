import os
import json
import io
import time
import warnings
import fitz  # PyMuPDF for PDF page chunking
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

# Verified 5-Tier Fallback Model Chain
DEFAULT_CHAIN = [
    "gemini-3.8-flash",       # Tier 1: Primary Model (High Accuracy)
    "gemini-3.6-flash",       # Tier 2: Fast & Reliable Backup
    "gemini-3.5-flash",       # Tier 3: Workhorse Backup
    "gemini-3.5-flash-lite",  # Tier 4: Google Recommended Lite Model
    "gemini-3.1-flash-lite"   # Tier 5: High Rate Limit Buffer
]

BATCH_SIZE = 10  # Free limit නොඉක්මවීමට පිටු 10 බැගින් process කිරීම

def generate_content_with_retry(contents, config):
    retryable_errors = ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand", "quota", "overloaded"]
    model_not_found_errors = ["404", "NOT_FOUND", "not found"]
    
    last_error = None

    for model_index, model_name in enumerate(DEFAULT_CHAIN):
        delay = 4
        for attempt in range(1, 4):
            try:
                print(f"🤖 Requesting Gemini ({model_name}, attempt {attempt}/3)...")
                return ai_client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
            except Exception as e:
                last_error = e
                err_str = str(e)
                print(f"⚠️ Gemini error with {model_name}: {err_str[:120]}...")

                if any(error in err_str.lower() for error in model_not_found_errors):
                    break
                
                if not any(error in err_str.lower() for error in retryable_errors):
                    raise e

                if attempt < 3:
                    time.sleep(delay)
                    delay *= 2

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

def extract_courses_from_pdf_chunk(chunk_bytes, year="2025/2026"):
    prompt = f"""
    You are an expert UGC Sri Lanka University Admission Handbook Extractor.
    Extract ALL University Degree Programmes found in the attached document chunk for admission year {year}.

    Return JSON array:
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
    Respond strictly with valid JSON.
    """

    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )

    response = generate_content_with_retry(
        contents=[genai.types.Part.from_bytes(data=chunk_bytes, mime_type="application/pdf"), prompt],
        config=config
    )

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

    # Handbook files පමණක් තෝරාගැනීම
    handbook_files = [f for f in files if "handbook" in f['name'].lower() or "ugc" in f['name'].lower() or "university" in f['name'].lower()]

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
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pages = len(doc)
            print(f"📖 Total Pages: {total_pages}. Processing in chunks of {BATCH_SIZE} pages...")

            total_extracted = 0

            for start_page in range(0, total_pages, BATCH_SIZE):
                end_page = min(start_page + BATCH_SIZE, total_pages)
                print(f"🔄 Chunking Pages {start_page + 1} to {end_page}...")

                chunk_doc = fitz.open()
                chunk_doc.insert_pdf(doc, from_page=start_page, to_page=end_page - 1)
                chunk_bytes = chunk_doc.write()
                chunk_doc.close()

                courses = extract_courses_from_pdf_chunk(chunk_bytes, year="2025/2026")

                if courses:
                    for course in courses:
                        supabase.table("university_courses").insert(course).execute()
                    total_extracted += len(courses)
                    print(f"✅ Saved {len(courses)} courses from pages {start_page + 1}-{end_page}.")
                
                time.sleep(4)  # Rate limit protection delay

            doc.close()
            move_file(file_id, GDRIVE_FOLDER_ID, processed_folder_id)
            print(f"🎉 Successfully processed {file_name}. Total courses added: {total_extracted}.")

        except Exception as e:
            print(f"❌ Error processing handbook {file_name}: {e}")
            print("⏳ Keeping file in main Drive folder for retry in next scheduled run.")

if __name__ == "__main__":
    process_handbook_files()
