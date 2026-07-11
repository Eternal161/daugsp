import os
import re
import time
import json
import uuid
import html
import hashlib
import datetime
from urllib.parse import urljoin
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

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
# CONFIG CÀ KHỊA TV - CÀO HTTP-FLV + GỘP TRẬN/BLV
# =========================================================
TARGET_SITE = "https://cakhia17.site/"
BASE_URL = "https://cakhia17.site"
FILE_PATH = "cakhia.json"
LIMIT_MATCHES = 15

# Số giây chờ ở trang chi tiết để FLV xuất hiện trong Network.
STREAM_WAIT_MS = 12_000

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/daucakhia")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Chỉ nhận URL HTTP-FLV thật, có hoặc không có query expire/sign.
FLV_URL_RE = re.compile(
    r"https?://[^\"'\s<>]+?\.flv(?:\?[^\"'\s<>]*)?",
    re.IGNORECASE,
)


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


def get_final_logo(team_name: str, site_logo: str) -> str:
    if site_logo:
        site_logo = urljoin(BASE_URL + "/", site_logo)
        if site_logo.startswith("http"):
            return site_logo

    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return (
        "https://ui-avatars.com/api/"
        f"?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"
    )


def normalize_flv_url(raw_url: str) -> str:
    r"""Giải mã URL lấy từ HTML/JSON có \/, \u0026 hoặc &amp;."""
    url = html.unescape(raw_url or "")
    url = url.replace("\\/", "/")
    url = url.replace("\\u0026", "&").replace("\\u0026", "&")
    url = url.strip(" \'\"),];")
    return url


def extract_flv_urls(text: str) -> set[str]:
    if not text:
        return set()

    # Chuẩn hóa toàn bộ chuỗi trước để cả URL thường và URL JSON dạng https:\/\/ đều khớp.
    normalized_text = html.unescape(text)
    normalized_text = normalized_text.replace("\\/", "/")
    normalized_text = normalized_text.replace("\\u0026", "&")

    found: set[str] = set()
    for match in FLV_URL_RE.findall(normalized_text):
        url = normalize_flv_url(match)
        if ".flv" not in url.lower():
            continue
        if any(bad in url.lower() for bad in ("/ad/", "ads.", "banner")):
            continue
        found.add(url)
    return found


def score_flv_url(url: str) -> int:
    low = url.lower()
    score = 0
    if "flv.cdnfaster-b.site" in low:
        score += 10_000
    if "/live/" in low:
        score += 2_000
    if "expire=" in low:
        score += 500
    if "sign=" in low:
        score += 500
    if low.startswith("https://"):
        score += 100
    return score


