import os
import json
import time
import fitz  # PyMuPDF
from google import genai
from google.genai import types
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

DEFAULT_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

BATCH_SIZE = 10  # පිටු 10 බැගින් Chunk කිරීම

def process_handbook_in_chunks(pdf_bytes, year="2025/2026"):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    print(f"📚 Handbook Total Pages: {total_pages}. Processing in batches of {BATCH_SIZE} pages...")

    for start_page in range(0, total_pages, BATCH_SIZE):
        end_page = min(start_page + BATCH_SIZE, total_pages)
        print(f"\n🔄 Processing Pages {start_page + 1} to {end_page} of {total_pages}...")

        # අදාළ පිටු 10 විතරක් අලුත් PDF Chunk එකක් ලෙස සාදාගැනීම
        chunk_doc = fitz.open()
        chunk_doc.insert_pdf(doc, from_page=start_page, to_page=end_page - 1)
        chunk_bytes = chunk_doc.write()
        chunk_doc.close()

        # Gemini Vision / Extraction Prompt එකට යැවීම
        try:
            courses = extract_courses_from_chunk(chunk_bytes, year)
            if courses:
                print(f"✅ Found {len(courses)} courses in pages {start_page + 1}-{end_page}.")
                # Supabase table එකට Insert කිරීම
                for c in courses:
                    supabase.table("university_courses").insert(c).execute()
            else:
                print(f"ℹ️ No degree courses found in pages {start_page + 1}-{end_page}.")

        except Exception as e:
            print(f"⚠️️ Error processing pages {start_page + 1}-{end_page}: {e}")

        # Free Tier Rate Limit ආරක්ෂා කරගැනීමට තත්පර 5ක Delay එකක්
        print("⏳ Waiting 5 seconds before next batch...")
        time.sleep(5)

    print("\n🎉 Whole Handbook Processing Finished Successfully!")
