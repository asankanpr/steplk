import os
import json
import io
import time
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
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

def get_or_create_processed_folder(parent_folder_id):
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
    drive_service.files().update(
        fileId=file_id,
        addParents=target_folder_id,
        removeParents=current_folder_id,
        fields='id, parents'
    ).execute()

def extract_data_with_gemini(file_bytes, mime_type):
    prompt = """
    You are an expert OCR and data extraction assistant for Sri Lankan Gazettes, Job Openings, Courses, and Efficiency Bar (EB) Exams.
    The document may contain ONE or MULTIPLE job advertisements/positions (e.g., Assistant Director, Management Assistant, Technical Officer, EB Exam Notice, etc.).
    Analyze the document carefully in Sinhala, English, or Tamil and extract ALL listed positions into a JSON array of objects.

    Return JSON format like this:
    {
      "posts": [
        {
          "title": "Exact post title in Sinhala/English (e.g., කළමනාකාර සහකාර - Management Assistant)",
          "organization": "Department/Ministry/Institute Name (e.g., දුම්රිය දෙපාර්තමේන්තුව)",
          "category": "One of: 'Government Job', 'කඩඉම් විභාග', 'Course', 'Semi-Govt Job'",
          "meq_level": "One of: 'OL', 'AL', 'NVQ', 'DEGREE', 'POST_GRAD', 'NONE'",
          "closing_date": "Closing Date in YYYY-MM-DD format if present, else null",
          "salary_code": "Salary code if present (e.g., MN-1, SL-1), else null",
          "salary_amount": "Salary scale or amount in LKR (e.g., LKR 38,500 - 62,000), else null",
          "description": "Brief summary/overview of duties or additional notes in Sinhala.",
          "qualifications": "Key requirements listed in clear bullet points in Sinhala."
        }
      ]
    }

    Respond ONLY with valid JSON. Do not add markdown codeblocks.
    """

    max_retries = 3
    delay = 5

    for attempt in range(1, max_retries + 1):
        try:
            print(f"🤖 Requesting Gemini (gemini-3.8-flash - Attempt {attempt}/{max_retries})...")
            response = ai_client.models.generate_content(
                model='gemini-3.8-flash',
                contents=[
                    genai.types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                    prompt
                ]
            )
            clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_text)
            
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "posts" in data:
                return data["posts"]
            elif isinstance(data, dict):
                return [data]
            return []

        except Exception as e:
            print(f"⚠️ Warning (Attempt {attempt} failed): {str(e)}")
            if ("503" in str(e) or "UNAVAILABLE" in str(e) or "high demand" in str(e)) and attempt < max_retries:
                print(f"⏳ Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                raise e

def process_drive_files():
    processed_folder_id = get_or_create_processed_folder(GDRIVE_FOLDER_ID)
    
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

        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        
        file_bytes = fh.getvalue()

        try:
            extracted_posts = extract_data_with_gemini(file_bytes, mime_type)
            
            for idx, post in enumerate(extracted_posts, start=1):
                post_payload = {
                    "title": post.get("title", f"{file_name} - Position {idx}"),
                    "organization": post.get("organization", "N/A"),
                    "category": post.get("category", "Government Job"),
                    "meq_level": post.get("meq_level", "NONE"),
                    "closing_date": post.get("closing_date"),
                    "salary_code": post.get("salary_code"),
                    "salary_amount": post.get("salary_amount"),
                    "description": post.get("description", ""),
                    "qualifications": post.get("qualifications", ""),
                    "status": "PENDING",
                    "source": "GDRIVE_AUTOMATION"
                }

                res = supabase.table("posts").insert(post_payload).execute()
                print(f"✅ Successfully created PENDING post ({idx}/{len(extracted_posts)}): {post_payload['title']}")

            move_file(file_id, GDRIVE_FOLDER_ID, processed_folder_id)
            print(f"📦 Moved {file_name} to Processed folder.")

        except Exception as e:
            print(f"❌ Error processing {file_name}: {str(e)}")

if __name__ == "__main__":
    process_drive_files()
