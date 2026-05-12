import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

COBALT_API = "https://api.cobalt.tools/"

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    """Fetch video metadata."""
    import yt_dlp
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            seen = set()
            for f in info.get("formats", []):
                height = f.get("height")
                if height and f.get("vcodec") != "none" and height not in seen:
                    seen.add(height)
                    formats.append({
                        "id": f"{height}p",
                        "label": f"{height}p Video",
                        "type": "video",
                        "height": height
                    })

            formats = sorted(formats, key=lambda x: x["height"], reverse=True)
            formats.insert(0, {"id": "mp3", "label": "MP3 — Audio Only", "type": "audio"})

            return jsonify({
                "title": info.get("title", "video"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
                "uploader": info.get("uploader"),
                "formats": formats,
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def download():
    """Get download link via cobalt.tools API — no bot issues."""
    data = request.json or {}
    url = data.get("url", "").strip()
    fmt = data.get("format", "mp3")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    is_audio = fmt == "mp3"
    quality = fmt.replace("p", "") if not is_audio else "1080"

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

    try:
        res = requests.post(COBALT_API, json=payload, headers=headers, timeout=20)
        result = res.json()
        status = result.get("status")

        if status == "error":
            code = result.get("error", {}).get("code", "unknown error")
            return jsonify({"error": code}), 400

        if status in ("redirect", "tunnel", "stream"):
            return jsonify({"url": result.get("url")})

        if status == "picker":
            items = result.get("picker", [])
            if items:
                return jsonify({"url": items[0].get("url")})

        return jsonify({"error": "Unexpected response from server"}), 500

    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out. Try again."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
