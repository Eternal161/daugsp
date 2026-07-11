import os
import re
import time
import json
import uuid
import html
import hashlib
import datetime
from urllib.parse import urljoin
from github import Github
from playwright.sync_api import sync_playwright

def apply_stealth(page):
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
    except Exception:
        pass

# =========================================================
# CONFIG CÀ KHỊA TV - CHIẾN THUẬT SPA ROUTING
# =========================================================
TARGET_SITE = "https://cakhia17.site/"
BASE_URL = "https://cakhia17.site"
FILE_PATH = "tieulam.json" # Lưu trùng tên với code Sáng TV của bạn
LIMIT_MATCHES = 15

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/daugsp")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# =========================================================
# HELPER
# =========================================================
def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"cakhia-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()

# =========================================================
# JS: EXTRACT DATA - BỎ QUA TRẬN TRÊN CÙNG
# =========================================================
JS_EXTRACT = r"""
() => {
    const results = [];
    const seen = new Set();
    const clean = value => (value || '').replace(/\s+/g, ' ').trim();
    const getText = el => clean(el ? (el.innerText || el.textContent || '') : '');
    const getSrc = img => img ? (img.currentSrc || img.src || img.getAttribute('src') || '') : '';

    const anchors = Array.from(document.querySelectorAll('a[href*="/truc-tiep/"]'));

    for (const a of anchors) {
        const href = new URL(a.getAttribute('href'), location.origin).href;
        
        // 💡 BỎ QUA TRẬN TỰ PHÁT TRÊN ĐẦU TRANG: Trận thật luôn nằm trong grid chứa 2 đội
        const gridBox = a.querySelector('div[class*="grid-cols-[1fr_auto_1fr]"]');
        if (!gridBox || gridBox.children.length < 3) continue;

        if (seen.has(href)) continue;
        seen.add(href);

        const card = a.closest('div[class*="rounded-lg"][class*="border"]') || a.parentElement || a;

        let home = '', away = '', homeLogo = '', awayLogo = '';
        const homeSpan = gridBox.children[0].querySelector('span.truncate, p');
        const awaySpan = gridBox.children[2].querySelector('span.truncate, p');
        if (homeSpan) home = getText(homeSpan);
        if (awaySpan) away = getText(awaySpan);
        
        const imgNha = gridBox.children[0].querySelector('img');
        const imgKhach = gridBox.children[2].querySelector('img');
        if (imgNha) homeLogo = getSrc(imgNha);
        if (imgKhach) awayLogo = getSrc(imgKhach);

        const aText = getText(a);
        const cardText = getText(card);
        const cardLines = (card.innerText || '').split('\n').map(clean).filter(Boolean);

        const scoreMatch = aText.match(/\b(\d{1,2})\s*[:\-]\s*(\d{1,2})\b/);
        const scoreStr = scoreMatch ? `${scoreMatch[1]}:${scoreMatch[2]}` : '';

        const timeMatch = cardText.match(/\b(\d{1,2}:\d{2})\s+(\d{1,2}\/\d{1,2})(?:\/\d{2,4})?\b/);
        const timeStr = timeMatch ? `${timeMatch[1]} ${timeMatch[2]}` : '';

        let status = '';
        const statusLine = cardLines.find(t => /^(hiệp|nghỉ giữa hiệp|sắp diễn ra|chưa bắt đầu|đang diễn ra|live|ft|ht)/i.test(t));
        if (statusLine) status = statusLine;

        // Bóc giải đấu (Thường là thẻ text nhỏ trên cùng của card)
        let tournament = '';
        const smallTexts = Array.from(card.querySelectorAll('div,span,p')).map(getText).filter(t => t.length > 2 && t.length < 50);
        for(const t of smallTexts) {
            if (t !== home && t !== away && !t.includes(':') && !t.toLowerCase().includes('blv')) {
                tournament = t; break;
            }
        }

        // Bóc BLV
        let blvName = cardLines.find(t => /\b(BLV|bình luận viên)\b/i.test(t)) || 'Cà Khịa';

        const lower = cardText.toLowerCase();
        const isLiveUI = Boolean(scoreStr) || /hiệp|nghỉ giữa hiệp|đang diễn ra|\blive\b/.test(lower);

        if (home && away && home !== away) {
            results.push({ href, home, away, homeLogo, awayLogo, tournament, timeStr, scoreStr, status, isLiveUI, blvName });
        }
    }
    return results;
}
"""

