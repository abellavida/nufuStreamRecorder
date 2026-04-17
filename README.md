# 📺 nufuStreamRecorder (DVR Manager Pro)

A lightweight, Flask-powered Digital Video Recorder (DVR) designed to schedule and automate recordings from the NUFU IPTV JSON API. This tool provides a mobile-friendly web interface to manage schedules, track active recordings, and browse completed files.

## ✨ Features

- **Automated Scheduling**: Set one-time or weekly recurring recordings for any stream.
- **Mobile-First UI**: Fully responsive Bootstrap 5 interface optimized for both desktop and smartphones.
- **Dynamic Filenaming**: Custom "Alternate Name" field allowing you to tag recordings (e.g., `Channel_EpisodeTitle_Timestamp.mp4`).
- **Real-time Monitoring**: Visual indicators for active recordings with a "Live" pulse badge.
- **Smart Search**: Instant "Filter-as-you-type" search bars for both the channel list and the completed recordings library.
- **FFmpeg Engine**: Uses standard FFmpeg for high-quality stream capturing without transcoding (direct copy).
- **Production Ready**: Robust path handling, rotating logs, and thread-safe recording management.

## 🚀 Getting Started

### Prerequisites

1. **Python 3.10+**
2. **FFmpeg**: Must be installed on your system.
   ```bash
   sudo apt update && sudo apt install ffmpeg -y
   ```
3. **Virtual Environment** (Recommended):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/abellavida/nufuStreamRecorder.git
   cd nufuStreamRecorder
   ```

2. **Install dependencies**:
   ```bash
   pip install flask requests schedule
   ```

3. **Configure Paths**:
   Open `app.py` and verify the `BASE_DIR` and `SAVE_FOLDER` settings. By default, it creates a `/recordings` folder inside the project directory.

### Running the App

```bash
python app.py
```
The app will be accessible at `http://your-server-ip:5000`.

## 📂 Project Structure

```text
nufuStreamRecorder/
├── app.py              # Flask backend & Scheduler logic
├── schedules.json      # Persistence for your recording tasks
├── recordings/         # Folder where .mp4 files are saved
├── templates/
│   └── index.html      # Responsive Bootstrap UI
└── dvr_production.log  # Rotating system logs
```

## 🛠 Usage

1. **Search**: Use the search bar in the "Schedule Recording" section to quickly find your channel.
2. **Naming**: Add an optional **Alternate Name** to make your files easier to identify later.
3. **Schedule**: Select the days and start time. Use **Duration (Hrs)** to specify how long to record (supports decimals like `1.5` for 90 minutes).
4. **Library**: View and filter completed recordings in the table at the bottom. The table automatically hides technical details like "Channel ID" on mobile devices to save space.

## ⚙️ Production Deployment (systemd)

To keep the DVR running 24/7 on a Linux server, create a service file:

1. Create the file: `sudo nano /etc/systemd/system/dvr.service`
2. Paste the following (update paths to match your server):
   ```ini
   [Unit]
   Description=NUFU DVR Manager
   After=network.target

   [Service]
   User=jc-media
   WorkingDirectory=/home/jc-media/PycharmProjects/nufuStreamRecorder
   ExecStart=/home/jc-media/PycharmProjects/nufuStreamRecorder/.venv/bin/python app.py
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```
3. Enable and start:
   ```bash
   sudo systemctl enable dvr
   sudo systemctl start dvr
   ```

## 📜 License
[MIT](https://choosealicense.com/licenses/mit/) - Feel free to use and modify for personal use.

---

### 🤝 Contributing
Found a bug or have a feature request? Open an issue or submit a pull request on GitHub!
