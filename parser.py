import json
import os
from enum import Enum
import fitz  # PyMuPDF
from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field

# ==========================================
# 1. StepLK Data Schema (Pydantic Model)
# ==========================================


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
    title: str = Field(
        description="තැන්න හෝ පාඨමාලාවේ නම (Sinhala title, e.g., 'මහජන සෞඛ්‍ය පරීක්ෂක')"
    )
    organization: str = Field(
        description="ආයතනය, දෙපාර්තමේන්තුව හෝ අමාත්‍යාංශය"
    )
    type: ItemType = Field(description="JOB හෝ COURSE")
    meq_level: MEQLevel = Field(
        description="අවම අධ්‍යාපනික සුදුසුකම් මට්ටම (NONE, OL, AL, NVQ, DEGREE, POST_GRAD)"
    )
    educational_qualifications: str = Field(
        description="අධ්‍යාපනික සුදුසුකම් කොටස වෙන්කර දක්වන්න"
    )
    experience_qualifications: str = Field(
        description="වෘත්තීය පළපුරුද්ද කොටස (අවශ්‍ය නැත්නම් 'අවශ්‍ය නොවේ' ලෙස සටහන් කරන්න)"
    )
    salary_or_fee: str = Field(
        description="වැටුප් ක්‍රමය හෝ පාඨමාලා ගාස්තුව (සඳහන් නැත්නම් 'සඳහන් කර නැත')"
    )
    age_limit: str = Field(
        description="වයස් සීමාව (සඳහන් නැත්නම් 'සඳහන් කර නැත')"
    )
    closing_date: str = Field(
        description="අයදුම්පත් භාරගන්නා අවසන් දිනය (YYYY-MM-DD ආකෘතියෙන්)"
    )


# ==========================================
# 2. PDF to Image Converter Function
# ==========================================


def pdf_page_to_image(
    pdf_path: str, page_number: int = 0, output_image_path: str = "temp_page.png"
):
    """PDF එකෙහි අදාළ පිටුව PNG ඡායාරූපයක් බවට පත් කරයි."""
    doc = fitz.open(pdf_path)
    if page_number >= len(doc):
        raise ValueError(
            f"PDF එකෙහි පිටු සංඛ්‍යාව {len(doc)} කි. පිටු අංක {page_number} සොයාගත නොහැක."
        )

    page = doc[page_number]
    pix = page.get_pixmap(dpi=200)  # High resolution for Sinhala text OCR
    pix.save(output_image_path)
    return output_image_path


# ==========================================
# 3. Vision Extraction Engine
# ==========================================


def parse_gazette_notice(
    image_path: str, api_key: str = None
) -> StepLKNotice:
    """Gemini Vision AI භාවිතයෙන් රූපයෙන් සිංහල දත්ත කියවා Structured JSON සකසයි."""
    client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))

    # Load image
    image = Image.open(image_path)

    prompt = """
    ඔබ ශ්‍රී ලංකා රාජ්‍ය ගැසට් පත්‍ර සහ පුවත්පත් දැන්වීම් කියවන AI විශ්ලේෂකයෙකි.
    ලබාදී ඇති රූපයේ ඇති සිංහල තොරතුරු නිවැරදිව කියවා පහත සඳහන් නීතිවලට අනුව JSON සකස් කරන්න:
    
    1. 'educational_qualifications' සහ 'experience_qualifications' පැහැදිලිව වෙන් කර දක්වන්න.
    2. Minimum Educational Qualification (meq_level) එක තීරණය කරන්න:
       - සාමාන්‍ය පෙළ නම් -> 'OL'
       - උසස් පෙළ නම් -> 'AL'
       - ඩිප්ලෝමා / NVQ නම් -> 'NVQ'
       - උපාධිය නම් -> 'DEGREE'
       - පශ්චාත් උපාධි නම් -> 'POST_GRAD'
       - සුදුසුකම් වැදගත් නැත්නම් -> 'NONE'
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

    # Parse JSON into Pydantic Model
    extracted_data = json.loads(response.text)
    return StepLKNotice(**extracted_data)


# ==========================================
# 4. Execution Example
# ==========================================
if __name__ == "__main__":
    # 1. Set GEMINI_API_KEY environment variable
    # os.environ["GEMINI_API_KEY"] = "YOUR_API_KEY_HERE"

    pdf_file = "sample_gazette.pdf"
    image_file = "gazette_page.png"

    try:
        print("1. PDF එක Image එකක් බවට හරවමින් පවතී...")
        if os.path.exists(pdf_file):
            image_path = pdf_page_to_image(pdf_file, page_number=0)
        else:
            image_path = image_file  # Direct Image input

        print("2. AI Vision මගින් සිංහල දත්ත Extract කරමින් පවතී...")
        result = parse_gazette_notice(image_path)

        print("\n✅ Extraction Successful! Output JSON:\n")
        print(json.dumps(result.model_dump(), indent=4, ensure_ascii=False))

    except Exception as e:
        print(f"\n❌ Error occurred: {e}")