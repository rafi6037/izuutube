import os
import re
import uuid
import threading
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import yt_dlp

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

DOWNLOAD_DIR = Path("/tmp/ytdl")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Store job statuses
jobs = {}

def cleanup_file(path: str, delay: int = 120):
    """Delete file after delay seconds."""
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
        except Exception:
            pass
    threading.Thread(target=_delete, daemon=True).start()


def sanitize_filename(name: str) -> str:
    return re.sub(r'[^\w\s\-.]', '', name).strip()


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/info", methods=["POST"])
def get_info():
    """Fetch video metadata."""
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []

            # Video formats
            seen = set()
            for f in info.get("formats", []):
                height = f.get("height")
                if height and f.get("vcodec") != "none" and height not in seen:
                    seen.add(height)
                    formats.append({
                        "id": f["format_id"],
                        "label": f"{height}p",
                        "type": "video",
                        "height": height,
                    })

            # Sort video formats descending
            formats = sorted(
                [x for x in formats if x["type"] == "video"],
                key=lambda x: x["height"],
                reverse=True,
            )

            # Add MP3 option
            formats.insert(0, {"id": "mp3", "label": "MP3 (Audio Only)", "type": "audio"})

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
    """Download video/audio and return the file."""
    data = request.json or {}
    url = data.get("url", "").strip()
    fmt = data.get("format", "mp3")
    title = sanitize_filename(data.get("title", "download"))

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = str(uuid.uuid4())
    out_path = DOWNLOAD_DIR / job_id
    out_path.mkdir(exist_ok=True)

    progress_data = {"percent": 0, "status": "downloading", "speed": "", "eta": ""}
    jobs[job_id] = progress_data

    def progress_hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "0%").strip().replace("%", "")
            try:
                progress_data["percent"] = float(pct)
            except Exception:
                pass
            progress_data["speed"] = d.get("_speed_str", "").strip()
            progress_data["eta"] = d.get("_eta_str", "").strip()
        elif d["status"] == "finished":
            progress_data["percent"] = 99
            progress_data["status"] = "processing"

    if fmt == "mp3":
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(out_path / "%(title)s.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }],
            "progress_hooks": [progress_hook],
            "quiet": True,
        }
        ext = "mp3"
    else:
        ydl_opts = {
            "format": f"bestvideo[height<={fmt.replace('p','')}]+bestaudio/best[height<={fmt.replace('p','')}]/best",
            "outtmpl": str(out_path / "%(title)s.%(ext)s"),
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook],
            "quiet": True,
        }
        ext = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        files = list(out_path.iterdir())
        if not files:
            return jsonify({"error": "Download failed — file not found"}), 500

        file_path = str(files[0])
        progress_data["percent"] = 100
        progress_data["status"] = "done"
        progress_data["file"] = file_path

        cleanup_file(file_path, delay=300)

        filename = f"{title}.{ext}"
        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype="audio/mpeg" if ext == "mp3" else "video/mp4",
        )

    except Exception as e:
        progress_data["status"] = "error"
        return jsonify({"error": str(e)}), 500
    finally:
        # Cleanup job after a while
        def _cleanup_job():
            time.sleep(600)
            jobs.pop(job_id, None)
        threading.Thread(target=_cleanup_job, daemon=True).start()


@app.route("/api/progress/<job_id>")
def progress(job_id):
    """Stream download progress."""
    def generate():
        for _ in range(300):
            job = jobs.get(job_id)
            if not job:
                yield f"data: {{}}\n\n"
                break
            import json
            yield f"data: {json.dumps(job)}\n\n"
            if job.get("status") in ("done", "error"):
                break
            time.sleep(0.5)
    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
