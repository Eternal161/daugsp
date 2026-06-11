import os
import re
import time
import json
import uuid
import hashlib
import datetime
import requests
from github import Github

# =========================================================
# CONFIG TIẾU LÂM TV - BẢN V6: CLOUDFLARE WORKER RELAY
# Không cần Playwright, không bị chặn IP nước ngoài
# =========================================================
FILE_PATH     = "tieulam.json"
LIMIT_MATCHES = 15

# Cloudflare Worker relay
WORKER_URL    = os.getenv("WORKER_URL", "https://tieulam-relay.daulam1601.workers.dev")
WORKER_SECRET = os.getenv("WORKER_SECRET", "tieulam_secret_2024")

# API endpoint (phát hiện từ DevTools)
API_MATCHES   = "https://api.tlap12062026.xyz/matches/graph"

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/daugsp")

# =========================================================
# HELPER
# =========================================================
def make_id(seed: str = "") -> str:
    h = hashlib.md5((seed or str(uuid.uuid4())).encode()).hexdigest()
    return f"kaytee-{h[:12]}"

def make_link_id() -> str:
    return "lnk-" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:10]

def get_final_logo(team_name: str, site_logo: str) -> str:
    if site_logo and site_logo.startswith("http"):
        return site_logo
    initials = requests.utils.quote(team_name[:2] if len(team_name) >= 2 else "FC")
    return f"https://ui-avatars.com/api/?name={initials}&size=200&background=1565C0&color=ffffff&bold=true"

# =========================================================
# CLOUDFLARE WORKER RELAY
# Worker forward POST request đến API, bypass IP block
# =========================================================
def relay_post(target_url: str, body: dict) -> dict:
    """Gọi API qua Cloudflare Worker relay (IP HKG/Singapore)"""
    import urllib.parse

    # Build URL thủ công để tránh double-encode
    worker_url = (
        f"{WORKER_URL}"
        f"?secret={urllib.parse.quote(WORKER_SECRET, safe='')}"
        f"&method=POST"
        f"&url={urllib.parse.quote(target_url, safe='')}"
    )

    body_str = json.dumps(body)

    resp = requests.post(
        worker_url,
        data=body_str,          # dùng data= thay vì json= để tránh double-serialize
        timeout=20,
        headers={"Content-Type": "application/json"}
    )
    resp.raise_for_status()

    # Worker debug mode trả về wrapper, extract response thật
    raw = resp.json()
    if "response" in raw:
        return json.loads(raw["response"])
    return raw

# =========================================================
# LẤY DANH SÁCH TRẬN LIVE TỪ API
# =========================================================
def get_live_matches() -> list:
    """Query API lấy tất cả trận FOOTBALL đang live"""
    # Lấy trận từ 3 giờ trước đến hiện tại (bắt trận đã bắt đầu)
    now_utc = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
    start_date = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000")

    body = {
        "limit": LIMIT_MATCHES,
        "page": 1,
        "order_asc": "start_date",
        "queries": [
            {"field": "is_live",    "type": "equal", "value": True},
            {"field": "desc",       "type": "equal", "value": "FOOTBALL"},
            {"field": "start_date", "type": "gte",   "value": start_date},
        ]
    }

    try:
        data = relay_post(API_MATCHES, body)
        matches = data.get("data", [])
        print(f"✅ API trả về {len(matches)} trận live")
        return matches
    except Exception as e:
        print(f"❌ Lỗi khi gọi API matches: {e}")
        return []

