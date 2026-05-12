import os
import threading
import time
import requests
import yt_dlp
from pathlib import Path
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

COBALT_API = "https://api.cobalt.tools/"
COOKIES_FILE = None

# Write cookies from environment variable to a temp file at startup
cookies_content = os.environ.get("COOKIES_CONTENT", "").strip()
if cookies_content:
    COOKIES_FILE = "/tmp/yt_cookies.txt"
    with open(COOKIES_FILE, "w") as f:
        f.write(cookies_content)
    print("[IzuTube] Cookies loaded from environment variable ✓")
else:
    print("[IzuTube] No cookies found — running without authentication")


def get_ydl_opts(extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"skip": ["dash", "hls"]}},
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if extra:
        opts.update(extra)
    return opts


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

            # Build a simple fixed format list — cobalt handles actual quality selection
            formats = [
                {"id": "mp3",  "label": "MP3 — Audio Only", "type": "audio"},
                {"id": "1080p","label": "1080p Video",       "type": "video", "height": 1080},
                {"id": "720p", "label": "720p Video",        "type": "video", "height": 720},
                {"id": "480p", "label": "480p Video",        "type": "video", "height": 480},
                {"id": "360p", "label": "360p Video",        "type": "video", "height": 360},
            ]

            return jsonify({
                "title":     info.get("title", "video"),
                "thumbnail": info.get("thumbnail"),
                "duration":  info.get("duration"),
                "uploader":  info.get("uploader"),
                "formats":   formats,
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 400


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
        else:
            ydl_opts = get_ydl_opts({
                "format": (
                    f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={quality}]+bestaudio/"
                    f"best[height<={quality}]/"
                    f"bestvideo+bestaudio/best"
                ),
                "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
                "merge_output_format": "mp4",
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

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
