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
# CONFIG XÂY CON TV - BẢN FULL CHỐNG ĐẠN
# =========================================================
TARGET_SITE   = "https://sv2.xaycon4.live/lich-thi-dau/bong-da?by=state&value=live"
BASE_URL      = "https://sv2.xaycon4.live"
FILE_PATH     = "xaycon.json"
LIMIT_MATCHES = 10 # Tăng số lượng để hiện được nhiều trận sắp tới hơn

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/daugsp")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
LOGO_CACHE = {}

# =========================================================
# HELPER FUNCTIONS
# =========================================================
def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"luongson-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

def get_api_logo(team_name: str) -> str:
    if not team_name or team_name == "Unknown": return ""
    team_name = re.sub(r"\bFc\b$", "FC", team_name).strip()
    if team_name in LOGO_CACHE: return LOGO_CACHE[team_name]
    try:
        slug = team_name.lower().replace(" ", "-")
        r = requests.get(f"https://football-logos.cc/{slug}/", headers=_HEADERS, timeout=5)
        m = re.search(r'https://football-logos\.cc/logos/[^"]+\.png', r.text)
        if m:
            LOGO_CACHE[team_name] = m.group(0)
            return m.group(0)
    except: pass
    LOGO_CACHE[team_name] = ""
    return ""

def get_final_logo(team_name: str, site_logo: str) -> str:
    api_logo = get_api_logo(team_name)
    if api_logo: return api_logo
    if site_logo and site_logo.startswith("http"): return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# XỬ LÝ THỜI GIAN (CHỐNG LỖI THÁNG 15)
# =========================================================
def parse_time_from_url(url: str) -> str:
    try:
        slug = url.rstrip('/').split('/')[-1]
        # Format: YYYY-MM-DD-HHMM
        m = re.search(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})', slug)
        if m:
            y, mth, d, h, mn = map(int, m.groups())
            if mth > 12: mth, d = d, mth
            dt_utc = datetime.datetime(y, mth, d, h, mn)
            dt_vn = dt_utc + datetime.timedelta(hours=7)
            return dt_vn.strftime("%H:%M %d/%m/%Y")
        # Format: HHMM-DD-MM-YYYY
        m2 = re.search(r'(\d{2})(\d{2})-(\d{2})-(\d{2})-(\d{4})', slug)
        if m2:
            hh, mm, dd, mo, yyyy = m2.groups()
            return f"{hh}:{mm} {dd}/{mo}/{yyyy}"
    except: pass
    return ""

