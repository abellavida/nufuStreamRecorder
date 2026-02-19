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
        logging.info(f"SUCCESS: {name} finalized and file closed.")
    except Exception as e:
        logging.error(f"FFMPEG ERROR on {name}: {e}")
    finally:
        with recordings_lock:
            if name in active_recordings:
                active_recordings.remove(name)

        # If it's a one-time recording, remove from schedule after completion
        if not repeat:
            logging.info(f"Task {task_uuid} (Run-Once) completed. Removing from DB.")
            db = load_db()
            db = [t for t in db if t['uuid'] != task_uuid]
            save_db(db)
            schedule.clear(task_uuid)


def get_stream_by_id(target_id):
    """Fetches fresh stream info from the API."""
    try:
        response = requests.get(JSON_API_URL, timeout=10)
        data = response.json()
        for key in data:
            if isinstance(data[key], list):
                for item in data[key]:
                    if str(item.get('channel_id')) == target_id or str(item.get('fixture_id')) == target_id:
                        return item
        return None
    except Exception as e:
        logging.error(f"API fetch error: {e}")
        return None


def job_wrapper(target_id, duration_seconds, task_uuid, repeat):
    """Bridge between scheduler and recording thread."""
    stream_data = get_stream_by_id(target_id)
    if stream_data:
        url = stream_data.get('secure_url')
        name = stream_data.get('channel_name') or stream_data.get('fixture_name') or "Unknown"
        if url:
            threading.Thread(
                target=record_stream,
                args=(url, name, duration_seconds, task_uuid, repeat),
                name=f"Rec-{target_id}"
            ).start()
    else:
        logging.error(f"Task {task_uuid} failed: ID {target_id} not in API.")


def register_schedule(task):
    """Adds a task to the live schedule library."""
    days_map = {
        'monday': schedule.every().monday,
        'tuesday': schedule.every().tuesday,
        'wednesday': schedule.every().wednesday,
        'thursday': schedule.every().thursday,
        'friday': schedule.every().friday,
        'saturday': schedule.every().saturday,
        'sunday': schedule.every().sunday
    }

    for day in task['days']:
        if day in days_map:
            days_map[day].at(task['time']).do(
                job_wrapper,
                task['id'],
                task['duration'],
                task['uuid'],
                task['repeat']
            ).tag(task['uuid'])


# --- 5. Background Thread ---
def run_scheduler():
    """Continuous loop to check the clock."""
    # Load all saved schedules into the live scheduler at startup
    initial_tasks = load_db()
    for task in initial_tasks:
        register_schedule(task)

    while True:
        schedule.run_pending()
        time.sleep(1)


# --- 6. Flask Routes ---
@app.route('/')
def index():
    try:
        api_data = requests.get(JSON_API_URL, timeout=5).json()
    except:
        api_data = {}

    return render_template(
        'index.html',
        data=api_data,
        schedules=load_db(),
        active=active_recordings,
        files=get_saved_files()
    )


@app.route('/add', methods=['POST'])
def add_schedule():
    hours = float(request.form.get('hours'))
    task = {
        'uuid': str(uuid.uuid4()),
        'id': request.form.get('stream_id'),
        'name': request.form.get('stream_name'),
        'time': request.form.get('time'),
        'duration': int(hours * 3600),
        'days': request.form.getlist('days'),
        'repeat': True if request.form.get('repeat') else False
    }

    db = load_db()
    db.append(task)
    save_db(db)
    register_schedule(task)
    return redirect(url_for('index'))


@app.route('/delete/<task_uuid>')
def delete(task_uuid):
    db = load_db()
    db = [t for t in db if t['uuid'] != task_uuid]
    save_db(db)
    schedule.clear(task_uuid)
    return redirect(url_for('index'))


# --- 7. Main Execution ---
if __name__ == '__main__':
    # Debug info for the console
    full_path = os.path.abspath(os.path.expanduser(SAVE_FOLDER))
    print(f"\n[DEBUG] Video Directory: {full_path}")
    if os.path.exists(full_path):
        count = len([f for f in os.listdir(full_path) if f.lower().endswith('.mp4')])
        print(f"[DEBUG] Found {count} existing MP4 files.")
    else:
        print(f"[DEBUG] WARNING: {full_path} does not exist yet.")

    # Start scheduler thread
    threading.Thread(target=run_scheduler, daemon=True, name="Scheduler").start()

    # Start Web Server
    app.run(host='0.0.0.0', port=5000, debug=False)