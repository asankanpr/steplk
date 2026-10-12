import os
import re
import time
import base64
import requests
import io  # 🔴 අලුතින් එක් කරන ලදී
from datetime import datetime
from bs4 import BeautifulSoup
from supabase import create_client, Client
from PIL import Image # 🔴 පින්තූර කැපීම සඳහා (Pillow) අලුතින් එක් කරන ලදී

# --- 1. SECURITY & CREDENTIALS ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("🔒 ආරක්ෂක දෝෂයකි: Supabase credentials හමු නොවිණි.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Referer': 'https://www.topjobs.lk/',
}
BASE_URL = "https://www.topjobs.lk"

# 🔴 LEVEL 1: Fast Blacklist Keywords for Agencies
AGENCY_KEYWORDS = [
    'recruitment', 'manpower', 'consultanc', 'consultant', 'placement', 
    'headhunter', 'hr solution', 'outsourcing', 'human resource', 'executive search'
]

def convert_topjobs_date(date_str):
    try:
        clean_str = re.sub(r'\s+', ' ', date_str.strip()).replace(',', '')
        date_obj = datetime.strptime(clean_str, "%a %b %d %Y")
        return date_obj.strftime("%Y-%m-%d")
    except Exception:
        return None

def get_today_vacancies():
    url = f"{BASE_URL}/applicant/vacancybyfunctionalarea.jsp?jst=OPEN/1000"
    print(f"🔍 Topjobs වෙබ් අඩවිය පරීක්ෂා කරමින් පවතී...")
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
        if res.status_code != 200:
            return []

        soup = BeautifulSoup(res.text, 'html.parser')
        vacancies = []

        rows = soup.find_all('tr')
        for row in rows:
            row_html = str(row)
            match_popup = re.search(r"(?:openSizeWindow|open_popup)\(['\"]([^'\"]+)['\"]", row_html)
            
            if match_popup:
                popup_path = match_popup.group(1).replace('%27', '')
                full_url = requests.compat.urljoin(BASE_URL, popup_path)
            else:
                continue

            cols = row.find_all('td')
            if len(cols) >= 5:
                title_employer_text = cols[1].text.strip()
                title_employer_lower = title_employer_text.lower()
                
                if any(kw in title_employer_lower for kw in AGENCY_KEYWORDS):
                    continue

                closing_raw = ""
                for td in cols:
                    td_txt = td.text.strip()
                    if any(month in td_txt for month in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]):
                        closing_raw = td_txt

                closing_formatted = convert_topjobs_date(closing_raw)
                vacancies.append({
                    'closing_date': closing_formatted,
                    'popup_url': full_url
                })

        print(f"🎯 තෝරාගත් (Genuine) රැකියා දැන්වීම් ගණන: {len(vacancies)}")
        return vacancies
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return []

