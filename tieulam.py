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
# CONFIG TIẾU LÂM TV - BẢN V6: ÉP CHỜ LOAD & NHẬN DIỆN CẤU TRÚC
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
# 💡 JS: BÓC TÁCH DỰA TRÊN CẤU TRÚC CỘT (KHÔNG DÙNG CLASS)
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

        // 💡 TÌM GRID BẰNG CẤU TRÚC: Hộp nào có đúng 3 cột và chứa ít nhất 2 ảnh
        let gridBox = null;
        for (let div of a.querySelectorAll('div')) {
            if (div.children.length === 3 && div.querySelectorAll('img').length >= 2) {
                gridBox = div;
                break;
            }
        }
        
        // Nếu không tìm thấy cấu trúc 3 cột -> Nó là Banner -> Bỏ qua
        if (!gridBox) continue;

        seen.add(href);

        // 1. Tên Giải Đấu (Lấy thẻ text đầu tiên trong khối trận)
        let tournament = 'Tiếu Lâm Live';
        const spans = Array.from(a.querySelectorAll('span, p'));
        if (spans.length > 0) {
            const firstText = clean(spans[0].innerText);
            if (firstText && firstText.length > 2) tournament = firstText;
        }

        // 2. Tách 3 cột
        const homeCol = gridBox.children[0];
        const centerCol = gridBox.children[1];
        const awayCol = gridBox.children[2];

        // Lấy Tên Đội
        const homeTextEls = Array.from(homeCol.querySelectorAll('span, p')).map(e => clean(e.innerText)).filter(t => t);
        const awayTextEls = Array.from(awayCol.querySelectorAll('span, p')).map(e => clean(e.innerText)).filter(t => t);
        const home = homeTextEls.length ? homeTextEls[homeTextEls.length - 1] : 'Đội nhà';
        const away = awayTextEls.length ? awayTextEls[awayTextEls.length - 1] : 'Đội khách';

        // Lấy Logo
        const homeImg = homeCol.querySelector('img');
        const awayImg = awayCol.querySelector('img');
        const homeLogo = homeImg ? homeImg.src : '';
        const awayLogo = awayImg ? awayImg.src : '';

        // 3. Thời gian & Tỉ số
        let timeStr = clean(centerCol.innerText).replace(/\\n/g, ' ');

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
                clean_url = url.split("?")[0]
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

def scrape_and_push():
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT TIẾU LÂM TV (Giờ VN): {now_str}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass
        
        try:
            print(f"📺 Đang mở trang chủ Tiếu Lâm...")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
            
            # 💡 QUAN TRỌNG: Ép bot phải đợi đến khi danh sách trận đấu render ra màn hình
            print("⏳ Đang chờ hệ thống tải danh sách các trận đấu...")
            page.wait_for_selector('a[href*="/truc-tiep/"]', state="visible", timeout=20000)
            page.wait_for_timeout(3000) # Nghỉ thêm 3s cho chắc ăn
            
        except PWTimeout:
            print("   ⚠️ Web load chậm hoặc hiện tại không có trận đấu nào trên lịch!")
        except Exception as e:
            print(f"   ⚠️ Có sự cố mạng: {e}")
            
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
        
        print(f"👉 Phát hiện {len(raw_matches)} trận đấu. Bắt đầu soi link m3u8...")
        for idx, m in enumerate(raw_matches, 1):
            print(f"   [{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            streams = capture_stream(context, m["href"])
            channels.append(build_channel(m, streams))

    if not GITHUB_TOKEN:
        print("\n⚠️ Không có GITHUB_TOKEN. Chỉ lưu ra file local.")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "id": "tieulam", "name": "Tiếu Lâm TV", "last_updated": now_str, 
                "groups": [{"id": "live", "name": "🔴 Live bóng đá", "channels": channels}]
            }, f, indent=2, ensure_ascii=False)
        return

    print("\n⏳ Đang tải dữ liệu lên GitHub...")
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
        print(f"✅ Đã CẬP NHẬT GHI ĐÈ thành công lên {REPO_NAME}/{FILE_PATH}")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã TẠO MỚI thành công file {FILE_PATH} trên GitHub!")

if __name__ == "__main__":
    scrape_and_push()
