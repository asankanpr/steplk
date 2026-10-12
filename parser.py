import json
import os
import io
from enum import Enum
import fitz  # PyMuPDF
from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field

class ItemType(str, Enum):
    JOB = "JOB"
    COURSE = "COURSE"

class MEQLevel(str, Enum):
    NONE = "NONE"
    OL = "OL"
    AL = "AL"
    NVQ = "NVQ"
    DEGREE = "DEGREE"
    POST_GRAD = "POST_GRAD"

class StepLKNotice(BaseModel):
    title: str = Field(description="තැන්න හෝ පාඨමාලාවේ නම")
    organization: str = Field(description="ආයතනය, දෙපාර්තමේන්තුව හෝ අමාත්‍යාංශය")
    type: ItemType = Field(description="JOB හෝ COURSE")
    meq_level: MEQLevel = Field(description="අවම අධ්‍යාපනික සුදුසුකම් මට්ටම")
    educational_qualifications: str = Field(description="අධ්‍යාපනික සුදුසුකම්")
    experience_qualifications: str = Field(description="වෘත්තීය පළපුරුද්ද")
    salary_or_fee: str = Field(description="වැටුප් ක්‍රමය හෝ පාඨමාලා ගාස්තුව")
    age_limit: str = Field(description="වයස් සීමාව")
    closing_date: str = Field(description="අයදුම්පත් භාරගන්නා අවසන් දිනය (YYYY-MM-DD)")

# 🔴 Security Update: In-memory image processing (No temp files on disk)
def pdf_page_to_image_buffer(pdf_path: str, page_number: int = 0) -> io.BytesIO:
    doc = fitz.open(pdf_path)
    if page_number >= len(doc):
        raise ValueError(f"Invalid page number {page_number}")
    page = doc[page_number]
    pix = page.get_pixmap(dpi=200)
    
    img_buffer = io.BytesIO()
    img_buffer.write(pix.tobytes("png"))
    img_buffer.seek(0)
    return img_buffer

def parse_gazette_notice(image_source, api_key: str = None) -> StepLKNotice:
    client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))
    image = Image.open(image_source)

    prompt = """
    ඔබ ශ්‍රී ලංකා රාජ්‍ය ගැසට් පත්‍ර සහ පුවත්පත් දැන්වීම් කියවන AI විශ්ලේෂකයෙකි.
    ලබාදී ඇති රූපයේ ඇති සිංහල තොරතුරු නිවැරදිව කියවා පහත සඳහන් නීතිවලට අනුව JSON සකස් කරන්න:
    1. 'educational_qualifications' සහ 'experience_qualifications' පැහැදිලිව වෙන් කර දක්වන්න.
    2. Minimum Educational Qualification (meq_level) එක තීරණය කරන්න (OL/AL/NVQ/DEGREE/POST_GRAD/NONE).
    3. closing_date දිනය YYYY-MM-DD format එකට සකසන්න.
    """

    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=[image, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=StepLKNotice,
            temperature=0.1,
        ),
    )
    return StepLKNotice(**json.loads(response.text))

if __name__ == "__main__":
    pdf_file = "sample_gazette.pdf"
    try:
        print("1. Converting PDF directly in memory...")
        if os.path.exists(pdf_file):
            img_buffer = pdf_page_to_image_buffer(pdf_file, page_number=0)
            print("2. Extracting data via AI...")
            result = parse_gazette_notice(img_buffer)
            print("\n✅ Extraction Successful!\n", json.dumps(result.model_dump(), indent=4, ensure_ascii=False))
        else:
            print("❌ File not found.")
    except Exception as e:
        print(f"\n❌ Error occurred: {e}")
