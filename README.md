# YTDL — YouTube Downloader

A clean, fast YouTube downloader web app built with Flask + yt-dlp.

## Deploy to Railway (5 minutes)

### Step 1 — Push to GitHub
```bash
git init
git add .
git commit -m "initial commit"
gh repo create ytdl-app --public --push
# or manually push to a GitHub repo
```

### Step 2 — Deploy on Railway
1. Go to [railway.app](https://railway.app) → **New Project**
2. Select **Deploy from GitHub repo** → pick your repo
3. Railway auto-detects `nixpacks.toml` and installs Python + ffmpeg
4. Your app is live in ~2 minutes!

### Step 3 — Use it
- Open your Railway URL
- Paste any YouTube link → click **Fetch**
- Pick MP3 or video quality (360p → 1080p)
- Click **Download** → file saves to your device

## Local Development

```bash
# Install ffmpeg (required)
# macOS: brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg

pip install -r requirements.txt
python app.py
# Open http://localhost:8000
```

## Tech Stack
- **Backend**: Python Flask + yt-dlp
- **Frontend**: Vanilla HTML/CSS/JS (no build step)
- **Deployment**: Railway with nixpacks (auto-installs ffmpeg)
- **Timeout**: 300s — handles long videos fine

## Notes
- Files are auto-deleted from server after 5 minutes
- MP3 exports at 320kbps
- Video merges best available video + audio into MP4
- For restricted videos, set `COOKIES_CONTENT` in Railway. Both Netscape cookie file content and JSON cookie exports are supported.
