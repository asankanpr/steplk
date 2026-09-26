import os
import json
import io
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
from supabase import create_client, Client

# 1. Environment Variables පරීක්ෂා කිරීම
GDRIVE_JSON_STR = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not all([GDRIVE_JSON_STR, GDRIVE_FOLDER_ID, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Missing required environment variables.")

# 2. Setup Google Drive API
info = json.loads(GDRIVE_JSON_STR)
creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/drive'])
drive_service = build('drive', 'v3', credentials=creds)

# 3. Setup Gemini AI Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# 4. Setup Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_or_create_processed_folder(parent_folder_id):
    """Processed කියන subfolder එක සොයාගැනීම හෝ සෑදීම"""
    query = f"'{parent_folder_id}' in parents and name = 'Processed' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get('files', [])
    
    if folders:
        return folders[0]['id']
    else:
        file_metadata = {
            'name': 'Processed',
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id]
        }
        folder = drive_service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')


def move_file(file_id, current_folder_id, target_folder_id):
    """Scan කළ File එක Processed folder එකට Move කිරීම"""
    drive_service.files().update(
        fileId=file_id,
        addParents=target_folder_id,
        removeParents=current_folder_id,
        fields='id, parents'
    ).execute()


import time

def extract_data_with_gemini(file_bytes, mime_type):
    prompt = """
    You are an expert OCR and data extraction assistant for Sri Lankan Gazettes, Job Openings, and Educational Courses.
    Analyze the attached document/image carefully and extract the following information in strict JSON format:
    
    {
      "title": "Exact post title in Sinhala/English (e.g., කළමනාකාර සහකාර - Management Assistant)",
      "organization": "Department/Ministry/Institute Name (e.g., දුම්රිය දෙපාර්තමේන්තුව)",
      "category": "One of: 'Government Job', 'Gazette', 'Course', 'Semi-Govt Job'",
      "closing_date": "Closing Date in YYYY-MM-DD format if present, else null",
      "description": "Brief summary of duties, qualifications, and how to apply in Sinhala.",
      "qualifications": "Key requirements/qualifications listed in points."
    }

    Respond ONLY with valid JSON. Do not add markdown codeblocks like ```json or any commentary.
    """

    # 2026 වසරේ සක්‍රීය gemini-3.8-flash Model එක භාවිතය
    response = ai_client.models.generate_content(
        model='gemini-3.8-flash',
        contents=[
            genai.types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            prompt
        ]
    )

    clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(clean_text)


def process_drive_files():
    processed_folder_id = get_or_create_processed_folder(GDRIVE_FOLDER_ID)
    
    # Upload Folder එකේ ඇති Files ඩවුන්ලෝඩ් කරගැනීම
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
        
        print(f"📄 Processing: {file_name} ({mime_type})...")

        # File එක Memory එකට Download කිරීම
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        
        file_bytes = fh.getvalue()

        try:
            # 1. Gemini AI OCR & Parsing
            extracted_data = extract_data_with_gemini(file_bytes, mime_type)
            
            # 2. Supabase DB එකට Pending Status එකෙන් එකතු කිරීම
            post_payload = {
                "title": extracted_data.get("title", file_name),
                "organization": extracted_data.get("organization", "N/A"),
                "category": extracted_data.get("category", "Government Job"),
                "closing_date": extracted_data.get("closing_date"),
                "description": extracted_data.get("description", ""),
                "qualifications": extracted_data.get("qualifications", ""),
                "status": "PENDING",  # Admin Approve කරන තෙක් Pending පවතී
                "source": "GDRIVE_AUTOMATION"
            }

            res = supabase.table("posts").insert(post_payload).execute()
            print(f"✅ Successfully created PENDING post: {extracted_data.get('title')}")

            # 3. Processed Folder එකට File එක Move කිරීම
            move_file(file_id, GDRIVE_FOLDER_ID, processed_folder_id)
            print(f"📦 Moved {file_name} to Processed folder.")

        except Exception as e:
            print(f"❌ Error processing {file_name}: {str(e)}")


if __name__ == "__main__":
    process_drive_files()
