import os
import json
import logging
import shutil
import threading
import time
import requests
import yt_dlp
from pathlib import Path
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)
logger = logging.getLogger(__name__)

COBALT_API = "https://api.cobalt.tools/"
COOKIES_FILE = None
QUALITY_OPTIONS = [
    {"id": "mp3",  "label": "MP3 — Audio Only", "type": "audio"},
    {"id": "1080p","label": "1080p Video",       "type": "video", "height": 1080},
    {"id": "720p", "label": "720p Video",        "type": "video", "height": 720},
    {"id": "480p", "label": "480p Video",        "type": "video", "height": 480},
    {"id": "360p", "label": "360p Video",        "type": "video", "height": 360},
]
BOT_CHECK_INDICATORS = ("sign in to confirm", "not a bot")

# Detect ffmpeg location at startup
FFMPEG_LOCATION = shutil.which("ffmpeg")
if FFMPEG_LOCATION:
    print(f"[IzuTube] ffmpeg found at {FFMPEG_LOCATION} ✓")
else:
    print("[IzuTube] WARNING: ffmpeg not found in PATH — audio conversion and video merging will be unavailable")

def normalize_cookies_content(raw_content):
    if not raw_content:
        return None

    content = raw_content.strip()
    if not content:
        return None

    # Common env-var formatting issues (escaped newlines and CRLF)
    content = content.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")

    # Support JSON exports (array of cookies or object with `cookies` key)
    parsed = None
    if content.startswith("{") or content.startswith("["):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None

    if parsed is not None:
        cookies = []
        if isinstance(parsed, list):
            cookies = parsed
        elif isinstance(parsed, dict):
            if isinstance(parsed.get("cookies"), list):
                cookies = parsed["cookies"]
            elif all(key in parsed for key in ("domain", "name", "value")):
                cookies = [parsed]

        lines = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            domain = str(cookie.get("domain", "")).strip()
            name = str(cookie.get("name", "")).strip()
            # Preserve cookie value exactly as provided (whitespace can be significant).
            value = str(cookie.get("value", ""))
            if not domain or not name:
                continue
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            path = str(cookie.get("path", "/") or "/")
            secure = "TRUE" if cookie.get("secure") else "FALSE"
            expiry = cookie.get("expiry")
            if expiry is None:
                expiry = cookie.get("expires")
            if expiry is None:
                expiry = 0
            try:
                expiry = int(expiry)
            except (TypeError, ValueError):
                expiry = 0
            lines.append(
                f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expiry}\t{name}\t{value}"
            )

        if not lines:
            return None
        return "# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n"

    # Assume plain Netscape format (prepend header if missing)
    if not content.startswith("# Netscape HTTP Cookie File"):
        content = "# Netscape HTTP Cookie File\n" + content
    if not content.endswith("\n"):
        content += "\n"
    return content


# Write cookies from environment variable to a temp file at startup
cookies_content = normalize_cookies_content(os.environ.get("COOKIES_CONTENT", ""))
if cookies_content:
    COOKIES_FILE = "/tmp/yt_cookies.txt"
    fd = os.open(COOKIES_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(cookies_content)
    print("[IzuTube] Cookies loaded from environment variable ✓")
else:
    print("[IzuTube] No valid cookies found — running without authentication")


def get_ydl_opts(extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"skip": ["dash", "hls"], "player_client": ["android", "web"]}},
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION
    if extra:
        opts.update(extra)
    return opts