def lay_flv_spa(page, url_path):
    link_stream = ""
    
    # 💡 LÀM SẠCH KHO CHỨA LINK TRƯỚC MỖI TRẬN
    page.evaluate("window.__botFlvLinks = []; window.__botApis = [];")
    
    def handle_response(response):
        nonlocal link_stream
        if link_stream: return
        try:
            req_url = response.url.lower()
            # Bắt link flv thô rớt ra mạng
            if ".flv" in req_url and "expire=" in req_url and "quangcao" not in req_url:
                link_stream = response.url
        except: pass

    page.on("response", handle_response)
    
    try:
        # 💡 CLICK ẢO ĐỂ LÁCH 404 (Không mở tab mới)
        page.evaluate(f'''([path]) => {{
            let link = document.querySelector(`a[href="${{path}}"]`) || document.querySelector(`a[href*="${{path.split('/').pop()}}"]`);
            if (link) link.click();
            else window.location.href = path;
        }}''', [url_path])
        
        # CHỜ VÀ ÉP LẤY LINK FLV
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if link_stream: 
                print(f"      🎯 [Network] Tóm được FLV: {link_stream[:60]}...")
                break
            
            # Lôi kho tàng JS Hack ra kiểm tra
            bot_links = page.evaluate("window.__botFlvLinks || []")
            if bot_links and len(bot_links) > 0:
                link_stream = bot_links[-1]
                print(f"      🎯 [JS Inject] Trộm được FLV từ API: {link_stream[:60]}...")
                break
                
            time.sleep(0.5)
            
    except Exception as e:
        print(f"      ⚠️ Lỗi chuyển trang: {e}")
    finally:
        try: page.remove_listener("response", handle_response)
        except: pass
        
    return link_stream

