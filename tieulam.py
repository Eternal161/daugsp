import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
from bs4 import BeautifulSoup
from github import Github

# =========================================================
# CONFIG SOCOLIVE (BẢN SIÊU NHẸ - KHÔNG DÙNG PLAYWRIGHT)
# =========================================================
TARGET_SITE   = "https://sv2.tieulam.xyz/trang-chu?type=football"
FILE_PATH     = "tieulam.json" 
LIMIT_MATCHES = 10  

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/daugsp")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}

def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"kaytee-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

def get_final_logo(team_name: str, site_logo: str) -> str:
    if site_logo and site_logo.startswith("http"): return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

def capture_stream_requests(match_url: str) -> list:
    # Quét trực tiếp mã nguồn HTML để mò link m3u8 (siêu tốc)
    try:
        r = requests.get(match_url, headers=HEADERS, timeout=10)
        html_text = r.text.replace('\\/', '/') # Sửa lỗi gạch chéo trong JSON
        
        # Tìm tất cả các link m3u8 có trong trang
        links = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html_text)
        if links:
            # Lọc ưu tiên các link có chứa auth_key hoặc pull
            valid_links = [l for l in links if 'auth_key' in l or 'pull' in l or 'stream' in l]
            if valid_links:
                return [valid_links[0]]
            return [links[0]]
    except:
        pass
    return []

def build_channel(m, stream_urls):
    home = (m.get('home') or "Unknown").title()
    away = (m.get('away') or "Unknown").title()
    thoi_gian = m.get('timeStr') or "Không rõ"
    
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {thoi_gian}" if thoi_gian else "")

    cid = make_id(m['href'])
    is_live = len(stream_urls) > 0 or m.get('isLive', False)
    
    label_text = "● Live" if is_live else ("🔴 Chờ stream" if m.get('isLive') else "⏳ Sắp đá")
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get('isLive') else "#d54f1a")

    return {
        "id": cid, 
        "name": display_name, 
        "tournament": m.get("tournament", "Socolive (Sáng TV)"),
        "logo_nha": get_final_logo(home, m.get('homeLogo')), 
        "logo_khach": get_final_logo(away, m.get('awayLogo')),
        "type": "single", 
        "display": "thumbnail-only", 
        "enable_detail": False,
        "image": {
            "padding": 1, 
            "background_color": "#ececec", 
            "display": "contain", 
            "url": get_final_logo(home, m.get('homeLogo')), 
            "width": 1600, 
            "height": 1200
        },
        "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
        "sources": [{
            "id": cid, 
            "name": "Socolive",
            "contents": [{
                "id": cid, 
                "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": [{"id": make_link_id(), "name": "Link Trực Tiếp", "type": "hls", "default": True, "url": stream_urls[0]}] if stream_urls else []}]
            }]
        }],
    }

def scrape_and_push():
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT SOCOLIVE (Bản Siêu Nhẹ - Giờ VN): {now_str}")

    try:
        print(f"📺 Đang tải trang chủ Socolive ({TARGET_SITE})...")
        res = requests.get(TARGET_SITE, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
    except Exception as e:
        print(f"⚠️ Có sự cố mạng: {e}")
        return

    anchors = soup.find_all('a', href=True)
    raw_matches = []
    seen_keys = set()
    seen_href = set()

    for a in anchors:
        href = a['href']
        if href in seen_href: continue
        
        # Chỉ lấy link phòng live
        if not re.search(r'(/room/|/live/|/truc-tiep/|\d{4,})', href): continue
        if not href.startswith('http'):
            href = "https://sv2.tieulam.xyz" + href if href.startswith('/') else "https://sv2.tieulam.xyz/" + href

        imgs = a.find_all('img')
        if len(imgs) < 2: continue
        
        seen_href.add(href)
        
        home_logo = imgs[0].get('src', '')
        away_logo = imgs[1].get('src', '')
        home = imgs[0].get('alt', '').strip()
        away = imgs[1].get('alt', '').strip()

        # Vét text nếu ảnh không có tên
        texts = [t.get_text(strip=True) for t in a.find_all(['span', 'p', 'div'])]
        texts = [t for t in t if len(t) > 1 and '/' not in t and ':' not in t]
        
        if not home and len(texts) >= 2:
            home, away = texts[0], texts[1]
        elif not home:
            home, away = "Đội nhà", "Đội khách"

        h_lower = home.lower()
        a_lower = away.lower()
        if not h_lower or not a_lower or "unknown" in h_lower or "đội nhà" in h_lower: continue

        key = f"{h_lower} vs {a_lower}"
        if key in seen_keys: continue
        seen_keys.add(key)

        # Tìm thời gian
        time_str = "Sắp diễn ra"
        all_texts = [t.get_text(strip=True) for t in a.find_all(['span', 'p', 'div'])]
        for t in all_texts:
            if re.search(r'\d{1,2}:\d{2}|\d{1,2}/\d{2}|Live', t, re.I):
                time_str = t
                break

        is_live = bool(re.search(r'live|trực tiếp|đang phát', a.get_text(), re.I) or '-' in time_str)

        raw_matches.append({
            'href': href, 'home': home, 'away': away, 
            'timeStr': time_str, 'homeLogo': home_logo, 'awayLogo': away_logo, 
            'tournament': 'Socolive', 'isLive': is_live
        })

    raw_matches = raw_matches[:LIMIT_MATCHES]
    channels = []
    
    print(f"👉 Phát hiện {len(raw_matches)} trận đấu. Bắt đầu quét nhanh link m3u8...")
    for idx, m in enumerate(raw_matches, 1):
        print(f"   [{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
        streams = capture_stream_requests(m['href'])
        channels.append(build_channel(m, streams))

    if not GITHUB_TOKEN:
        print("\n⚠️ Không có GITHUB_TOKEN. Chỉ lưu ra file local.")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "id": "tieulam", "name": "Socolive (Sáng TV)", "last_updated": now_str, 
                "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]
            }, f, indent=2, ensure_ascii=False)
        return

    print("\n⏳ Đang tải dữ liệu lên GitHub...")
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    content = json.dumps({
        "id": "tieulam", "name": "Socolive (Sáng TV)", "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    msg = f"⚽ Update Socolive Siêu nhẹ: {now_str}"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã CẬP NHẬT thành công lên {REPO_NAME}/{FILE_PATH}")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã TẠO MỚI thành công file {FILE_PATH} trên GitHub!")

if __name__ == "__main__":
    scrape_and_push()