def parse_teams_from_title(title: str):
    clean = re.sub(r'[-_]\d{4}-\d{2}-\d{2}[-_]\d{4}$', '', title)
    clean = re.sub(r'\.\s*[A-Za-z0-9 \-]{3,30}$', '', clean).strip()
    if re.fullmatch(r'[a-z0-9\-]+', clean): clean = clean.replace('-', ' ')
    m = re.split(r'\s+vs\.?\s+', clean, maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2 and m[0].strip() and m[1].strip(): return m[0].strip().title(), m[1].strip().title()
    return clean.strip().title(), "Unknown"

# =========================================================
# JS: LẤY DỮ LIỆU TỪ XÂY CON TV (DỰA TRÊN CẤU TRÚC F12 MỚI)
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();

    // 💡 Các trận đấu được bọc trong thẻ <a> có chứa link xem trực tiếp
    const matchCards = document.querySelectorAll('a.block.w-full[href*="/xem-truc-tiep/"]');

    for (const a of matchCards) {
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);

        // 1. Lấy Giải đấu (nằm trong thẻ p.text-sm)
        let tournament = '';
        const leagueEl = a.querySelector('p.text-sm');
        if (leagueEl) tournament = clean(leagueEl.innerText);

        // 2. Lấy Thời gian & Ngày
        let timeStr = '';
        const timeSpans = a.querySelectorAll('.flex.items-center.gap-1 span');
        let timeText = "", dateText = "";
        
        timeSpans.forEach(span => {
            if (span.classList.contains('bg-yellow-300')) {
                timeText = clean(span.innerText);
            } else if (span.innerText.includes('/')) {
                dateText = clean(span.innerText);
            }
        });
        
        if (timeText || dateText) {
            timeStr = `${timeText} ${dateText}`.trim();
        } else {
            // Nếu trận đấu đang Live, text có thể hiển thị khác, vét cạn text
            const statusDiv = a.querySelector('.flex.items-center.gap-1');
            if (statusDiv) timeStr = clean(statusDiv.innerText);
        }

        // 3. Lấy 2 Đội bóng & Logo
        let home = '', away = '', homeLogo = '', awayLogo = '';
        const imgs = a.querySelectorAll('img.object-contain');
        const spans = a.querySelectorAll('span.font-semibold.truncate');
        
        if (imgs.length >= 2 && spans.length >= 2) {
            homeLogo = imgs[0].src;
            awayLogo = imgs[1].src;
            home = clean(spans[0].innerText);
            away = clean(spans[1].innerText);
        }

        results.push({ href, home, away, timeStr, homeLogo, awayLogo, tournament });
    }
    return results;
}
"""

# =========================================================
# CAPTURE STREAM (ĐÃ FIX LỖI LIVE/LIVE)
# =========================================================
def capture_stream(context, match_url: str) -> list:
    page = context.new_page()
    try: Stealth().apply_stealth_sync(page)
    except: pass
    streams = set()
    BAD = [".gif", ".png", ".jpg", ".mp4", "saba.m3u8", "/ad/", "/ads/", "quangcao", "banner"]

    def process_url(url):
        u = url.lower()
        if ".m3u8" in u and not any(b in u for b in BAD):
            # CHUẨN HÓA LINK CDNFÁTER ĐỂ PHÁT ĐƯỢC 100%
            if "cdnfaster-a.live/" in u and "cdnfaster-a.live/live/" not in u:
                url = url.replace("cdnfaster-a.live/", "cdnfaster-a.live/live/")
            streams.add(url)

    page.on("request",  lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(match_url, wait_until="load", timeout=60000)
        try:
            vp = page.viewport_size
            if vp: page.mouse.click(vp["width"] // 2, vp["height"] // 2)
        except: pass
        page.wait_for_timeout(8000)
        
        deadline = time.time() + 15
        while time.time() < deadline:
            if any("cdnfaster-a.live" in s.lower() for s in streams): break
            time.sleep(1)
    except: pass
    finally: page.close()

    if not streams: return []
    scored = []
    for s in streams:
        score = 0
        lo = s.lower()
        if "cdnfaster-a.live" in lo: score += 10000 
        if "100ycdn" in lo: score += 5000
        scored.append((score, s))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [s for sc, s in scored]

# =========================================================
# XÂY DỰNG CẤU TRÚC JSON
# =========================================================
def build_channel(m: dict, stream_urls: list) -> dict:
    home = m.get("home", "").title()
    away = m.get("away", "").title()
    thoi_gian = re.sub(r'(\d{1,2}:\d{2})(\d{1,2}/\d{2})', r'\1 \2', m.get("timeStr", ""))
    
    cid = make_id(m["href"])
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {m.get('tournament')}" if m.get('tournament') else "") + (f" | {thoi_gian}" if thoi_gian else "")

    # TRẠNG THÁI HIỂN THỊ
    is_live = len(stream_urls) > 0
    label_text = "● Live" if is_live else ("🔴 Chờ stream" if m.get("isLiveUI") else "⏳ Chưa live")
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get("isLiveUI") else "#d54f1a")

    return {
        "id": cid, "name": display_name, 
        "tournament": m.get("tournament", ""),
        "logo_nha": m.get("homeLogo"), "logo_khach": m.get("awayLogo"),
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": m.get("homeLogo"), "width": 1600, "height": 1200},
        "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
        "sources": [{
            "id": cid, "name": "Lương Sơn",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": [{"id": make_link_id(), "name": f"Link {idx+1}", "type": "hls", "default": idx==0, "url": u} for idx, u in enumerate(stream_urls[:2])]}]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push():
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT LƯƠNG SƠN (Giờ VN): {now_str} - tieulam.py:239")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        try: Stealth().apply_stealth_sync(page)
        except: pass

        try: page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60000)
        except: pass
        page.wait_for_timeout(5000)

        # Cuộn trang sâu hơn để lấy cả trận sắp tới
        for _ in range(5):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(1000)

        raw_matches = page.evaluate(JS_EXTRACT)
        
        valid_matches = []
        seen_keys = set()

        for m in raw_matches:
            h = (m.get("home") or "").strip()
            a = (m.get("away") or "").strip()
            if not h or not a or h == a or len(h) < 2:
                slug = m["href"].rstrip("/").split("/")[-1]
                fh, fa = parse_teams_from_title(slug)
                m["home"], m["away"] = fh, fa

            m["home"] = re.sub(r' vao luc.*$', '', m["home"], flags=re.IGNORECASE).strip()
            m["away"] = re.sub(r' vao luc.*$', '', m["away"], flags=re.IGNORECASE).strip()

            h_lower, a_lower = m["home"].lower(), m["away"].lower()
            
            # CHỐNG RÁC NHẬN ĐỊNH
            if any(x in h_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]) or \
               any(x in a_lower for x in ["unknown", "luongson", "#main", "nhan dinh", "nhận định"]):
                continue
                
            match_key = f"{h_lower} vs {a_lower}"
            if match_key not in seen_keys:
                seen_keys.add(match_key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        print(f"\n🎥 QUÉT TẤT CẢ {len(raw_matches)} TRẬN (BAO GỒM TRẬN SẮP TỚI)... - tieulam.py:286")

        for idx, m in enumerate(raw_matches, 1):
            m["timeStr"] = m.get("timeStr") or parse_time_from_url(m["href"]) or "Không rõ"
            print(f"[{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']}) - tieulam.py:290")
            
            # Chỉ đi cào stream nếu trận đấu có dấu hiệu đang Live (Tiết kiệm thời gian)
            m["streams"] = []
            if m.get("isLiveUI") or any(char.isdigit() for char in m["timeStr"]):
                m["streams"] = capture_stream(context, m["href"])
            
            m["homeLogo"] = get_final_logo(m["home"], m.get("homeLogo"))
            m["awayLogo"] = get_final_logo(m["away"], m.get("awayLogo"))

    # Đóng gói JSON
    channels = [build_channel(m, m["streams"]) for m in raw_matches]
    content = json.dumps({
        "id": "xaycon", 
        "name": "Xây Con TV", 
        "last_updated": now_str, 
        "groups": [{"id": "live", "name": "🔴 Trực tiếp & Sắp tới", "channels": channels}]
    }, indent=2, ensure_ascii=False)
    
    # 💡 LƯU RA FILE LOCAL ĐỂ TEST 
    try:
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n💾 Đã lưu file local thành công: {FILE_PATH}  Đã ghi {len(channels)} trận! - tieulam.py:313")
    except Exception as e:
        print(f"❌ Lỗi khi lưu file local: {e} - tieulam.py:315")

    # Đẩy lên GitHub
    if GITHUB_TOKEN:
        repo = Github(GITHUB_TOKEN).get_repo(REPO_NAME)
        msg = "⚽ Sync Lương Sơn: " + now_str
        try:
            existing = repo.get_contents(FILE_PATH)
            repo.update_file(existing.path, msg, content, existing.sha)
            print("\n✅ Đã cập nhật thành công lên GitHub! - tieulam.py:324")
        except:
            repo.create_file(FILE_PATH, msg, content)
            print("\n✅ Đã khởi tạo file mới trên GitHub! - tieulam.py:327")

if __name__ == "__main__":
    scrape_and_push()