def get_basic_youtube_info(url):
    """Fallback metadata source when yt-dlp is blocked by YouTube bot checks."""
    response = requests.get(
        "https://www.youtube.com/oembed",
        params={"url": url, "format": "json"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "title": data.get("title", "video"),
        "thumbnail": data.get("thumbnail_url"),
        "duration": None,
        "uploader": data.get("author_name"),
    }


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    """Fetch video metadata — yt-dlp with cookies, simplified format list."""
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    opts = get_ydl_opts({
        "skip_download": True,
        "format": "best",  # don't evaluate all formats, just get metadata
    })

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                "title":     info.get("title", "video"),
                "thumbnail": info.get("thumbnail"),
                "duration":  info.get("duration"),
                "uploader":  info.get("uploader"),
                "formats":   QUALITY_OPTIONS,
            })
    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        err_lower = err.lower()
        # Heuristic matching because yt-dlp error text can vary between locales.
        needs_fallback = any(indicator in err_lower for indicator in BOT_CHECK_INDICATORS)
        if not needs_fallback:
            logger.warning("[IzuTube] yt-dlp metadata fetch failed: %s", e)
            return jsonify({"error": "Failed to fetch video info from YouTube. Verify the URL. If the video is restricted, authentication may be required."}), 400
        try:
            info = get_basic_youtube_info(url)
            return jsonify({**info, "formats": QUALITY_OPTIONS})
        except requests.RequestException as fallback_error:
            logger.warning("[IzuTube] oEmbed fallback failed: %s", fallback_error)
            return jsonify({"error": "Failed to fetch video info. If the video is restricted, authentication may be required."}), 400
    except Exception as e:
        logger.exception("[IzuTube] Unexpected error while fetching metadata")
        return jsonify({"error": "Failed to fetch video info"}), 500


@app.route("/api/download", methods=["POST"])
def download():
    """Try cobalt.tools first, fall back to yt-dlp with cookies."""
    data = request.json or {}
    url = data.get("url", "").strip()
    fmt = data.get("format", "mp3")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    is_audio = fmt == "mp3"
    quality = fmt.replace("p", "") if not is_audio else "1080"

    # ── Try cobalt.tools first ──
    try:
        payload = {
            "url": url,
            "downloadMode": "audio" if is_audio else "auto",
            "videoQuality": quality,
            "audioFormat": "mp3",
            "audioBitrate": "320",
            "filenameStyle": "pretty",
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        res = requests.post(COBALT_API, json=payload, headers=headers, timeout=15)
        result = res.json()
        status = result.get("status")

        if status in ("redirect", "tunnel", "stream"):
            return jsonify({"url": result.get("url")})

        if status == "picker":
            items = result.get("picker", [])
            if items:
                return jsonify({"url": items[0].get("url")})

        print(f"[IzuTube] cobalt status={status}, falling back to yt-dlp")

    except Exception as e:
        print(f"[IzuTube] cobalt error: {e} — falling back to yt-dlp")

    # ── Fallback: yt-dlp with cookies ──
    if not COOKIES_FILE:
        return jsonify({"error": "Download failed. Add COOKIES_CONTENT to Railway environment variables."}), 500

    try:
        out_dir = Path(f"/tmp/izutube_{os.urandom(4).hex()}")
        out_dir.mkdir(exist_ok=True)

        if is_audio:
            ydl_opts = get_ydl_opts({
                "format": "bestaudio/best",
                "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }],
            })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        else:
            format_candidates = [
                (
                    f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={quality}]+bestaudio/"
                    f"best[height<={quality}]/"
                    f"bestvideo+bestaudio/best"
                ),
                (
                    f"bestvideo[height<={quality}]+bestaudio/"
                    f"best[height<={quality}]/"
                    "bestvideo+bestaudio/best"
                ),
                "bestvideo+bestaudio/best",
                "best",
            ]

            for attempt_index, format_candidate in enumerate(format_candidates):
                ydl_opts = get_ydl_opts({
                    "format": format_candidate,
                    "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
                    "merge_output_format": "mp4",
                })
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                    break
                except yt_dlp.utils.DownloadError as e:
                    is_last = attempt_index == len(format_candidates) - 1
                    if not is_last:
                        logger.warning(
                            "[IzuTube] yt-dlp format attempt failed, retrying with fallback (%s/%s): %s",
                            attempt_index + 1,
                            len(format_candidates),
                            e,
                        )
                        continue
                    raise

        files = list(out_dir.iterdir())
        if not files:
            return jsonify({"error": "Download failed — no file produced"}), 500

        file_path = str(files[0])
        ext = "mp3" if is_audio else "mp4"
        title = data.get("title", "download").replace("/", "-")

        def _cleanup():
            time.sleep(300)
            try:
                os.remove(file_path)
                out_dir.rmdir()
            except Exception:
                pass
        threading.Thread(target=_cleanup, daemon=True).start()

        return send_file(
            file_path,
            as_attachment=True,
            download_name=f"{title}.{ext}",
            mimetype="audio/mpeg" if ext == "mp3" else "video/mp4",
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
