import subprocess
import json
import requests
import os
import time
import logging
import threading
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from logging.handlers import RotatingFileHandler
import schedule

# --- 1. Configuration ---
JSON_API_URL = "https://nufu.tv/json/jcarter@abellavida.com"
# Using absolute path for consistency
SAVE_FOLDER = "/home/jc3/Videos/"
DB_FILE = "schedules.json"
LOG_PATH = "/home/jc3/Videos/dvr_app.log"

# Ensure directories exist
os.makedirs(SAVE_FOLDER, exist_ok=True)

app = Flask(__name__)

# --- 2. Logging Setup ---
log_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
logging.basicConfig(
    handlers=[log_handler],
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Global thread-safe list for active recordings
active_recordings = []
recordings_lock = threading.Lock()


# --- 3. Helpers & Persistence ---
def load_db():
    """Reads the saved schedules from the JSON file."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading DB: {e}")
            return []
    return []


def save_db(data):
    """Writes the current schedule list to the JSON file."""
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving DB: {e}")


def get_saved_files():
    """Scans the video folder and returns file metadata for the UI."""
    files = []
    target_path = os.path.abspath(os.path.expanduser(SAVE_FOLDER))

    if not os.path.exists(target_path):
        logging.error(f"PATH ERROR: {target_path} not found.")
        return []

    try:
        for entry in os.scandir(target_path):
            # Case-insensitive check for .mp4 files
            if entry.is_file() and entry.name.lower().endswith('.mp4'):
                stats = entry.stat()
                files.append({
                    'name': entry.name,
                    'size': f"{stats.st_size / (1024 * 1024):.2f} MB",
                    'date': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M'),
                    'raw_time': stats.st_mtime  # For sorting
                })
        # Sort by most recent first
        return sorted(files, key=lambda x: x['raw_time'], reverse=True)
    except Exception as e:
        logging.error(f"Failed to scan directory: {e}")
        return []


# --- 4. Recording Logic ---
def record_stream(stream_url, name, duration_seconds, task_uuid, repeat):
    """The actual FFmpeg execution thread."""
    with recordings_lock:
        active_recordings.append(name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = name.replace(' ', '_').replace('/', '-')
    filename = os.path.join(SAVE_FOLDER, f"{clean_name}_{timestamp}.mp4")

    logging.info(f"STARTING RECORDING: {name} for {duration_seconds}s")

    command = [
        'ffmpeg', '-y',
        '-i', stream_url,
        '-t', str(duration_seconds),
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        filename
    ]

    try:
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=duration_seconds + 120)
        logging.info(f"SUCCESS: {