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

# Browser Headers (Topjobs අවහිර නොකිරීම සඳහා)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Referer': 'https://www.topjobs.lk/',
}

BASE_URL = "https://www.topjobs.lk"

# --- 2. SMART DATE CONVERTER ---
def convert_topjobs_date(date_str):
    """'Sat Oct 17 2026' ආකෘතියේ දිනයන් '2026-10-17' (YYYY-MM-DD) බවට හැරවීම"""
    try:
        clean_str = re.sub(r'\s+', ' ', date_str.strip()).replace(',', '')
        date_obj = datetime.strptime(clean_str, "%a %b %d %Y")
        return date_obj.strftime("%Y-%m-%d")
    except Exception:
        return None

# --- 3. SCRAPING LOGIC ---
def get_today_vacancies():
    # 🌟 මෙන්න රහස: ?jst=OPEN/1000 දැමූ විට සියලුම රැකියා ලැයිස්තුව ලෝඩ් වේ
    url = f"{BASE_URL}/applicant/vacancybyfunctionalarea.jsp?jst=OPEN/1000"
    print(f"🔍 Topjobs වෙබ් අඩවිය පරීක්ෂා කරමින් පවතී: {url}")
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
        if res.status_code != 200:
            print(f"⚠️ වෙබ් අඩවිය ලබාගත නොහැකි විය. Status Code: {res.status_code}")
            return []

        soup = BeautifulSoup(res.text, 'html.parser')
        vacancies = []

        # Table පේළි (Rows) පරීක්ෂා කිරීම
        rows = soup.find_all('tr')
        for row in rows:
            row_html = str(row)
            
            # Topjobs හි Popup URL එක හෝ Servlet URL එක සෙවීම
            # සාමාන්‍යයෙන් 'JobAdvertismentServlet' අඩංගු ලින්ක් එකක් පවතී
            match = re.search(r"((?:/employer/)?JobAdvertismentServlet\?[^\"'>\s]+)", row_html)
            
            if not match:
                # onclick හරහා විවෘත වන open_popup සොයා ගැනීම
                match_popup = re.search(r"open_popup\('([^']+)'\)", row_html)
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

            # Table එකෙන් දිනයන් සහ විස්තර ලබා ගැනීම
            cols = row.find_all('td')
            if len(cols) >= 5:
                # Opening Date & Closing Date
                closing_raw = ""
                for td in cols:
                    td_txt = td.text.strip()
                    # දිනයක් ඇති තීරුව හඳුනා ගැනීම (උදා: Sat Oct ...)
                    if any(month in td_txt for month in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]):
                        closing_raw = td_txt  # සාමාන්‍යයෙන් අන්තිමට හමුවන දිනය Closing Date වේ

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
    """Popup පිටුව ඇතුළෙන් ඇත්තම Flyer Image Link එක ගැනීම"""
    try:
        res = requests.get(popup_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')

        # Iframe තිබේ නම් එය ඇතුළට යාම
        iframe = soup.find('iframe')
        if iframe and iframe.get('src'):
            nested_url = requests.compat.urljoin(popup_url, iframe['src'])
            return extract_flyer_image_url(nested_url)

        # Image tags පරීක්ෂා කිරීම
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if any(ext in src.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                if 'logo' not in src.lower() and 'icon' not in src.lower() and 'button' not in src.lower():
                    return requests.compat.urljoin(popup_url, src)
    except Exception as e:
        print(f"⚠️ Image URL සෙවීමේ දෝෂයක්: {e}")
    return None

# --- 4. INTEGRATION, IMGBB UPLOAD & DATABASE INSERTION ---
def process_and_save_vacancy(job_data):
    topjobs_img_url = extract_flyer_image_url(job_data['popup_url'])
    if not topjobs_img_url:
        return

    print(f"\n📥 Topjobs වෙතින් Flyer එක හමු විය: {topjobs_img_url}")

    try:
        # පින්තූරය Download කරගැනීම
        img_res = requests.get(topjobs_img_url, headers=HEADERS, timeout=20)
        if img_res.status_code != 200:
            print(f"⚠️ පින්තූරය Download කරගත නොහැකි විය.")
            return

        base64_data = base64.b64encode(img_res.content).decode('utf-8')
        mime_type = "image/png" if ".png" in topjobs_img_url.lower() else "image/jpeg"

        # 1. ImgBB වෙත Upload කිරීම (via upload-image Edge Function)
        print("☁️ ImgBB වෙත පින්තූරය Upload කරමින් පවතී...")
        upload_res = supabase.functions.invoke('upload-image', {
            'body': {
                'base64Image': base64_data
            }
        })

        upload_data = upload_res.get('data') or {}
        permanent_img_url = None

        if upload_data and upload_data.get('success'):
            permanent_img_url = upload_data.get('url')
            print(f"✅ ImgBB Upload සාර්ථකයි! Link: {permanent_img_url}")
        else:
            permanent_img_url = topjobs_img_url

        # 2. Duplicate Checking (දැනටමත් පද්ධතියේ තිබේදැයි බැලීම)
        check_dup = supabase.table("posts").select("id").eq("image_url", permanent_img_url).execute()
        if len(check_dup.data) > 0:
            print(f"🔄 Duplicate: මෙම පෝස්ටුව දැනටමත් ඇත. (Skipped)")
            return

        # 3. Gemini Vision මගින් දත්ත කියවීම (via scan-flyer Edge Function)
        print("🤖 Gemini AI මගින් Flyer එක කියවමින් පවතී...")
        invoke_res = supabase.functions.invoke('scan-flyer', {
            'body': {
                'base64Data': base64_data,
                'mimeType': mime_type
            }
        })

        res_data = invoke_res.get('data', {})
        if not res_data or 'candidates' not in res_data:
            print(f"⚠️ AI කියවීම අසාර්ථක විය.")
            return

        raw_text = res_data['candidates'][0]['content']['parts'][0]['text']
        raw_text = re.sub(r'```json|```', '', raw_text).strip()
        
        import json
        extracted = json.loads(raw_text)

        contact_info = extracted.get('contact', '')
        desc = "පෞද්ගලික අංශයේ රැකියා ඇබෑර්තුවකි."
        if contact_info:
            desc = f"පෞද්ගලික අංශයේ රැකියා ඇබෑර්තුවකි. වැඩිදුර විස්තර සඳහා අමතන්න: {contact_info}"

        final_closing_date = job_data['closing_date'] if job_data['closing_date'] else extracted.get("closing")

        # 4. Posts Table එකට Pending ලෙස Insert කිරීම
        payload = {
            "title": extracted.get("title") or "Private Job Vacancy",
            "organization": extracted.get("org") or "Private Company",
            "category": "Private Job",
            "meq_level": extracted.get("meq_level", "NONE"),
            "salary_amount": extracted.get("salary") or "සඳහන් නැත",
            "closing_date": final_closing_date,
            "qualifications": extracted.get("qualifications") or "දැන්වීම පරීක්ෂා කරන්න",
            "description": desc,
            "image_url": permanent_img_url,  # ImgBB URL
            "status": "PENDING", 
            "source": "TOPJOBS_AUTO"
        }

        supabase.table("posts").insert(payload).execute()
        print(f"🎉 සාර්ථකව Database එකට එක් විය: {payload['title']}")

    except Exception as err:
        print(f"❌ Process කිරීමේ දෝෂයක්: {err}")

if __name__ == "__main__":
    print("🚀 Topjobs ස්වයංක්‍රීය Scraper ක්‍රියාවලිය ආරම්භ විය...")
    jobs_list = get_today_vacancies()
    
    # පළමු රැකියා 10 පමණක් ආරක්ෂිතව Process කිරීම (Rate Limit වැළැක්වීමට)
    for job in jobs_list[:10]:
        process_and_save_vacancy(job)
        time.sleep(4)
            
    print("\n✨ සියලු දත්ත එක් කිරීම අවසන්!")
