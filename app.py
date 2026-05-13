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
    {"id": "videos", "label": "Videos — Video with Audio", "type": "video"},
    {"id": "mp3-mp4", "label": "MP3 + MP4 — Both Formats", "type": "mixed"},
]
SUPPORTED_FORMATS = {"mp3", "videos", "mp3-mp4"}
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


def is_valid_https_url(url):
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def parse_optional_seconds(value, field_name):
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer number of seconds")
    if parsed < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return parsed


def find_https_download_link(payload, *, seen=None, depth=0, depth_limit=MAX_RESPONSE_PARSE_DEPTH):
    if depth >= depth_limit:
        return None

    if seen is None:
        seen = set()

    if isinstance(payload, str):
        value = payload.strip()
        if is_valid_https_url(value):
            return value
        return None

    if isinstance(payload, dict):
        obj_id = id(payload)
        if obj_id in seen:
            return None
        seen.add(obj_id)

        for key in DOWNLOAD_URL_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and is_valid_https_url(value):
                return value
        for value in payload.values():
            found = find_https_download_link(value, seen=seen, depth=depth + 1, depth_limit=depth_limit)
            if found:
                return found
        return None

    if isinstance(payload, list):
        obj_id = id(payload)
        if obj_id in seen:
            return None
        seen.add(obj_id)

        for item in payload:
            found = find_https_download_link(item, seen=seen, depth=depth + 1, depth_limit=depth_limit)
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
    """Fetch a download link from yt2mp3converter."""
    data = request.json or {}
    url = data.get("url", "").strip()
    selected_format = str(data.get("format", "mp3")).strip().lower() or "mp3"

    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not is_valid_youtube_url(url):
        return jsonify({"error": "Please provide a valid YouTube URL"}), 400
    if selected_format not in SUPPORTED_FORMATS:
        return jsonify({"error": "Invalid format. Use mp3, videos, or mp3-mp4."}), 400

    try:
        start_time = parse_optional_seconds(data.get("stime"), "stime")
    except ValueError:
        return jsonify({"error": "Invalid stime. Use integer seconds and ensure value is >= 0."}), 400
    try:
        end_time = parse_optional_seconds(data.get("etime"), "etime")
    except ValueError:
        return jsonify({"error": "Invalid etime. Use integer seconds and ensure value is >= 0."}), 400
    if start_time is not None and end_time is not None and end_time <= start_time:
        return jsonify({"error": "etime must be greater than stime"}), 400

    try:
        params = {"url": url, "format": selected_format}
        if start_time is not None:
            params["stime"] = start_time
        if end_time is not None:
            params["etime"] = end_time

        res = requests.get(
            YT2MP3_API,
            params=params,
            timeout=20,
        )
        res.raise_for_status()

        content_type = res.headers.get("Content-Type", "").lower()
        if content_type.startswith("application/json"):
            payload = res.json()
            download_url = find_https_download_link(payload)
            if download_url:
                response_payload = {"url": download_url, "download": download_url}
                if isinstance(payload, dict):
                    for key in ("title", "duration", "filesize"):
                        value = payload.get(key)
                        if value:
                            response_payload[key] = value
                return jsonify(response_payload)
            message = payload.get("error") if isinstance(payload, dict) else None
            return jsonify({"error": message or "Failed to get download link"}), 502

        text = res.text.strip()
        if is_valid_https_url(text):
            return jsonify({"url": text, "download": text})
        return jsonify({"error": "Unexpected response from download provider"}), 502
    except requests.RequestException as e:
        logger.warning("[IzuTube] Download provider request failed: %s", e)
        return jsonify({"error": "Failed to contact download provider"}), 502
    except Exception:
        logger.exception("[IzuTube] Unexpected error while fetching download link")
        return jsonify({"error": "Failed to get download link"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
