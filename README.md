# 📺 nufuStreamRecorder (DVR Manager Pro)

A lightweight, Flask-powered Digital Video Recorder (DVR) designed to schedule and automate recordings from the NUFU IPTV JSON API. This tool provides a mobile-friendly web interface to manage schedules, track active recordings, and browse completed files.

## ✨ Features

- **Automated Scheduling**: Set one-time or weekly recurring recordings for any stream.
- **Favorite-Team Sports Scheduling**: Fetch structured ESPN team schedules for selected NHL/NFL favorites without LLM calls.
- **Mobile-First UI**: Fully responsive Bootstrap 5 interface optimized for both desktop and smartphones.
- **Dynamic Filenaming**: Custom "Alternate Name" field allowing you to tag recordings (e.g., `Channel_EpisodeTitle_Timestamp.mp4`).
- **Real-time Monitoring**: Visual indicators for active recordings with a "Live" pulse badge.
- **Smart Search**: Instant "Filter-as-you-type" search bars for both the channel list and the completed recordings library.
- **FFmpeg Engine**: Uses standard FFmpeg for high-quality stream capturing without transcoding (direct copy).
- **Sports Folders**: Sports recordings are routed under the configured recording path: NHL to `Hockey`, NFL to `Football`, and MLB to `Baseball`; other recordings remain at the root.
- **Production Ready**: Robust path handling, rotating logs, and thread-safe recording management.

## 🚀 Getting Started

### Prerequisites

1. **Python 3.10+**
2. **FFmpeg**: Install a build for your operating system and make sure `ffmpeg` is available on `PATH`.

### Installation

1. **Clone the repository** and open the project folder.

2. **Install dependencies** from PowerShell on Windows:
   ```powershell
   py -m pip install Flask requests schedule waitress
   ```
   On macOS, use:
   ```text
   python3 -m pip install Flask requests schedule waitress
   ```

3. **Configure Settings** in the app. By default, recordings go to the current user's `Videos/Recordings` folder; sports are saved in their league subfolders, and the dashboard library also lists nested recordings. Settings, including the optional dormant Gemini key, are stored beside `.venv/app.py`.

### Running the App

Run `.venv/app.py` with Python (`py .venv/app.py` on Windows or `python3 .venv/app.py` on macOS). The app starts the background schedulers and serves the site with Waitress at `http://localhost:5100`.

## 📂 Project Structure

```text
nufuStreamRecorder/
├── .venv/
│   ├── app.py              # Flask backend and scheduler
│   ├── sports_scheduler.py # Favorite-team schedule integration
│   ├── schedules.json      # Manual recording tasks
│   └── templates/          # Dashboard and settings UI
└── README.md
```

## 🛠 Usage

1. **Search**: Use the search bar in the "Schedule Recording" section to quickly find your channel.
2. **Naming**: Add an optional **Alternate Name** to make your files easier to identify later.
3. **Schedule**: Select the days and start time. Use **Duration (Hrs)** to specify how long to record (supports decimals like `1.5` for 90 minutes).
4. **Library**: View and filter completed recordings in the table at the bottom. The table automatically hides technical details like "Channel ID" on mobile devices to save space.
5. **Favorite-team games**: In **Favorite Teams**, add the NHL and/or NFL teams you follow. ESPN's structured schedule determines game start times and status; the dashboard lists favorite-team games in the next rolling seven days with visitor, home team, and local start time. Schedules refresh every six hours; no AI API key is required. Use **Save Favorites & Refresh Schedule** to request an immediate refresh. The recorder checks NUFU shortly before puck drop, starts with the matching NUFU `games` fixture (which must have a `fixture_id`), and falls back to a NUFU channel matching a broadcast partner listed by ESPN if the fixture is unavailable or fails. If a stream ends early, the app refreshes the selected fixture or ESPN-listed broadcast channel and retries; each attempt is saved as a separate video part. Each game and manual recording runs independently, so overlapping events can record concurrently, subject to available system and network resources. Sports recordings run for up to six hours for NHL or five hours for NFL. Manually scheduled one-off and recurring recordings remain available for any stream in the feed.

## ⚙️ Running continuously

Keep the Python process running while recordings are scheduled. For automatic startup, configure your operating system's task or service manager (for example, Task Scheduler on Windows or `launchd` on macOS).

## 📜 License
[MIT](https://choosealicense.com/licenses/mit/) - Feel free to use and modify for personal use.

---

### 🤝 Contributing
Found a bug or have a feature request? Open an issue or submit a pull request on GitHub!