# =========================================================
# JS: LẤY TÊN ĐỘI, LOGO, GIẢI, GIỜ, TỶ SỐ, BLV
# Dựa theo DOM Cakhia: a[href*="/truc-tiep/"] và hai cột w-4/12.
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
        if (seen.has(href)) continue;
        seen.add(href);

        // Khối card lớn bao quanh trận. Selector đầu tiên bám theo class thấy trên trang.
        const card =
            a.closest('div[class*="rounded-lg"][class*="border"]') ||
            a.parentElement?.parentElement ||
            a.parentElement ||
            a;

        // Hai cột đội có class w-4/12, bên trong có img và p.
        let teamCols = Array.from(a.querySelectorAll('div')).filter(el => {
            const cls = String(el.className || '');
            return cls.includes('w-4/12') && el.querySelector('img') && el.querySelector('p');
        });

        // Fallback nếu site đổi nhẹ class.
        if (teamCols.length < 2) {
            teamCols = Array.from(a.querySelectorAll('div')).filter(el => {
                const p = el.querySelector(':scope > p, :scope > span + p');
                const img = el.querySelector('img');
                return p && img && getText(p).length > 1;
            });
        }

        let home = '';
        let away = '';
        let homeLogo = '';
        let awayLogo = '';

        if (teamCols.length >= 2) {
            const homeCol = teamCols[0];
            const awayCol = teamCols[teamCols.length - 1];
            home = getText(homeCol.querySelector('p'));
            away = getText(awayCol.querySelector('p'));
            homeLogo = getSrc(homeCol.querySelector('img'));
            awayLogo = getSrc(awayCol.querySelector('img'));
        }

        // Fallback theo các thẻ p tên đội giống ảnh DevTools.
        if (!home || !away) {
            const teamNames = Array.from(a.querySelectorAll('p'))
                .map(getText)
                .filter(t => t && t.length <= 80)
                .filter(t => !/^\d+\s*[:\-]\s*\d+$/.test(t))
                .filter(t => !/^(hiệp|nghỉ|live|ft|ht|vs)/i.test(t));
            if (teamNames.length >= 2) {
                home = home || teamNames[0];
                away = away || teamNames[teamNames.length - 1];
            }
        }

        const aText = getText(a);
        const cardText = getText(card);
        const cardLines = (card.innerText || '')
            .split('\n')
            .map(clean)
            .filter(Boolean);

        const scoreMatch = aText.match(/\b(\d{1,2})\s*[:\-]\s*(\d{1,2})\b/);
        const scoreStr = scoreMatch ? `${scoreMatch[1]}:${scoreMatch[2]}` : '';

        const timeMatch = cardText.match(/\b(\d{1,2}:\d{2})\s+(\d{1,2}\/\d{1,2})(?:\/\d{2,4})?\b/);
        const timeStr = timeMatch ? `${timeMatch[1]} ${timeMatch[2]}` : '';

        let status = '';
        const statusLine = cardLines.find(t =>
            /^(hiệp|nghỉ giữa hiệp|sắp diễn ra|chưa bắt đầu|đang diễn ra|live|ft|ht)/i.test(t)
        );
        if (statusLine) status = statusLine;

        const aRect = a.getBoundingClientRect();
        const smallRounded = Array.from(card.querySelectorAll('div,span,p')).filter(el => {
            const cls = String(el.className || '');
            const text = getText(el);
            if (!text || text.length > 80) return false;
            return cls.includes('rounded-full') || cls.includes('truncate');
        });

        const isJunk = text => {
            if (!text) return true;
            if (text === home || text === away || text === scoreStr || text === timeStr || text === status) return true;
            if (/^\d{1,2}:\d{2}(\s+\d{1,2}\/\d{1,2})?$/.test(text)) return true;
            if (/^\d+\s*[:\-]\s*\d+$/.test(text)) return true;
            if (/^(ht|ft|vs|live)$/i.test(text)) return true;
            return false;
        };

        // Badge giải thường nằm phía trên hàng tên đội.
        let tournament = '';
        const leagueEl = smallRounded.find(el => {
            const rect = el.getBoundingClientRect();
            const text = getText(el);
            return !isJunk(text) && rect.top <= aRect.top + 35;
        });
        if (leagueEl) tournament = getText(leagueEl);

        // Fallback chọn dòng ngắn đầu tiên không phải tên đội/tỷ số/thời gian.
        if (!tournament) {
            tournament = cardLines.find(t =>
                !isJunk(t) &&
                t.length >= 3 && t.length <= 60 &&
                !/bình luận|blv/i.test(t)
            ) || '';
        }

        // Badge BLV thường nằm ngay dưới anchor trận.
        let blvName = '';
        const blvEl = smallRounded.find(el => {
            const rect = el.getBoundingClientRect();
            const text = getText(el);
            return !isJunk(text) && text !== tournament && rect.top >= aRect.bottom - 8;
        });
        if (blvEl) blvName = getText(blvEl);

        if (!blvName) {
            blvName = cardLines.find(t => /\b(BLV|bình luận viên)\b/i.test(t)) || 'Cà Khịa';
        }

        const lower = cardText.toLowerCase();
        const isLiveUI = Boolean(scoreStr) || /hiệp|nghỉ giữa hiệp|đang diễn ra|\blive\b/.test(lower);

        // Chỉ nhận card có đủ hai đội.
        if (home && away && home !== away) {
            results.push({
                href,
                home,
                away,
                homeLogo,
                awayLogo,
                tournament,
                timeStr,
                scoreStr,
                status,
                isLiveUI,
                blvName,
            });
        }
    }

    return results;
}
"""


# =========================================================
# CAPTURE HTTP-FLV
# =========================================================
def capture_stream(context, match_url: str) -> list[str]:
    page = context.new_page()
    try:
        Stealth().apply_stealth_sync(page)
    except Exception:
        pass

    streams: set[str] = set()

    def add_url(url: str) -> None:
        if not url:
            return
        if ".flv" in url.lower():
            streams.update(extract_flv_urls(url))

    def on_request(request) -> None:
        try:
            add_url(request.url)
        except Exception:
            pass

    def on_response(response) -> None:
        try:
            add_url(response.url)

            # Một số trang trả URL FLV trong API JSON thay vì request video ngay.
            content_type = (response.headers.get("content-type") or "").lower()
            if any(t in content_type for t in ("json", "javascript", "text/plain")):
                length_text = response.headers.get("content-length") or "0"
                try:
                    content_length = int(length_text)
                except ValueError:
                    content_length = 0

                if content_length == 0 or content_length <= 2_000_000:
                    body = response.text()
                    streams.update(extract_flv_urls(body))
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        page.goto(match_url, wait_until="domcontentloaded", timeout=60_000)

        # Cho player tự chạy. Nếu có video HTML5 thì gọi play() để kích hoạt request.
        page.wait_for_timeout(2_000)
        try:
            page.evaluate(
                """
                () => {
                    document.querySelectorAll('video').forEach(v => {
                        v.muted = true;
                        const p = v.play();
                        if (p && p.catch) p.catch(() => {});
                    });
                }
                """
            )
        except Exception:
            pass

        # Thử bấm các nút play/overlay phổ biến nếu player chưa tự phát.
        click_selectors = [
            'button[aria-label*="play" i]',
            'button[title*="play" i]',
            '.art-video-player .art-layer-play',
            '.vjs-big-play-button',
            '[class*="play-button" i]',
        ]
        for selector in click_selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible():
                    locator.click(timeout=1_500, force=True)
                    break
            except Exception:
                pass

        page.wait_for_timeout(STREAM_WAIT_MS)

        # Fallback 1: performance entries.
        try:
            resource_urls = page.evaluate(
                "performance.getEntriesByType('resource').map(e => e.name)"
            )
            for url in resource_urls or []:
                add_url(url)
        except Exception:
            pass

        # Fallback 2: video/source src, HTML và script inline.
        try:
            media_urls = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('video,source'))
                    .map(el => el.currentSrc || el.src || el.getAttribute('src') || '')
                    .filter(Boolean)
                """
            )
            for url in media_urls or []:
                add_url(url)
        except Exception:
            pass

        try:
            streams.update(extract_flv_urls(page.content()))
        except Exception:
            pass

    except PWTimeout:
        print("         ⚠️ Trang chi tiết load chậm, vẫn dùng link đã bắt được.")
    except Exception as exc:
        print(f"         ⚠️ Không mở được trang chi tiết: {exc}")
    finally:
        page.close()

    ranked = sorted(streams, key=score_flv_url, reverse=True)
    return ranked


