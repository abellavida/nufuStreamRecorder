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
- **Production Ready**: Robust path handling, rotating logs, and thread-safe recording management.

## 🚀 Getting Started

### Prerequisites

1. **Python 3.10+**
2. **FFmpeg**: Install a build for your operating system and make sure `ffmpeg` is available on `PATH`.

### Installation

1. **Clone the repository** and open the project folder.

2. **Install dependencies** from PowerShell on Windows:
   ```powershell
   py -m pip install Flask requests schedule
   ```
   On macOS, use:
   ```text
   python3 -m pip install Flask requests schedule
   ```

3. **Configure Settings** in the app. By default, recordings go to the current user's `Videos/Recordings` folder, and settings and schedules are stored beside `.venv/app.py`.

### Running the App

Run `.venv/app.py` with Python (`py .venv/app.py` on Windows or `python3 .venv/app.py` on macOS). The app will be accessible at `http://localhost:5100`.

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
5. **Favorite-team games**: In **Favorite Teams**, add the NHL and/or NFL teams you follow. The dashboard lists favorite-team games in the next rolling seven days, with the scheduled visitor, home team, and local start time shown from ESPN's structured schedule. The app refreshes selected-team schedules every six hours; no AI API key is required. Use **Save Favorites & Refresh Schedule** to request an immediate refresh. During the final three minutes before a game and again at its scheduled start, the app checks NUFU for the live stream URL, matching the event in the NUFU `games` feed and requiring its `fixture_id`. Published broadcast channels are optional metadata and are not required for recording. Each game and manual recording runs independently, so overlapping events can record concurrently, subject to available system and network resources. Sports recordings use the app's FFmpeg engine and run for up to four hours. Manually scheduled one-off and recurring recordings remain available for any stream in the feed.

## ⚙️ Running continuously

Keep the Python process running while recordings are scheduled. For automatic startup, configure your operating system's task or service manager (for example, Task Scheduler on Windows or `launchd` on macOS).

## 📜 License
[MIT](https://choosealicense.com/licenses/mit/) - Feel free to use and modify for personal use.

---

### 🤝 Contributing
Found a bug or have a feature request? Open an issue or submit a pull request on GitHub!
