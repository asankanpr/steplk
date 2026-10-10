import os
import re
import time
import base64
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from supabase import create_client, Client

# --- 1. SECURITY: Securely load Environment Variables ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("🔒 ආරක්ෂක දෝෂයකි: Supabase credentials හමු නොවිණි.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Browser Headers to avoid being blocked by Topjobs
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Referer': 'http://www.topjobs.lk/',
}
BASE_URL = "http://www.topjobs.lk"

# --- 2. SMART DATE CONVERTER ---
def convert_topjobs_date(date_str):
    """Topjobs හි ඇති 'Sat Oct 17 2026' වැනි දිනයන් '2026-10-17' YYYY-MM-DD ආකෘතියට හැරවීම"""
    try:
        # Extra spaces අයින් කරලා පිරිසිදු කිරීම
        clean_str = re.sub(r'\s+', ' ', date_str.strip())
        # Parse the date (e.g., "Sat Oct 10 2026" or "Sat Oct 10, 2026")
        clean_str = clean_str.replace(',', '')
        date_obj = datetime.strptime(clean_str, "%a %b %d %Y")
        return date_obj.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"⚠️ දිනය කියවීමේ දෝෂයක් ({date_str}): {e}")
        return None

# --- 3. SCRAPING LOGIC ---
def get_today_vacancies():
    url = f"{BASE_URL}/applicant/vacancybyfunctionalarea.jsp"
    print(f"🔍 Topjobs වෙබ් අඩවිය පරීක්ෂා කරමින් පවතී...")
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=25)
        if res.status_code != 200:
            print(f"⚠️ වෙබ් අඩවිය ලබාගත නොහැකි විය. Status Code: {res.status_code}")
            return []

        soup = BeautifulSoup(res.text, 'html.parser')
        vacancies = []

        rows = soup.find_all('tr')
        for row in rows:
            onclick_attr = row.get('onclick', '') or ''
            
            match = re.search(r"open_popup\('([^']+)'\)", onclick_attr)
            if not match:
                a_tag = row.find('a', href=re.compile(r'open_popup'))
                if a_tag:
                    match = re.search(r"open_popup\('([^']+)'\)", a_tag['href'])

            if match:
                popup_rel_url = match.group(1)
                full_popup_url = requests.compat.urljoin(BASE_URL, popup_rel_url)
                
                # Smart HTML Column Extraction (From the table directly!)
                cols = row.find_all('td')
                if len(cols) >= 5:
                    title_employer_text = cols[1].text.strip()
                    opening_date_raw = cols[3].text.strip()
                    closing_date_raw = cols[4].text.strip()

                    closing_date_formatted = convert_topjobs_date(closing_date_raw)

                    vacancies.append({
                        'title_raw': title_employer_text,
                        'closing_date': closing_date_formatted,
                        'popup_url': full_popup_url
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
            if any(ext in src.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                if 'logo' not in src.lower() and 'icon' not in src.lower() and 'button' not in src.lower():
                    return requests.compat.urljoin(popup_url, src)
    except Exception as e:
        print(f"⚠️ Image URL සෙවීමේ දෝෂයක්: {e}")
    return None

# --- 4. INTEGRATION & DATABASE INSERTION ---
def process_and_save_vacancy(job_data):
    image_url = extract_flyer_image_url(job_data['popup_url'])
    if not image_url:
        return

    print(f"📸 Flyer එක හමු විය: {image_url}")

    try:
        # SECURITY: Duplicate Checking
        # එකම පින්තූරය හෝ එකම නම දෙවරක් යාම වැළැක්වීම
        check_dup = supabase.table("posts").select("id").eq("image_url", image_url).execute()
        if len(check_dup.data) > 0:
            print(f"🔄 Duplicate: මෙම පෝස්ටුව දැනටමත් පද්ධතියේ ඇත. (Skip කරනු ලැබේ)")
            return

        img_res = requests.get(image_url, headers=HEADERS, timeout=20)
        if img_res.status_code != 200:
            return

        base64_data = base64.b64encode(img_res.content).decode('utf-8')
        mime_type = "image/png" if ".png" in image_url.lower() else "image/jpeg"

        # Edge Function එක හරහා AI එකට යැවීම
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

        # ** SMART MERGE **
        # AI එක හොයාගත්තු Closing Date එකට වඩා අර Web Table එකෙන් ගත්තු Date එක 100% නිවැරදියි. 
        # ඒ නිසා අපි Web එකේ date එක පාවිච්චි කරනවා.
        final_closing_date = job_data['closing_date'] if job_data['closing_date'] else extracted.get("closing")

        payload = {
            "title": extracted.get("title") or "Private Job Vacancy",
            "organization": extracted.get("org") or "Private Company",
            "category": "Private Job",
            "meq_level": extracted.get("meq_level", "NONE"),
            "salary_amount": extracted.get("salary") or "සඳහන් නැත",
            "closing_date": final_closing_date, # Smart Date Applied
            "qualifications": extracted.get("qualifications") or "දැන්වීම පරීක්ෂා කරන්න",
            "description": desc,
            "image_url": image_url,
            "status": "PENDING", 
            "source": "TOPJOBS_AUTO"
        }

        supabase.table("posts").insert(payload).execute()
        print(f"✅ සාර්ථකව Database එකට එක් විය: {payload['title']} (අවසන් දිනය: {final_closing_date})")

    except Exception as err:
        print(f"❌ Process කිරීමේ දෝෂයක්: {err}")

if __name__ == "__main__":
    print("🚀 Topjobs ස්වයංක්‍රීය Scraper ක්‍රියාවලිය ආරම්භ විය...")
    jobs_list = get_today_vacancies()
    
    # 5. SECURITY: Rate Limiting & Safe Batching
    for job in jobs_list[:15]: # දිනකට උපරිම 15ක් පමණක් ආරක්ෂිතව පරීක්ෂා කිරීම
        process_and_save_vacancy(job)
        time.sleep(4) # Server එක block වීම වැළැක්වීමට තත්පර 4ක විරාමයක්
            
    print("🎉 සියලු දත්ත එක් කිරීම අවසන්!")