def scrape_and_push() -> None:
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT CÀ KHỊA (Giờ VN): {now_str}")

    channels = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            timezone_id="Asia/Ho_Chi_Minh",
        )
        page = context.new_page()
        apply_stealth(page)

        # 💡 TIÊM MÃ ĐỘC VÀO LÕI TRÌNH DUYỆT ĐỂ BẮT MỌI GÓI API CHỨA ĐUÔI .FLV
        js_interceptor = r"""
        window.__botFlvLinks = [];
        const origFetch = window.fetch;
        window.fetch = async function(...args) {
            const response = await origFetch.apply(this, args);
            try { 
                response.clone().text().then(text => {
                    const clean = text.replace(/\\\//g, '/');
                    // Bắt trọn ổ link FLV có chứa query expire & sign
                    const flvMatch = clean.match(/https?:\/\/[^"'\s<>]+?\.flv(?:\?[^"'\s<>]*)?/i);
                    if (flvMatch && !flvMatch[0].includes('quangcao')) {
                        window.__botFlvLinks.push(flvMatch[0]);
                    }
                }).catch(()=>({})); 
            } catch(e) {}
            return response;
        };
        """
        page.add_init_script(js_interceptor)

        try:
            print(f"📺 Đang mở trang Cà Khịa: {TARGET_SITE}")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"   ❌ Lỗi mở trang: {exc}")

        # Cuộn trang nhẹ nhàng để tải các trận bên dưới
        try:
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(1000)
            page.evaluate("window.scrollTo(0, 0)")
        except: pass

        raw_matches = page.evaluate(JS_EXTRACT)
        print(f"📋 Tìm thấy {len(raw_matches)} trận đấu thật (đã bỏ qua bẫy Auto-play).")

        if not raw_matches:
            browser.close()
            return

        # Gộp trận
        grouped_matches = {}
        for match in raw_matches:
            home = clean_text(match.get("home", ""))
            away = clean_text(match.get("away", ""))
            if not home or not away: continue

            key = f"{home.lower()}::{away.lower()}"
            blv_name = clean_text(match.get("blvName") or "Cà Khịa")

            if key not in grouped_matches:
                match["hrefs_and_blvs"] = [(match["href"], blv_name)]
                grouped_matches[key] = match
            else:
                pair = (match["href"], blv_name)
                if pair not in grouped_matches[key]["hrefs_and_blvs"]:
                    grouped_matches[key]["hrefs_and_blvs"].append(pair)

        valid_matches = list(grouped_matches.values())
        valid_matches.sort(key=lambda item: bool(item.get("isLiveUI")), reverse=True)
        valid_matches = valid_matches[:LIMIT_MATCHES]

        print(f"✂️ Đã áp dụng Limit! Bắt đầu cào {len(valid_matches)} trận...\n")

        for index, match in enumerate(valid_matches, start=1):
            print(f"⏳ [{index}/{len(valid_matches)}] {match['home']} vs {match['away']} ({match.get('timeStr')})")

            all_match_streams = []
            
            for href, blv_name in match["hrefs_and_blvs"]:
                url_path = href.replace(TARGET_URL, "") if href.startswith(TARGET_URL) else href
                
                # An toàn: Đứng ở trang chủ trước khi click
                if TARGET_URL not in page.url or len(page.url) > len(TARGET_URL) + 5:
                    page.goto(TARGET_URL, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                
                print(f"      > Đang Click ảo vào BLV: {blv_name}...")
                flv_link = lay_flv_spa(page, url_path)
                
                if flv_link:
                    all_match_streams.append({"name": blv_name, "url": flv_link})
                else:
                    print("         ❌ Lướt qua không thấy link.")
                    
                # Cào xong lùi lại trang chủ
                try: 
                    page.evaluate("window.history.back()")
                    page.wait_for_timeout(1000)
                except: pass

            # ================= BUILD JSON =================
            title_clean = f"{match['home']} vs {match['away']}"
            display_name = f"⚽ {title_clean} | {match.get('tournament', '')} | {match.get('timeStr', '')}"
            cid = make_id(match["href"])
            has_stream = len(all_match_streams) > 0
            
            if has_stream:
                label_text = f"● Live {match.get('scoreStr','')}".strip()
                label_color = "#ff0000"
            elif match.get("isLiveUI"):
                label_text = match.get("status") or "🔴 Chờ stream"
                label_color = "#ff6600"
            else:
                label_text = "⏳ Chưa live"
                label_color = "#d54f1a"

            stream_links = []
            for i, s in enumerate(all_match_streams):
                stream_links.append({
                    "id": make_link_id(),
                    "name": s["name"],
                    "type": "flv",
                    "default": i == 0,
                    "url": s["url"],
                })

            home_logo = get_final_logo(match['home'], match.get("homeLogo", ""))
            away_logo = get_final_logo(match['away'], match.get("awayLogo", ""))

            channels.append({
                "id": cid, "name": display_name, "tournament": match.get("tournament", ""),
                "logo_nha": home_logo, "logo_khach": away_logo,
                "type": "single", "display": "thumbnail-only", "enable_detail": False,
                "image": {"padding": 1, "background_color": "#ececec", "display": "contain", "url": home_logo, "width": 1600, "height": 1200},
                "labels": [{"text": label_text, "position": "top-left", "color": "#00ffffff", "text_color": label_color}],
                "sources": [{
                    "id": cid, "name": "Cà Khịa TV",
                    "contents": [{
                        "id": cid, "name": title_clean,
                        "streams": [{"id": cid, "name": "F", "stream_links": stream_links}]
                    }]
                }],
            })

        browser.close()

    # Đẩy lên GitHub
    if GITHUB_TOKEN:
        payload = {
            "id": "cakhia", "name": "Cà Khịa TV", "last_updated": now_str,
            "groups": [{"id": "live", "name": "🔴 Live bóng đá Cà Khịa", "channels": channels}],
        }
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        github = Github(GITHUB_TOKEN)
        repo = github.get_repo(REPO_NAME)
        message = f"⚽ Update Cà Khịa FLV (VN Time): {now_str}"
        try:
            existing = repo.get_contents(FILE_PATH)
            repo.update_file(existing.path, message, content, existing.sha)
        except:
            repo.create_file(FILE_PATH, message, content)
        print(f"\n✅ HOÀN TẤT: Đã đẩy {len(channels)} trận lên GitHub!")
    else:
        print("\n⚠️ Không có GH_TOKEN.")

if __name__ == "__main__":
    scrape_and_push()
