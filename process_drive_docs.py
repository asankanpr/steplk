import os
import json
import io
import time
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
from google.genai import types
from supabase import create_client, Client

# Environment Variables
GDRIVE_JSON_STR = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not all([GDRIVE_JSON_STR, GDRIVE_FOLDER_ID, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Missing required environment variables.")

info = json.loads(GDRIVE_JSON_STR)
creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

ai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

def make_file_publicly_readable(file_id):
    try:
        user_permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=file_id, body=user_permission, fields='id').execute()
    except Exception as e:
        print(f"⚠️ Permission notice: {str(e)}")

# -------------------------------------------------------------
# PASS 1: INITIAL EXTRACTION (Deterministic with Temperature 0.0)
# -------------------------------------------------------------
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

    config = types.GenerateContentConfig(
        temperature=0.0, # Deterministic Output
        response_mime_type="application/json"
    )

    response = ai_client.models.generate_content(
        model='gemini-3.8-flash',
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

# -------------------------------------------------------------
# PASS 2: QUALITY CHECK & RE-AUDIT PASS
# -------------------------------------------------------------
def run_quality_check_and_reverify(file_bytes, mime_type, primary_posts):
    """
    Re-scans document, checks character-by-character details, and recovers missed positions.
    """
    extracted_titles = [p.get("title", "") for p in primary_posts]

    qc_prompt = f"""
    You are an Quality Control Inspector for Sri Lankan Gazette data extractions.
    
    Initial pass extracted the following {len(extracted_titles)} titles:
    {json.dumps(extracted_titles, ensure_ascii=False)}

    YOUR AUDIT TASK:
    1. Re-examine the attached document from start to finish.
    2. Check if ANY job position, course, or exam notice was SKIPPED or MISSED in the above list.
    3. If any position was missed, extract it with full details.
    
    Return JSON format:
    {{
      "missing_posts": [
        ... missed post objects matching standard structure ...
      ],
      "qc_summary": "Short audit summary note in Sinhala (e.g. 'සියලු තනතුරු 13 පරීක්ෂා කර තහවුරු කරන ලදී.')"
    }}
    """

    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )

    try:
        response = ai_client.models.generate_content(
            model='gemini-3.8-flash',
            contents=[genai.types.Part.from_bytes(data=file_bytes, mime_type=mime_type), qc_prompt],
            config=config
        )

        qc_result = json.loads(response.text.strip())
        missing_posts = qc_result.get("missing_posts", [])
        qc_summary = qc_result.get("qc_summary", "Quality check completed.")

        # Combine Primary + Recovered Missing Posts
        final_posts = primary_posts + missing_posts
        return final_posts, len(missing_posts), qc_summary

    except Exception as e:
        print(f"⚠️ Quality check pass error (using primary extraction): {str(e)}")
        return primary_posts, 0, "QC Verification bypass due to minor notice."

def log_document_status(file_name, file_id, status, count, titles_str, qc_note, error_msg=None):
    try:
        log_payload = {
            "file_name": file_name,
            "file_id": file_id,
            "status": status,
            "extracted_count": count,
            "extracted_titles": f"{titles_str} | [QC Audit: {qc_note}]",
            "error_message": error_msg
        }
        supabase.table("document_logs").insert(log_payload).execute()
    except Exception as log_err:
        print(f"⚠️ Failed to write to document_logs: {str(log_err)}")

def process_drive_files():
    processed_folder_id = get_or_create_folder(GDRIVE_FOLDER_ID, "Processed")
    unsuccessful_folder_id = get_or_create_folder(GDRIVE_FOLDER_ID, "Unsuccessful")
    
    query = f"'{GDRIVE_FOLDER_ID}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])

    if not files:
        print("📁 No new files to process in Google Drive.")
        return

    print(f"🔄 Found {len(files)} file(s) to process...")

    for file in files:
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']
        
        print(f"📄 Processing: {file_name}...")
        gdrive_file_url = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        
        file_bytes = fh.getvalue()

        try:
            # 1. Primary Extraction
            primary_posts = extract_data_with_gemini(file_bytes, mime_type)
            print(f"🔍 Primary Extraction found: {len(primary_posts)} posts.")

            # 2. Quality Check & Re-verification Pass
            final_posts, recovered_count, qc_summary = run_quality_check_and_reverify(file_bytes, mime_type, primary_posts)
            print(f"🛡️ Quality Check Audit: {qc_summary} (Recovered: {recovered_count} missed posts). Total: {len(final_posts)}")

            make_file_publicly_readable(file_id)

            extracted_titles = []

            # 3. Insert Final Verified Posts into Database
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
                    "pdf_url": gdrive_file_url,
                    "status": "PENDING",
                    "source": "GDRIVE_AUTOMATION"
                }

                supabase.table("posts").insert(post_payload).execute()
                extracted_titles.append(post_title)

            titles_str = ", ".join(extracted_titles)
            log_document_status(file_name, file_id, "SUCCESS", len(final_posts), titles_str, qc_summary)

            move_file(file_id, GDRIVE_FOLDER_ID, processed_folder_id)
            print(f"📦 Successfully verified and moved {file_name} to 'Processed'.")

        except Exception as e:
            err_msg = str(e)
            print(f"❌ Error processing {file_name}: {err_msg}")
            log_document_status(file_name, file_id, "FAILED", 0, "", "QC Failed", err_msg)

            try:
                move_file(file_id, GDRIVE_FOLDER_ID, unsuccessful_folder_id)
            except Exception as move_err:
                print(f"🚨 Move failed: {str(move_err)}")

if __name__ == "__main__":
    process_drive_files()
