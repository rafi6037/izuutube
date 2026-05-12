import os
import logging
import requests
from urllib.parse import urlparse
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)
logger = logging.getLogger(__name__)

YT2MP3_API = "https://www.yt2mp3converter.net/apis/fetch.php"
QUALITY_OPTIONS = [
    {"id": "mp3", "label": "MP3 — Audio Only", "type": "audio"},
]
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
DOWNLOAD_URL_KEYS = (
    "url",
    "link",
    "download",
    "download_url",
    "downloadUrl",
    "file",
    "file_url",
    "fileUrl",
)
MAX_RESPONSE_PARSE_DEPTH = 5


def is_valid_youtube_url(url):
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False

    host = (parsed.hostname or "").lower()
    return host in YOUTUBE_HOSTS


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


def find_https_download_link(payload, *, _seen=None, _depth=0, _max_depth=MAX_RESPONSE_PARSE_DEPTH):
    if _depth > _max_depth:
        return None

    if _seen is None:
        _seen = set()

    if isinstance(payload, str):
        value = payload.strip()
        if value.startswith("https://"):
            return value
        return None

    if isinstance(payload, dict):
        obj_id = id(payload)
        if obj_id in _seen:
            return None
        _seen.add(obj_id)

        for key in DOWNLOAD_URL_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("https://"):
                return value
        for value in payload.values():
            found = find_https_download_link(value, _seen=_seen, _depth=_depth + 1, _max_depth=_max_depth)
            if found:
                return found
        return None

    if isinstance(payload, list):
        obj_id = id(payload)
        if obj_id in _seen:
            return None
        _seen.add(obj_id)

        for item in payload:
            found = find_https_download_link(item, _seen=_seen, _depth=_depth + 1, _max_depth=_max_depth)
            if found:
                return found
    return None


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    """Fetch lightweight YouTube metadata with oEmbed and MP3-only format list."""
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "Please provide a valid YouTube URL"}), 400

    try:
        info = get_basic_youtube_info(url)
        return jsonify({**info, "formats": QUALITY_OPTIONS})
    except requests.RequestException as e:
        logger.warning("[IzuTube] oEmbed metadata fetch failed: %s", e)
        return jsonify({"error": "Failed to fetch video info. Verify the YouTube URL."}), 400
    except Exception as e:
        logger.exception("[IzuTube] Unexpected error while fetching metadata")
        return jsonify({"error": "Failed to fetch video info"}), 500


@app.route("/api/download", methods=["POST"])
def download():
    """Fetch an MP3 download link from yt2mp3converter."""
    data = request.json or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "Please provide a valid YouTube URL"}), 400

    try:
        res = requests.get(
            YT2MP3_API,
            params={"url": url, "format": "mp3"},
            timeout=20,
        )
        res.raise_for_status()

        content_type = res.headers.get("Content-Type", "").lower()
        if "application/json" in content_type:
            payload = res.json()
            download_url = find_https_download_link(payload)
            if download_url:
                return jsonify({"url": download_url})
            message = payload.get("error") if isinstance(payload, dict) else None
            return jsonify({"error": message or "Failed to get MP3 download link"}), 502

        text = res.text.strip()
        if text.startswith("https://"):
            return jsonify({"url": text})
        return jsonify({"error": "Unexpected response from MP3 provider"}), 502
    except requests.RequestException as e:
        logger.warning("[IzuTube] MP3 provider request failed: %s", e)
        return jsonify({"error": "Failed to contact MP3 provider"}), 502
    except Exception:
        logger.exception("[IzuTube] Unexpected error while fetching MP3 link")
        return jsonify({"error": "Failed to get MP3 download link"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