# =========================================================
# BUILD CHANNEL (giữ nguyên format JSON cũ)
# =========================================================
def build_channel(m: dict) -> dict:
    """
    Dữ liệu API trả về:
      m["title"]        = "Ninh Binh FC - The Cong Viettel"
      m["team_1"]       = "Ninh Binh FC"
      m["team_2"]       = "The Cong Viettel"
      m["team_1_score"] = 1
      m["team_2_score"] = 0
      m["league"]       = "Vietnam National Cup"
      m["source_live"]  = "https://station1-global.vsc100.com/live/..."
      m["team_1_logo"]  = "https://img.sports-data.online/..."
      m["team_2_logo"]  = "https://img.sports-data.online/..."
      m["blv"]          = "BLV TRANG NGÔ"
      m["start_date"]   = "2026-06-11 11:00:00"
      m["is_live"]      = True
    """
    home = (m.get("team_1") or "").strip()
    away = (m.get("team_2") or "").strip()

    # Fallback: parse từ title nếu không có team_1/team_2
    if not home or not away:
        title = m.get("title", "Unknown vs Unknown")
        parts = re.split(r" - | vs ", title, maxsplit=1)
        home = parts[0].strip() if len(parts) > 0 else "Đội nhà"
        away = parts[1].strip() if len(parts) > 1 else "Đội khách"

    score1   = m.get("team_1_score", 0) or 0
    score2   = m.get("team_2_score", 0) or 0
    league   = m.get("league", "Tiếu Lâm Live") or "Tiếu Lâm Live"
    blv      = m.get("blv", "") or ""
    is_live  = m.get("is_live", False)
    m3u8_url = m.get("source_live", "") or ""

    # Format thời gian hiển thị
    start_raw = m.get("start_date", "")
    try:
        dt = datetime.datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        dt_vn = dt.astimezone(VN_TZ)
        time_str = dt_vn.strftime("%H:%M %d/%m")
    except Exception:
        time_str = start_raw[:16] if start_raw else "Không rõ"

    # Score display
    if is_live:
        score_str = f"{score1}-{score2}"
        time_display = f"Live {score_str}"
    else:
        time_display = time_str

    title_clean  = f"{home} vs {away}"
    display_name = f"⚽ {title_clean} | {time_display}"
    if blv:
        display_name += f" | {blv}"

    cid = make_id(m.get("id") or title_clean)

    label_text  = "● Live" if is_live else "⏳ Sắp đá"
    label_color = "#ff0000" if is_live else "#d54f1a"

    home_logo = get_final_logo(home, m.get("team_1_logo", ""))
    away_logo = get_final_logo(away, m.get("team_2_logo", ""))

    stream_links = []
    if m3u8_url:
        stream_links = [{
            "id":      make_link_id(),
            "name":    "Link Trực Tiếp",
            "type":    "hls",
            "default": True,
            "url":     m3u8_url
        }]

    return {
        "id":             cid,
        "name":           display_name,
        "tournament":     league,
        "logo_nha":       home_logo,
        "logo_khach":     away_logo,
        "type":           "single",
        "display":        "thumbnail-only",
        "enable_detail":  False,
        "image": {
            "padding":          1,
            "background_color": "#ececec",
            "display":          "contain",
            "url":              home_logo,
            "width":            1600,
            "height":           1200
        },
        "labels": [{
            "text":       label_text,
            "position":   "top-left",
            "color":      "#00ffffff",
            "text_color": label_color
        }],
        "sources": [{
            "id":   cid,
            "name": "Tiếu Lâm",
            "contents": [{
                "id":   cid,
                "name": title_clean,
                "streams": [{
                    "id":           cid,
                    "name":         "F",
                    "stream_links": stream_links
                }]
            }]
        }],
    }

# =========================================================
# CHƯƠNG TRÌNH CHÍNH
# =========================================================
def scrape_and_push():
    now_vn  = datetime.datetime.now(VN_TZ)
    now_str = now_vn.strftime("%H:%M %d/%m/%Y")
    print(f"🚀 BẮT ĐẦU BOT TIẾU LÂM TV V6 (Giờ VN): {now_str}")
    print(f"🌐 Dùng Worker relay: {WORKER_URL}")

    # 1. Lấy danh sách trận live từ API
    print("\n📡 Đang gọi API lấy danh sách trận live...")
    matches = get_live_matches()

    if not matches:
        print("⚠️ Không có trận nào live hoặc API lỗi. Giữ nguyên file cũ.")
        return

    # 2. Build channels
    channels = []
    print(f"\n👉 Xử lý {len(matches)} trận:")
    for idx, m in enumerate(matches, 1):
        title   = m.get("title", "Unknown")
        m3u8    = m.get("source_live", "")
        score1  = m.get("team_1_score", 0)
        score2  = m.get("team_2_score", 0)
        has_m3u8 = "✅" if m3u8 else "❌"
        print(f"  [{idx}/{len(matches)}] {title} ({score1}-{score2}) M3U8:{has_m3u8}")
        channels.append(build_channel(m))

    # 3. Build JSON
    output = {
        "id":           "tieulam",
        "name":         "Tiếu Lâm TV",
        "last_updated": now_str,
        "groups": [{
            "id":       "live",
            "name":     "🔴 Live bóng đá",
            "channels": channels
        }]
    }
    content = json.dumps(output, indent=2, ensure_ascii=False)

    # 4. Push lên GitHub hoặc lưu local
    if not GITHUB_TOKEN:
        print("\n⚠️ Không có GITHUB_TOKEN. Lưu file local.")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ Đã lưu {FILE_PATH} ({len(channels)} trận)")
        return

    print(f"\n⏳ Đang tải lên GitHub {REPO_NAME}/{FILE_PATH}...")
    g    = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg  = f"⚽ Update Tiếu Lâm TV V6 (VN Time): {now_str}"

    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã CẬP NHẬT thành công ({len(channels)} trận)")
    except Exception:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã TẠO MỚI file {FILE_PATH} ({len(channels)} trận)")

if __name__ == "__main__":
    scrape_and_push()
