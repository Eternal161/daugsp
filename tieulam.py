import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG TIẾU LÂM TV - BẢN V5: CHUẨN TÊN GIẢI & LỌC SẠCH CDN
# =========================================================
TARGET_SITE   = "https://sv1.tieulam1.live/trang-chu?type=football"
BASE_URL      = "https://sv1.tieulam1.live"
FILE_PATH     = "tieulam.json"
LIMIT_MATCHES = 15  

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/daugsp")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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

# =========================================================
# 💡 JS: CẬP NHẬT CLASS TÊN GIẢI ĐẤU DỰA VÀO ẢNH F12
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();

    const anchors = Array.from(document.querySelectorAll('a[href*="/truc-tiep/"]'));

    for (const a of anchors) {
        const href = a.href;
        if (seen.has(href)) continue;
        
        const gridBox = a.querySelector('div[class*="grid-cols-[1fr_auto_1fr]"]');
        if (!gridBox || gridBox.children.length < 3) continue;

        seen.add(href);

        // 1. 💡 Cập nhật chuẩn class Tên Giải Đấu
        const leagueEl = a.querySelector('span.text-xs.font-normal');
        const tournament = leagueEl ? clean(leagueEl.innerText) : 'Tiếu Lâm Live';

        const homeCol = gridBox.children[0];
        const centerCol = gridBox.children[1];
        const awayCol = gridBox.children[2];

        const homeSpan = homeCol.querySelector('span.truncate') || homeCol.querySelector('span');
        const awaySpan = awayCol.querySelector('span.truncate') || awayCol.querySelector('span');
        const home = homeSpan ? clean(homeSpan.innerText) : 'Đội nhà';
        const away = awaySpan ? clean(awaySpan.innerText) : 'Đội khách';

        const homeImg = homeCol.querySelector('img');
        const awayImg = awayCol.querySelector('img');
        const homeLogo = homeImg ? homeImg.src : '';
        const awayLogo = awayImg ? awayImg.src : '';

        let timeStr = clean(centerCol.innerText); 
        timeStr = timeStr.replace(/\\n/g, ' ');

        const isLive = /live|trực tiếp|đang phát/.test((a.innerText || '').toLowerCase()) || timeStr.includes('-');

        if (timeStr.toLowerCase().includes('sắp diễn ra')) {
            timeStr = 'Sắp diễn ra';
        } else if (!timeStr.toLowerCase().includes('live')) {
            if (timeStr.includes('-')) timeStr = 'Live ' + timeStr;
        }

        results.push({ href, home, away, timeStr, homeLogo, awayLogo, tournament, isLive });
    }
    return results;
}
"""

# =========================================================
# 💡 CAPTURE STREAM: LỌC BỎ CDN, CHỈ LẤY LINK GỐC
# =========================================================
def capture_stream(context, match_url: str) -> list:
    page = context.new_page()
    try: Stealth().apply_stealth_sync(page)
    except: pass
    
    captured_link = None
    
    def handle_request(req):
        nonlocal captured_link
        url = req.url
        if ".m3u8" in url.lower() and "/ad/" not in url.lower() and not captured_link:
            if "pull" in url.lower() or "live" in url.lower() or "asynccdn" in url.lower():
                # 1. Cắt đuôi token "?wsSession=..."
                clean_url = url.split("?")[0]
                
                # 2. 💡 Ép lấy link gốc (Cắt bỏ CDN mồi như 100ycdn)
                if "pull.asynccdn.xyz" in clean_url:
                    clean_url = "https://pull.asynccdn.xyz" + clean_url.split("pull.asynccdn.xyz")[1]
                
                captured_link = clean_url

    page.on("request", handle_request)
    
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=45000)
        for _ in range(12):
            if captured_link:
                break
            page.wait_for_timeout(1000)
    except Exception:
        pass
    finally:
        page.remove_listener("request", handle_request)
        page.close()
    
    return [captured_link] if captured_link else []

# =========================================================
# BUILD CHANNEL
# =========================================================
def build_channel(m, stream_urls):
    home = (m.get('home') or "Unknown").title()
    away = (m.get('away') or "Unknown").title()
    thoi_gian = m.get('timeStr') or "Không rõ"
    
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', thoi_gian)
    
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {thoi_gian}" if thoi_gian else "")

    cid = make_id(m['href'])
    is_live = len(stream_urls) > 0 or m.get('isLive', False)
    
    label_text = "● Live" if is_live else ("🔴 Chờ stream" if m.get('isLive') else "⏳ Sắp đá")
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get('isLive') else "#d54f1a")

    return {
        "id": cid, 
        "name": display_name, 
        "tournament": m.get("tournament", "Tiếu Lâm Live"),
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
            "name": "Tiếu Lâm",
            "contents": [{
                "id": cid, 
                "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": [{"id": make_link_id(), "name": "Link Trực Tiếp", "type": "hls", "default": True, "url": stream_urls[0]}] if stream_urls else []}]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push():
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT TIẾU LÂM TV (Giờ VN): {now_str} - bat_bong_da.py:190")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass
        
        try:
            print(f"📺 Đang mở trang chủ Tiếu Lâm... - bat_bong_da.py:200")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            print("⚠️ Web load quá chậm (quá 60s). Đang ép Bot cào tiếp... - bat_bong_da.py:203")
        except Exception as e:
            print(f"⚠️ Có sự cố mạng nhỏ: {e} - bat_bong_da.py:205")
            
        page.wait_for_timeout(5000)
        
        raw_matches = page.evaluate(JS_EXTRACT)
        
        valid_matches = []
        seen_keys = set()
        for m in raw_matches:
            h_lower = (m.get('home') or "").lower()
            a_lower = (m.get('away') or "").lower()
            if not h_lower or not a_lower or "unknown" in h_lower: continue
            
            key = f"{h_lower} vs {a_lower}"
            if key not in seen_keys:
                seen_keys.add(key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        channels = []
        
        print(f"👉 Phát hiện {len(raw_matches)} trận đấu (đã bỏ qua Banner). Bắt đầu soi link m3u8... - bat_bong_da.py:226")
        for idx, m in enumerate(raw_matches, 1):
            print(f"[{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']}) - bat_bong_da.py:228")
            
            streams = capture_stream(context, m["href"])
            channels.append(build_channel(m, streams))

    if not GITHUB_TOKEN:
        print("\n⚠️ Không có GITHUB_TOKEN. Chỉ lưu ra file local. - bat_bong_da.py:234")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "id": "tieulam", "name": "Tiếu Lâm TV", "last_updated": now_str, 
                "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]
            }, f, indent=2, ensure_ascii=False)
        return

    print("\n⏳ Đang tải dữ liệu lên GitHub... - bat_bong_da.py:242")
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    content = json.dumps({
        "id": "tieulam", "name": "Tiếu Lâm TV", "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    msg = f"⚽ Update Tiếu Lâm TV (VN Time): {now_str}"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã CẬP NHẬT GHI ĐÈ thành công lên {REPO_NAME}/{FILE_PATH} - bat_bong_da.py:254")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã TẠO MỚI thành công file {FILE_PATH} trên GitHub! - bat_bong_da.py:257")

if __name__ == "__main__":
    scrape_and_push()