# =========================================================
# BUILD CHANNEL THEO JSON SÁNGTV
# =========================================================
def build_channel(match: dict, stream_data: list[dict]) -> dict:
    home = clean_text(match.get("home") or "Unknown")
    away = clean_text(match.get("away") or "Unknown")
    time_str = clean_text(match.get("timeStr") or "Không rõ")
    tournament = clean_text(match.get("tournament") or "")
    score_str = clean_text(match.get("scoreStr") or "")
    status = clean_text(match.get("status") or "")

    title_clean = f"{home} vs {away}"
    display_name = f"⚽ {title_clean}"
    if tournament:
        display_name += f" | {tournament}"
    display_name += f" | {time_str}"

    cid = make_id(match["href"])
    has_stream = bool(stream_data)

    if has_stream:
        label_text = f"● Live {score_str}" if score_str else "● Live"
        label_color = "#ff0000"
    elif match.get("isLiveUI"):
        label_text = status or "🔴 Chờ stream"
        label_color = "#ff6600"
    else:
        label_text = "⏳ Chưa live"
        label_color = "#d54f1a"

    stream_links = []
    seen_urls = set()
    for item in stream_data:
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        stream_links.append(
            {
                "id": make_link_id(),
                "name": item.get("name") or "Cà Khịa",
                "type": "flv",
                "default": len(stream_links) == 0,
                "url": url,
            }
        )

    home_logo = get_final_logo(home, match.get("homeLogo", ""))
    away_logo = get_final_logo(away, match.get("awayLogo", ""))

    return {
        "id": cid,
        "name": display_name,
        "tournament": tournament,
        "logo_nha": home_logo,
        "logo_khach": away_logo,
        "type": "single",
        "display": "thumbnail-only",
        "enable_detail": False,
        "image": {
            "padding": 1,
            "background_color": "#ececec",
            "display": "contain",
            "url": home_logo,
            "width": 1600,
            "height": 1200,
        },
        "labels": [
            {
                "text": label_text,
                "position": "top-left",
                "color": "#00ffffff",
                "text_color": label_color,
            }
        ],
        "sources": [
            {
                "id": cid,
                "name": "Cà Khịa",
                "contents": [
                    {
                        "id": cid,
                        "name": title_clean,
                        "streams": [
                            {
                                "id": cid,
                                "name": "F",
                                "stream_links": stream_links,
                            }
                        ],
                    }
                ],
            }
        ],
    }


