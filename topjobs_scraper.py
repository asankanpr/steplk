import os
import re
import time
import base64
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from supabase import create_client, Client

# --- 1. SECURITY: Supabase Environment Variables ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("🔒 ආරක්ෂක දෝෂයකි: Supabase credentials හමු නොවිණි.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Browser Headers (Topjobs Block වීම වැළැක්වීමට)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Referer': 'https://www.topjobs.lk/',
}

BASE_URL = "https://www.topjobs.lk"

# --- 2. SMART DATE CONVERTER ---
def convert_topjobs_date(date_str):
    try:
        clean_str = re.sub(r'\s+', ' ', date_str.strip()).replace(',', '')
        date_obj = datetime.strptime(clean_str, "%a %b %d %Y")
        return date_obj.strftime("%Y-%m-%d")
    except Exception:
        return None

# --- 3. SCRAPING LOGIC ---
def get_today_vacancies():
    url = f"{BASE_URL}/applicant/vacancybyfunctionalarea.jsp?jst=OPEN/1000"
    print(f"🔍 Topjobs වෙබ් අඩවිය පරීක්ෂා කරමින් පවතී...")
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
        if res.status_code != 200:
            print(f"⚠️ වෙබ් අඩවිය ලබාගත නොහැකි විය. Status Code: {res.status_code}")
            return []

        soup = BeautifulSoup(res.text, 'html.parser')
        vacancies = []

        rows = soup.find_all('tr')
        for row in rows:
            row_html = str(row)
            
            match = re.search(r"((?:/employer/)?JobAdvertismentServlet\?[^\"'>\s]+)", row_html)
            if not match:
                match_popup = re.search(r"open_popup\(['\"]([^'\"]+)['\"]\)", row_html)
                if match_popup:
                    popup_path = match_popup.group(1)
                    full_url = requests.compat.urljoin(BASE_URL, popup_path)
                else:
                    continue
            else:
                servlet_path = match.group(1).replace("&amp;", "&")
                if not servlet_path.startswith("http"):
                    full_url = requests.compat.urljoin(BASE_URL, servlet_path)
                else:
                    full_url = servlet_path

            cols = row.find_all('td')
            if len(cols) >= 5:
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

        print(f"🎯 හමුවූ රැකියා දැන්වීම් ගණන: {len(vacancies)}")
        return vacancies
        
    except Exception as e:
        print(f"❌ Error in get_today_vacancies: {e}")
        return []

def extract_flyer_image_url(popup_url):
    try:
        res = requests.get(popup_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')

        iframe = soup.find('iframe')
        if iframe and iframe.get('src'):
            nested_url = requests.compat.urljoin(popup_url, iframe['src'])
            return extract_flyer_image_url(nested_url)

        for img in soup.find_all('img'):
            src = img.get('src', '')
            src_lower = src.lower()
            # 🔴 FIX: .gif ෆෝමැට් එක සහ Topjobs අභ්‍යන්තර ෆෝල්ඩර් පරීක්ෂා කිරීම එකතු කර ඇත
            if any(ext in src_lower for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']) or 'advertisment' in src_lower:
                if not any(skip in src_lower for skip in ['logo', 'icon', 'button', 'spacer']):
                    return requests.compat.urljoin(popup_url, src)
    except Exception as e:
        print(f"   ⚠️ Image URL සෙවීමේ දෝෂයක්: {e}")
    return None

# --- 4. INTEGRATION & DB INSERTION ---
def process_and_save_vacancy(job_data):
    topjobs_img_url = extract_flyer_image_url(job_data['popup_url'])
    if not topjobs_img_url:
        print("   ⚠️ මෙම ලින්ක් එකෙහි Image එකක් හමු නොවිණි (Text පමණක් විය හැක).")
        return False

    print(f"   📥 Flyer පින්තූරය හමු විය: {topjobs_img_url}")

    try:
        img_res = requests.get(topjobs_img_url, headers=HEADERS, timeout=20)
        if img_res.status_code != 200:
            print(f"   ⚠️ පින්තූරය Download කරගත නොහැකි විය.")
            return False

        base64_data = base64.b64encode(img_res.content).decode('utf-8')
        mime_type = "image/jpeg"
        if ".png" in topjobs_img_url.lower(): mime_type = "image/png"
        elif ".gif" in topjobs_img_url.lower(): mime_type = "image/gif" # GIF Support

        # 1. ImgBB වෙත Upload කිරීම
        print("   ☁️ ImgBB වෙත Upload කරමින් පවතී...")
        upload_res = supabase.functions.invoke('upload-image', {
            'body': {'base64Image': base64_data}
        })

        upload_data = upload_res.get('data') or {}
        permanent_img_url = topjobs_img_url

        if upload_data and upload_data.get('success'):
            permanent_img_url = upload_data.get('url')
            print(f"   ✅ ImgBB Upload සාර්ථකයි! Link: {permanent_img_url}")

        # 2. Duplicate Checking
        check_dup = supabase.table("posts").select("id").eq("image_url", permanent_img_url).execute()
        if len(check_dup.data) > 0:
            print(f"   🔄 Duplicate: මෙම පෝස්ටුව දැනටමත් ඇත. (Skipped)")
            return False

        # 3. Gemini Vision
        print("   🤖 Gemini AI මගින් දත්ත කියවමින් පවතී...")
        invoke_res = supabase.functions.invoke('scan-flyer', {
            'body': {
                'base64Data': base64_data,
                'mimeType': mime_type
            }
        })

        res_data = invoke_res.get('data', {})
        if not res_data or 'candidates' not in res_data:
            print(f"   ⚠️ AI කියවීම අසාර්ථක විය.")
            return False

        raw_text = res_data['candidates'][0]['content']['parts'][0]['text']
        raw_text = re.sub(r'```json|```', '', raw_text).strip()
        
        import json
        extracted = json.loads(raw_text)

        contact_info = extracted.get('contact', '')
        desc = "පෞද්ගලික අංශයේ රැකියා ඇබෑර්තුවකි."
        if contact_info:
            desc = f"පෞද්ගලික අංශයේ රැකියා ඇබෑර්තුවකි. වැඩිදුර විස්තර සඳහා අමතන්න: {contact_info}"

        final_closing_date = job_data['closing_date'] if job_data['closing_date'] else extracted.get("closing")

        # 4. Insert to Database
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
    print("🚀 Topjobs ස්වයංක්‍රීය Scraper ක්‍රියාවලිය ආරම්භ විය...")
    jobs_list = get_today_vacancies()
    
    successful_inserts = 0
    # 🔴 FIX: පින්තූර නැති රැකියා මඟහැර හරියටම පෝස්ට් 10ක් Save වන තෙක් ලූප් එක දිවීම
    for job in jobs_list:
        if successful_inserts >= 10: 
            break
            
        print(f"\n🔎 රැකියාව පරීක්ෂා කරමින්: {job['popup_url']}")
        is_saved = process_and_save_vacancy(job)
        
        if is_saved:
            successful_inserts += 1
            time.sleep(4) # Save වුණොත් විතරක් තත්පර 4ක් ඉන්නවා
        else:
            time.sleep(1) # නැත්නම් ඉක්මනට ඊළඟ එකට යනවා
            
    print(f"\n✨ ක්‍රියාවලිය අවසන්! සාර්ථකව ඇතුළත් කළ පෝස්ට් ගණන: {successful_inserts}")