def extract_flyer_image_url(popup_url, depth=0):
    if depth > 3: return None
        
    try:
        res = requests.get(popup_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')

        for frame in soup.find_all(['iframe', 'frame']):
            src = frame.get('src', '')
            if src:
                nested_url = requests.compat.urljoin(popup_url, src)
                img_url = extract_flyer_image_url(nested_url, depth + 1)
                if img_url: return img_url

        for a_tag in soup.find_all('a'):
            href = a_tag.get('href', '')
            if '.pdf' in href.lower():
                return requests.compat.urljoin(popup_url, href)

        possible_flyers = []
        for img in soup.find_all('img'):
            src = img.get('src', '')
            src_lower = src.lower()
            
            if '/images/' in src_lower or 'spacer' in src_lower or 'btn' in src_lower or 'icon' in src_lower:
                continue
                
            if '_small' in src_lower or 'thumb' in src_lower:
                continue
                
            if any(ext in src_lower for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']):
                full_url = requests.compat.urljoin(popup_url, src)
                if '/logo/' in src_lower:
                    return full_url  
                possible_flyers.append(full_url)
                
        if possible_flyers:
            return possible_flyers[-1]
                
    except Exception:
        pass
    return None

def process_and_save_vacancy(job_data):
    topjobs_img_url = extract_flyer_image_url(job_data['popup_url'])
    
    if not topjobs_img_url:
        print("   ⚠️ මෙම ලින්ක් එකෙහි Image හෝ PDF එකක් හමු නොවිණි.")
        return False

    if topjobs_img_url.lower().endswith('.pdf'):
        print(f"   ⚠️ PDF Flyer එකක් හමුවිය. එය මඟහරින ලදී: {topjobs_img_url}")
        return False

    print(f"   📥 සැබෑ Flyer පින්තූරය හමු විය: {topjobs_img_url}")

    try:
        check_dup = supabase.table("posts").select("id").eq("image_url", topjobs_img_url).execute()
        if len(check_dup.data) > 0:
            print(f"   🔄 Duplicate: මෙම පෝස්ටුව දැනටමත් ඇත. (Skipped)")
            return False

        img_res = requests.get(topjobs_img_url, headers=HEADERS, timeout=20)
        if img_res.status_code != 200:
            return False

        image_bytes = img_res.content

        # -------------------------------------------------------------
        # ✂️ 100% SMART WATERMARK REMOVAL (Topjobs සලකුණ කැපීම) ✂️
        # -------------------------------------------------------------
        try:
            print("   ✂️ පින්තූරයේ ඇති Topjobs Watermark එක ඉවත් කරමින් පවතී...")
            image = Image.open(io.BytesIO(image_bytes))
            
            # GIF, PNG වැනි දේවල් RGB වලට හැරවීම (Crop කිරීම පහසු වීමට)
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            width, height = image.size
            
            # සාමාන්‍යයෙන් Topjobs සලකුණ ඇත්තේ යටම කෙළවරේය (පහළින් පික්සෙල් 45ක් පමණ).
            crop_amount = 45 
            
            # පින්තූරය ඉතා කුඩා නම් (height 200 ට අඩු නම්) crop නොකරමු
            if height > 200:
                # Box: (left, upper, right, lower)
                image = image.crop((0, 0, width, height - crop_amount))
            
            # කැපූ පින්තූරය නැවත bytes බවට හැරවීම
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='JPEG', quality=95)
            image_bytes = img_byte_arr.getvalue()
            
            print("   ✅ Watermark එක සාර්ථකව කපා ඉවත් කරන ලදී!")
        except Exception as e:
            print(f"   ⚠️ පින්තූරය Crop කිරීමේ දෝෂයක්: {e}")

        # Crop කරපු එක Base64 වලට හරවනවා
        base64_data = base64.b64encode(image_bytes).decode('utf-8')
        mime_type = "image/jpeg"

        # -------------------------------------------------------------
        # 🤖 1. Gemini AI මගින් දත්ත කියවීම 
        # -------------------------------------------------------------
        print("   🤖 Gemini AI මගින් දත්ත කියවමින් පවතී...")
        scan_url = f"{SUPABASE_URL}/functions/v1/scan-flyer"
        
        scan_res = None
        for attempt in range(2): 
            try:
                scan_res = requests.post(
                    scan_url,
                    headers={"Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
                    json={'base64Data': base64_data, 'mimeType': mime_type},
                    timeout=90  
                )
                if scan_res.status_code == 200:
                    break
            except requests.exceptions.Timeout:
                print(f"   ⏳ AI Timeout (Attempt {attempt+1}/2). Retrying...")
            except Exception as e:
                print(f"   ⚠️ AI Request Error: {e}")
                
        if not scan_res or scan_res.status_code != 200:
            print(f"   ⚠️ AI කියවීම අසාර්ථක විය.")
            return False

        res_data = scan_res.json()
        if not res_data or 'candidates' not in res_data:
            return False

        raw_text = res_data['candidates'][0]['content']['parts'][0]['text']
        raw_text = re.sub(r'```json|```', '', raw_text).strip()
        
        import json
        extracted = json.loads(raw_text)

        if extracted.get("is_agency") == True:
            print(f"   🚫 [LEVEL 2 REJECT] AI විසින් මෙය Manpower/Agency එකක් බව හඳුනාගත්තා.")
            return False

        # -------------------------------------------------------------
        # ☁️ 2. Genuine නම් පමණක් ImgBB වෙත Upload කිරීම
        # -------------------------------------------------------------
        print("   ☁️ Genuine පෝස්ටුවකි. ImgBB වෙත Upload කරමින් පවතී...")
        upload_url = f"{SUPABASE_URL}/functions/v1/upload-image"
        upload_res = requests.post(
            upload_url,
            headers={"Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
            json={'base64Image': base64_data},
            timeout=90
        )
        
        permanent_img_url = topjobs_img_url
        if upload_res.status_code == 200:
            upload_data = upload_res.json()
            if upload_data.get('success'):
                permanent_img_url = upload_data.get('url')
                print(f"   ✅ ImgBB Upload සාර්ථකයි! Link: {permanent_img_url}")

        contact_info = extracted.get('contact', '')
        desc = "පෞද්ගලික අංශයේ රැකියා ඇබෑර්තුවකි."
        if contact_info:
            desc = f"පෞද්ගලික අංශයේ රැකියා ඇබෑර්තුවකි. වැඩිදුර විස්තර සඳහා අමතන්න: {contact_info}"

        final_closing_date = job_data['closing_date'] if job_data['closing_date'] else extracted.get("closing")

        # 3. Insert to Database
        payload = {
            "title": extracted.get("title") or "Private Job Vacancy",
            "organization": extracted.get("org") or "Private Company",
            "category": "Private Job",
            "meq_level": extracted.get("meq_level", "NONE"),
            "salary_amount": extracted.get("salary") or "සඳහන් නැත",
            "closing_date": final_closing_date,
            "qualifications": extracted.get("qualifications") or "දැන්වීම පරීක්ෂා කරන්න",
            "description": desc,
            "image_url": permanent_img_url, 
            "status": "PENDING", 
            "source": "TOPJOBS_AUTO"
        }

        supabase.table("posts").insert(payload).execute()
        print(f"   🎉 සාර්ථකව Database එකට එක් විය: {payload['title']}")
        return True

    except Exception as err:
        print(f"   ❌ Process කිරීමේ දෝෂයක්: {err}")
        return False

if __name__ == "__main__":
    print("🚀 Topjobs ස්වයංක්‍රීය (Anti-Spam & Watermark Free) Scraper ආරම්භ විය...")
    jobs_list = get_today_vacancies()
    
    successful_inserts = 0
    for job in jobs_list:
        if successful_inserts >= 10: 
            break
            
        print(f"\n🔎 රැකියාව පරීක්ෂා කරමින්: {job['popup_url']}")
        is_saved = process_and_save_vacancy(job)
        
        if is_saved:
            successful_inserts += 1
            time.sleep(4) 
        else:
            time.sleep(1) 
            
    print(f"\n✨ ක්‍රියාවලිය අවසන්! සාර්ථකව ඇතුළත් කළ Genuine පෝස්ට් ගණන: {successful_inserts}")
