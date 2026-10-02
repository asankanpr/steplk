import os
import json
import fitz  # PyMuPDF
from google import genai
from google.genai import types
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Verified 5-Tier Fallback Chain
DEFAULT_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

def extract_handbook_courses(pdf_bytes, year="2025/2026"):
    prompt = f"""
    You are an expert UGC Sri Lanka University Admission Handbook Extractor.
    Extract ALL Degree Programmes from this document into a structured JSON list for admission year {year}.

    Return JSON array:
    {{
      "courses": [
        {{
          "admission_year": "{year}",
          "course_name_si": "පාඨමාලාවේ නම සිංහලෙන්",
          "course_name_en": "Official Course Name in English",
          "degree": "BSc / BA / BTech / MBBS / BEd etc.",
          "stream": "One of: 'Biological Science', 'Physical Science', 'Commerce', 'Arts', 'Technology', 'Common'",
          "subject_requirements": ["Chemistry", "Biology", "Physics"], 
          "other_requirements": "O/L English Pass, Aptitude Test, etc.",
          "duration_years": 4,
          "universities": ["University of Colombo", "University of Peradeniya"],
          "description": "Short summary of course in Sinhala"
        }}
      ]
    }}
    """
    # PDF Processing and Gemini API Call Execution Logic...
