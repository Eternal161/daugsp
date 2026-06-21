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


# =========================================================
# CONFIG SOCOLIVE (THAY THẾ TIẾU LÂM TV)
# =========================================================
TARGET_SITE   = "https://sv2.tieulam.xyz/trang-chu?type=football"
FILE_PATH     = "tieulam.json" # Giữ nguyên tên file để App Sáng TV không bị lỗi
LIMIT_MATCHES = 10  

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
# JS: BÓC TÁCH SOCOLIVE (THUẬT TOÁN QUÉT THẺ A TỔNG QUÁT)
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();

    // Socolive thường bọc mỗi trận đấu trong 1 thẻ <a> có link chứa số ID phòng
    const anchors = Array.from(document.querySelectorAll('a'));
    
    for (const a of anchors) {
        const href = a.href || '';
        if (seen.has(href)) continue;

        // Tìm thẻ a chứa ít nhất 2 ảnh (logo 2 đội) và link có vẻ là link phòng live
        const imgs = Array.from(a.querySelectorAll('img'));
        if (imgs.length < 2) continue;

        const isMatchLink = /\\/room\\/|\\/live\\/|\\/truc-tiep\\//.test(href) || href.match(/\\d{4,}/);
        if (!isMatchLink) continue;

        seen.add(href);

        let homeLogo = imgs[0].src;
        let awayLogo = imgs[1].src;

        let home = clean(imgs[0].alt) || '';
        let away = clean(imgs[1].alt) || '';

        // Vét text nếu ảnh không có alt
        const texts = Array.from(a.querySelectorAll('span, p, div'))
                           .map(e => clean(e.innerText))
                           .filter(t => t.length > 1 && !t.includes('/') && !t.includes(':'));
        
        if (!home && texts.length >= 2) {
            home = texts[0];
            away = texts[1];
        } else if (!home) {
            home = 'Đội nhà';
            away = 'Đội khách';
        }

        // Tìm thời gian
        let timeStr = 'Sắp diễn ra';
        const timeNode = Array.from(a.querySelectorAll('span, p, div')).find(e => clean(e.innerText).match(/\\d{1,2}:\\d{2}|\\d{1,2}\\/\\d{2}|Live/i));
        if (timeNode) {
            timeStr = clean(timeNode.innerText);
        }

        const isLive = /live|trực tiếp|đang phát/i.test(a.innerText) || timeStr.includes('-');

        results.push({ href, home, away, timeStr, homeLogo, awayLogo, tournament: 'Socolive', isLive });
    }
    return results;
}
"""

def capture_stream(context, match_url: str) -> list:
    page = context.new_page()
    
    
    captured_link = None
    
    def handle_request(req):
        nonlocal captured_link
        url = req.url
        # 💡 GIỮ NGUYÊN auth_key, KHÔNG CẮT BỎ NHƯ TIẾU LÂM
        if ".m3u8" in url.lower() and not captured_link:
            if "auth_key=" in url or "pull" in url.lower() or "stream" in url.lower():
                captured_link = url

    page.on("request", handle_request)
    
    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=45000)
        # Bấm nút Play nếu có (đánh thức luồng stream)
        try:
            page.locator('button[class*="play"], div[class*="play"]').first.click(timeout=3000)
        except:
            pass

        # Quét link siêu tốc (tối đa 12 giây)
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
    print(f"🚀 BẮT ĐẦU BOT SOCOLIVE (Giờ VN): {now_str}")

    with sync_playwright() as p:
        # BẬT GIAO DIỆN KẾT HỢP XVFB HOẶC TASK SCHEDULER
        browser = p.chromium.launch(executable_path="/usr/bin/chromium-browser", headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        try: stealth_sync(page)
        except: pass
        
        try:
            print(f"📺 Đang mở trang chủ Socolive ({TARGET_SITE})...")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
            
            print("⏳ Đang cuộn trang để tải dữ liệu trận đấu...")
            for _ in range(6):
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(1500)
            
            page.wait_for_timeout(3000) 
            
        except PWTimeout:
            print("   ⚠️ Lỗi Timeout mạng. Tiếp tục trích xuất những gì đang có...")
            page.screenshot(path="man_hinh_bot.png")
        except Exception as e:
            print(f"   ⚠️ Có sự cố mạng: {e}")
            
        raw_matches = page.evaluate(JS_EXTRACT)
        
        valid_matches = []
        seen_keys = set()
        for m in raw_matches:
            h_lower = (m.get('home') or "").lower()
            a_lower = (m.get('away') or "").lower()
            if not h_lower or not a_lower or "unknown" in h_lower: continue
            if "đội nhà" in h_lower: continue
            
            key = f"{h_lower} vs {a_lower}"
            if key not in seen_keys:
                seen_keys.add(key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        channels = []
        
        print(f"👉 Phát hiện {len(raw_matches)} trận đấu. Bắt đầu soi link m3u8 Socolive...")
        for idx, m in enumerate(raw_matches, 1):
            print(f"   [{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            
            streams = capture_stream(context, m["href"])
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
    
    msg = f"⚽ Update Socolive (Thay Tiếu Lâm): {now_str}"
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã CẬP NHẬT thành công lên {REPO_NAME}/{FILE_PATH}")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã TẠO MỚI thành công file {FILE_PATH} trên GitHub!")

if __name__ == "__main__":
    scrape_and_push()