# =========================================================
# GITHUB
# =========================================================
def push_json(channels: list[dict], now_str: str) -> None:
    if not GITHUB_TOKEN:
        raise RuntimeError("Thiếu secret GH_TOKEN.")

    payload = {
        "id": "cakhia",
        "name": "Cà Khịa TV",
        "last_updated": now_str,
        "groups": [
            {
                "id": "live",
                "name": "🔴 Live bóng đá",
                "channels": channels,
            }
        ],
    }
    content = json.dumps(payload, indent=2, ensure_ascii=False)

    github = Github(GITHUB_TOKEN)
    repo = github.get_repo(REPO_NAME)
    message = f"⚽ Update Cà Khịa FLV (VN Time): {now_str}"

    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, message, content, existing.sha)
    except Exception:
        repo.create_file(FILE_PATH, message, content)


# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push() -> None:
    now_vn = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT CÀ KHỊA FLV (Giờ VN): {now_str}")

    channels: list[dict] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_HEADERS["User-Agent"],
            timezone_id="Asia/Ho_Chi_Minh",
            locale="vi-VN",
            extra_http_headers={
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        page = context.new_page()
        try:
            Stealth().apply_stealth_sync(page)
        except Exception:
            pass

        try:
            print("📺 Đang mở trang Cà Khịa...")
            page.goto(TARGET_SITE, wait_until="domcontentloaded", timeout=60_000)
        except PWTimeout:
            print("   ⚠️ Trang chủ load chậm, tiếp tục đọc DOM đã tải.")
        except Exception as exc:
            print(f"   ❌ Không mở được trang chủ: {exc}")

        # Cuộn để các card lazy-load và ảnh logo có currentSrc đầy đủ.
        for _ in range(4):
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(800)
            except Exception:
                break
        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        page.wait_for_timeout(2_000)

        try:
            raw_matches = page.evaluate(JS_EXTRACT)
        except Exception as exc:
            print(f"   ❌ Lỗi đọc danh sách trận: {exc}")
            raw_matches = []

        print(f"📋 Tìm thấy {len(raw_matches)} card trận hợp lệ.")

        if not raw_matches:
            try:
                page.screenshot(path="cakhia_debug.png", full_page=True)
                with open("cakhia_debug.html", "w", encoding="utf-8") as file:
                    file.write(page.content())
                print("   ⚠️ Đã lưu cakhia_debug.png và cakhia_debug.html để kiểm tra selector.")
            except Exception:
                pass
            browser.close()
            raise RuntimeError(
                "Không tìm thấy card /truc-tiep/. Dừng cập nhật để không ghi đè cakhia.json bằng dữ liệu rỗng."
            )

        # Gộp cùng một trận nhưng có nhiều BLV/link trang chi tiết.
        grouped_matches: dict[str, dict] = {}
        for match in raw_matches:
            home = clean_text(match.get("home", ""))
            away = clean_text(match.get("away", ""))
            if not home or not away or home.lower() == away.lower():
                continue

            key = f"{home.lower()}::{away.lower()}"
            blv_name = clean_text(match.get("blvName") or "Cà Khịa")

            if key not in grouped_matches:
                match["hrefs_and_blvs"] = [(match["href"], blv_name)]
                grouped_matches[key] = match
            else:
                pair = (match["href"], blv_name)
                if pair not in grouped_matches[key]["hrefs_and_blvs"]:
                    grouped_matches[key]["hrefs_and_blvs"].append(pair)

        # Live lên đầu, sau đó giữ thứ tự trang.
        valid_matches = list(grouped_matches.values())
        valid_matches.sort(key=lambda item: bool(item.get("isLiveUI")), reverse=True)
        valid_matches = valid_matches[:LIMIT_MATCHES]

        for index, match in enumerate(valid_matches, start=1):
            print(
                f"\n[{index}/{len(valid_matches)}] "
                f"{match['home']} vs {match['away']} "
                f"({match.get('timeStr') or 'Chưa rõ'})"
            )

            all_match_streams: list[dict] = []
            used_urls: set[str] = set()

            for href, blv_name in match["hrefs_and_blvs"]:
                print(f"      > Cào FLV: {blv_name} — {href}")
                streams = capture_stream(context, href)

                if not streams:
                    print("         ❌ Chưa bắt được URL .flv")
                    continue

                # Mỗi trang/BLV lấy URL được chấm điểm cao nhất.
                best_url = streams[0]
                if best_url in used_urls:
                    continue
                used_urls.add(best_url)
                all_match_streams.append({"name": blv_name, "url": best_url})
                print(f"         ✅ {best_url}")

            channels.append(build_channel(match, all_match_streams))

        browser.close()

    push_json(channels, now_str)
    print(f"\n✅ HOÀN TẤT: đã cập nhật {len(channels)} trận vào {REPO_NAME}/{FILE_PATH}")


if __name__ == "__main__":
    scrape_and_push()
