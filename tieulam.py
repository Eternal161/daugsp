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
# 💡 BỘ GIÁP STEALTH BẤT TỬ (Tự động thích ứng mọi phiên bản)
# =========================================================
def apply_stealth(page):
    try:
        # Thử cú pháp mới nhất của playwright-stealth
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        try:
            # Nếu bản cũ hơn thì dùng cú pháp class Stealth
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
    except Exception:
        pass

# =========================================================
# CONFIG XOILAC (CARIBBEAN BUSINESS) -> XUẤT TIEULAM.JSON
# =========================================================
TARGET_SITE   = "https://caribbeanbusiness.com/"
BASE_URL      = "https://caribbeanbusiness.com"
FILE_PATH     = "tieulam.json"
LIMIT_MATCHES = 12       # Quét 12 trận hot nhất
MAX_BLV       = 6        # Lấy tối đa 6 BLV mỗi trận

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
    return f"tieulam-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

def get_final_logo(team_name: str, site_logo: str) -> str:
    if site_logo and site_logo.startswith("http"): return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

def format_xoilac_time(time_str: str) -> str:
    try:
        clean = time_str.replace(" - ", " ").replace(".", "/").strip()
        if re.search(r'\d{1,2}:\d{2}\s+\d{1,2}/\d{1,2}$', clean):
            clean += f"/{datetime.datetime.now(VN_TZ).year}"
        return clean
    except:
        return time_str or "Sắp diễn ra"

# =========================================================
# JS: TRÍCH XUẤT DANH SÁCH TRẬN ĐẤU TỪ TRANG CHỦ XOILAC
# =========================================================
JS_EXTRACT = """
() => {
    const results = [];
    const seen = new Set();
    const clean = t => (t || '').replace(/\\s+/g, ' ').trim();

    const matchCards = document.querySelectorAll('[class*="item-match"], [class*="match-football-item"]');

    matchCards.forEach(card => {
        const anchor = card.querySelector('a[href*="/truc-tiep/"]');
        if (!anchor) return;
        
        const href = anchor.href;
        if (seen.has(href)) return;
        seen.add(href);

        let home = '', away = '';
        const homeEl = card.querySelector('[class*="team--home-name"], [class*="home-team"], [class*="team-home"] [class*="name"]');
        const awayEl = card.querySelector('[class*="team--away-name"], [class*="away-team"], [class*="team-away"] [class*="name"]');
        if (homeEl) home = clean(homeEl.innerText);
        if (awayEl) away = clean(awayEl.innerText);

        if (!home || !away) {
            const title = anchor.getAttribute('title') || '';
            const m = title.split(/\\s+vs\\s+/i);
            if (m.length >= 2) {
                home = clean(m[0]);
                away = clean(m[1].split(/\\s+lúc\\s+/i)[0]);
            }
        }

        let homeLogo = '', awayLogo = '';
        const imgs = card.querySelectorAll('img');
        if (imgs.length >= 2) {
            homeLogo = imgs[0].src;
            awayLogo = imgs[1].src;
        }

        const leagueEl = card.querySelector('[class*="league"], span[data-attr]');
        const tournament = leagueEl ? clean(leagueEl.innerText) : 'Bóng Đá';

        const timeEl = card.querySelector('[class*="date"], [class*="time"]');
        const timeStr = timeEl ? clean(timeEl.innerText) : '';

        const statusEl = card.querySelector('[class*="status"], [class*="score"], .live-btn');
        const statusText = statusEl ? clean(statusEl.innerText).toLowerCase() : '';
        const isLive = statusText.includes('hiệp') || statusText.includes('ht') || statusText.includes('live') || /\\d+\\s*[:\\-]\\s*\\d+/.test(statusText);

        if (home && away && home !== away) {
            results.push({ href, home, away, timeStr, homeLogo, awayLogo, tournament, isLive, statusText });
        }
    });

    return results;
}
"""

