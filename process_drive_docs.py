import os
import json
import io
import time
import warnings
import uuid
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
from google.genai import types
from supabase import create_client, Client

warnings.filterwarnings("ignore")

# 1. Environment Variables Validation
GDRIVE_JSON_STR = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_DOC_EXTRACTOR_KEY") or os.getenv("GEMINI_DRIVE_API_KEY") or os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not all([GDRIVE_JSON_STR, GDRIVE_FOLDER_ID, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("❌ CRITICAL: Missing required environment variables.")
    exit(1)

# Initialize Clients
info = json.loads(GDRIVE_JSON_STR)
creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

ai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ENV_PRIMARY = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
ENV_FALLBACK = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash").strip()

DEFAULT_CHAIN = [
    ENV_PRIMARY,
    ENV_FALLBACK,
    "gemini-3.5-flash",       
    "gemini-3.5-flash-lite",  
    "gemini-3.1-flash-lite"   
]

MODEL_NAMES = []
for m in DEFAULT_CHAIN:
    m_clean = m.replace("models/", "").strip() if m else ""
    if m_clean and m_clean not in MODEL_NAMES:
        MODEL_NAMES.append(m_clean)

def generate_content_with_retry(contents, config):
    retryable_errors = ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand", "quota", "overloaded"]
    model_not_found_errors = ["404", "NOT_FOUND", "not found", "not supported"]
    
    last_error = None

    for model_index, model_name in enumerate(MODEL_NAMES):
        delay = 5
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
                print(f"⚠️ Gemini error with {model_name} (attempt {attempt}/3): {err_str[:120]}...")

                if any(error in err_str.lower() for error in model_not_found_errors):
                    break
                
                if not any(error in err_str.lower() for error in retryable_errors):
                    raise e

                if attempt < 3:
                    time.sleep(delay)
                    delay *= 2
                elif model_index < len(MODEL_NAMES) - 1:
                    print(f"🔁 Switching to Fallback: {MODEL_NAMES[model_index + 1]}...")

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

# 🔴 Security Update: Removed `make_file_publicly_readable` function completely.
# Drive files should remain strictly private.

def extract_data_with_gemini(file_bytes, mime_type):
    prompt = """
    You are a meticulous Sri Lankan Gazette & Job Advertisement OCR Specialist.
    Read the entire document page by page. Do NOT omit or merge any job positions, exams, or courses.
    
    For EVERY position found, extract accurate fields into a JSON array:
    {
      "posts": [
        {
          "title": "Exact post title in Sinhala/English",
          "organization": "Department/Ministry/Institute Name",
          "category": "One of: 'Government Job', 'කඩඉම් විභාග', 'Course', 'Semi-Govt Job'",
          "meq_level": "One of: 'OL', 'AL', 'NVQ', 'DEGREE', 'POST_GRAD', 'NONE'",
          "closing_date": "Closing Date in YYYY-MM-DD format if present, else null",
          "salary_code": "Salary code if present (e.g., MN-1, SL-1), else null",
          "salary_amount": "Salary scale or amount in LKR, else null",
          "description": "Brief summary of duties/notes in Sinhala.",
          "qualifications": "Key educational & experience requirements in clear bullet points in Sinhala."
        }
      ]
    }
    Respond strictly with valid JSON.
    """
    config = types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json")
    response = generate_content_with_retry(
        contents=[genai.types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
        config=config
    )
    clean_text = response.text.strip()
    data = json.loads(clean_text)

    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "posts" in data:
        return data["posts"]
    return []

def run_quality_check_and_reverify(file_bytes, mime_type, primary_posts):
    extracted_titles = [p.get("title", "") for p in primary_posts]
    qc_prompt = f"""
    You are a Quality Control Inspector. Initial pass extracted {len(extracted_titles)} titles:
    {json.dumps(extracted_titles, ensure_ascii=False)}

    YOUR AUDIT TASK:
    1. Re-examine the document.
    2. Check if ANY position was MISSED.
    3. If missed, extract it.
    
    Return JSON format:
    {{
      "missing_posts": [ ... ],
      "qc_summary": "Short audit summary note"
    }}
    """
    config = types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json")
    try:
        response = generate_content_with_retry(
            contents=[genai.types.Part.from_bytes(data=file_bytes, mime_type=mime_type), qc_prompt],
            config=config
        )
        qc_result = json.loads(response.text.strip())
        missing_posts = qc_result.get("missing_posts", [])
        return primary_posts + missing_posts, len(missing_posts), qc_result.get("qc_summary", "QC done.")
    except Exception as e:
        return primary_posts, 0, f"QC bypassed: {str(e)[:50]}"

def log_document_status(file_name, file_id, status, count, titles_str, qc_note, error_msg=None):
    try:
        log_payload = {
            "file_name": file_name,
            "file_id": file_id,
            "status": status,
            "extracted_count": count,
            "extracted_titles": f"{titles_str} | [QC: {qc_note}]",
            "error_message": error_msg
        }
        supabase.table("document_logs").insert(log_payload).execute()
    except Exception as log_err:
        print(f"⚠️ Log Write Failed: {str(log_err)}")

def process_drive_files():
    processed_folder_id = get_or_create_folder(GDRIVE_FOLDER_ID, "Processed")
    query = f"'{GDRIVE_FOLDER_ID}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])

    if not files:
        print("📁 No new files to process.")
        return

    for file in files:
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']
        
        print(f"📄 Processing: {file_name}...")
        
        # 🔴 Security Update: Remove Direct Drive Link sharing. Replace with a secure internal reference.
        secure_reference_id = f"DRIVE_REF_{file_id}"

        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        
        file_bytes = fh.getvalue()

        try:
            primary_posts = extract_data_with_gemini(file_bytes, mime_type)
            final_posts, recovered_count, qc_summary = run_quality_check_and_reverify(file_bytes, mime_type, primary_posts)
            extracted_titles = []

            for idx, post in enumerate(final_posts, start=1):
                post_title = post.get("title", f"{file_name} - Position {idx}")
                
                post_payload = {
                    "title": post_title,
                    "organization": post.get("organization", "N/A"),
                    "category": post.get("category", "Government Job"),
                    "meq_level": post.get("meq_level", "NONE"),
                    "closing_date": post.get("closing_date"),
                    "salary_code": post.get("salary_code"),
                    "salary_amount": post.get("salary_amount"),
                    "description": post.get("description", ""),
                    "qualifications": post.get("qualifications", ""),
                    "pdf_url": secure_reference_id, # Internal Ref instead of public link
                    "status": "PENDING",
                    "source": "GDRIVE_AUTOMATION"
                }

                supabase.table("posts").insert(post_payload).execute()
                extracted_titles.append(post_title)

            titles_str = ", ".join(extracted_titles)
            log_document_status(file_name, file_id, "SUCCESS", len(final_posts), titles_str, qc_summary)
            move_file(file_id, GDRIVE_FOLDER_ID, processed_folder_id)
            print(f"📦 Successfully verified and moved {file_name}.")

        except Exception as e:
            print(f"❌ Error processing {file_name}: {e}")
            log_document_status(file_name, file_id, "FAILED", 0, "", "Error", str(e))

if __name__ == "__main__":
    process_drive_files()