# =========================================================
# CAPTURE STREAM: SĂN BLV (FLV/M3U8)
# =========================================================
def capture_xoilac_streams(context, match_url: str, global_seen_streams: set) -> list:
    page = context.new_page()
    apply_stealth(page) # 💡 Đã dùng bộ giáp mới
    
    current_captured = []
    seen_urls = set()
    BAD = [".gif", ".png", ".jpg", ".mp4", "/ad/", "/ads/", "quangcao", "banner", "tvc", "google", "facebook", "segment"]

    def process_url(url):
        u = url.lower()
        if (".flv" in u or ".m3u8" in u) and not any(b in u for b in BAD):
            if url not in seen_urls and url not in global_seen_streams:
                seen_urls.add(url)
                current_captured.append(url)

    page.on("request",  lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    streams_dict = {}

    try:
        print("      > Đang vào trang xem để dò BLV...")
        page.goto(match_url, wait_until="domcontentloaded", timeout=25000)
        
        deadline = time.time() + 4
        while time.time() < deadline:
            if current_captured: break
            time.sleep(0.5)

        if current_captured:
            streams_dict["⭐ BLV Mặc Định"] = current_captured[-1]
            global_seen_streams.add(current_captured[-1])

        blv_buttons = page.evaluate('''() => {
            let btns = [];
            document.querySelectorAll('button, a, span, div[class*="server"] > div, div[class*="stream"] > div').forEach((el, idx) => {
                let txt = el.innerText.trim();
                if (txt && txt.length >= 2 && txt.length <= 18 && !txt.includes('\\n') && 
                    !txt.toLowerCase().includes('telegram') && !txt.toLowerCase().includes('facebook') && 
                    !txt.toLowerCase().includes('tất cả') && !txt.toLowerCase().includes('đóng')) {
                    
                    if (/^(HD|SD|▶|\|\||VIP|SERVER|KÊNH|BLV)\b/i.test(txt) || /^[A-Z0-9\\s]{3,12}$/.test(txt)) {
                        el.setAttribute('data-bot-btn', `blv-${idx}`);
                        btns.push({ text: txt, selector: `[data-bot-btn="blv-${idx}"]` });
                    }
                }
            });
            return btns;
        }''')

        hd_btns = [b for b in blv_buttons if "HD" in b['text'].upper()]
        other_btns = [b for b in blv_buttons if "HD" not in b['text'].upper()]
        sorted_btns = (hd_btns + other_btns)[:MAX_BLV]

        for btn in sorted_btns:
            raw_name = btn['text']
            clean_name = re.sub(r'^[▶\|\|\s]+', '', raw_name).strip()
            if not clean_name.upper().startswith("BLV"):
                clean_name = f"🎙️ {clean_name}"

            if clean_name in streams_dict: continue

            print(f"      > Đang chuyển sang: {clean_name}...")
            current_captured.clear()
            try:
                page.click(btn['selector'], timeout=3000)
                time.sleep(3.0)
                
                if current_captured:
                    latest_url = current_captured[-1]
                    if latest_url not in global_seen_streams:
                        streams_dict[clean_name] = latest_url
                        global_seen_streams.add(latest_url)
            except:
                pass

    except Exception as e:
        pass
    finally:
        page.close()

    results = []
    for name, url in streams_dict.items():
        results.append({"name": name, "url": url})
    return results

# =========================================================
# XÂY DỰNG CẤU TRÚC JSON CHUẨN SÁNG TV
# =========================================================
def build_channel(m: dict, stream_data: list) -> dict:
    home = m.get("home", "Unknown").title()
    away = m.get("away", "Unknown").title()
    thoi_gian = format_xoilac_time(m.get("timeStr", ""))
    
    cid = make_id(m["href"])
    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}" + (f" | {m.get('tournament')}" if m.get('tournament') else "") + f" | {thoi_gian}"

    is_live = len(stream_data) > 0
    label_text = "● Live" if is_live else ("🔴 Chờ stream" if m.get("isLive") else "⏳ Chưa live")
    label_color = "#ff0000" if is_live else ("#ff6600" if m.get("isLive") else "#d54f1a")

    stream_links = []
    for idx, s in enumerate(stream_data):
        u = s["url"]
        stream_links.append({
            "id": make_link_id(),
            "name": s["name"],
            "type": "flv" if ".flv" in u.lower() else "hls",
            "default": idx == 0,
            "url": u
        })

    return {
        "id": cid, "name": display_name, 
        "tournament": m.get("tournament", ""),
        "logo_nha": get_final_logo(home, m.get("homeLogo")), 
        "logo_khach": get_final_logo(away, m.get("awayLogo")),
        "type": "single", "display": "thumbnail-only", "enable_detail": False,
        "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": get_final_logo(home, m.get("homeLogo")), "width": 1600, "height": 1200},
        "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
        "sources": [{
            "id": cid, "name": "Tiếu Lâm (Xoilac)",
            "contents": [{
                "id": cid, "name": title_clean,
                "streams": [{"id": cid, "name": "F", "stream_links": stream_links}]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push():
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT XOILAC -> TIẾU LÂM (Giờ VN): {now_str}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=_HEADERS["User-Agent"], timezone_id="Asia/Ho_Chi_Minh")
        page = context.new_page()
        apply_stealth(page) # 💡 Đã dùng bộ giáp mới

        try:
            print(f"📺 Đang tải trang chủ {TARGET_SITE}...")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(5000)
        except Exception as e:
            print(f"⚠️ Lỗi tải trang chủ: {e}")

        raw_matches = page.evaluate(JS_EXTRACT)
        valid_matches = []
        seen_keys = set()

        for m in raw_matches:
            h = m["home"].lower()
            a = m["away"].lower()
            if not h or not a or "unknown" in h or "xoilac" in h: continue
            
            key = f"{h} vs {a}"
            if key not in seen_keys:
                seen_keys.add(key)
                valid_matches.append(m)

        raw_matches = valid_matches[:LIMIT_MATCHES]
        print(f"\n🎯 LỌC ĐƯỢC {len(raw_matches)} TRẬN ĐẤU HOT NHẤT...")

        global_seen_streams = set()
        channels = []

        for idx, m in enumerate(raw_matches, 1):
            print(f"\n[{idx}/{len(raw_matches)}] {m['home']} vs {m['away']} ({m['timeStr']})")
            streams = []
            if m.get("isLive") or "00:" in m.get("timeStr", "") or "lúc" in m["href"]:
                streams = capture_xoilac_streams(context, m["href"], global_seen_streams)
            
            channels.append(build_channel(m, streams))

    if GITHUB_TOKEN:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(REPO_NAME)
        content = json.dumps({
            "id": "tieulam", "name": "Tiếu Lâm TV (Xoilac)", "last_updated": now_str, 
            "groups": [{"id": "live", "name": "🔴 Live bóng đá Xoilac", "channels": channels}]
        }, indent=2, ensure_ascii=False)
        
        msg = f"⚽ Update Xoilac (Tiếu Lâm): {now_str}"
        try:
            existing = repo.get_contents(FILE_PATH)
            repo.update_file(existing.path, msg, content, existing.sha)
            print("\n✅ ĐÃ GHI ĐÈ THÀNH CÔNG VÀO TIEULAM.JSON TRÊN GITHUB!")
        except:
            repo.create_file(FILE_PATH, msg, content)
            print("\n✅ ĐÃ TẠO MỚI TIEULAM.JSON TRÊN GITHUB!")
    else:
        print("\n⚠️ Không tìm thấy GITHUB_TOKEN, chỉ in ra console chứ không push!")

if __name__ == "__main__":
    scrape_and_push()
