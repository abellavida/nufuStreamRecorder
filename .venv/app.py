import subprocess
import json
from pickle import TRUE

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
SAVE_FOLDER = "/home/jc3/Videos/"
# JSON_API_URL = "https://tvpass.org/playlist.m3u"
DB_FILE = "schedules.json"
LOG_PATH = "/home/jc3/Videos/dvr_app.log"

os.makedirs(SAVE_FOLDER, exist_ok=True)

app = Flask(__name__)

# --- 2. Logging ---
log_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
logging.basicConfig(handlers=[log_handler], level=logging.INFO,
                    format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s')

active_recordings = []
recordings_lock = threading.Lock()


# --- 3. Helpers ---
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []


def save_db(data):
    with open(DB_FILE, 'w') as f: json.dump(data, f, indent=4)


def get_saved_files():
    files = []
    target_path = os.path.abspath(os.path.expanduser(SAVE_FOLDER))
    if not os.path.exists(target_path): return []
    try:
        for entry in os.scandir(target_path):
            if entry.is_file() and entry.name.lower().endswith('.mp4'):
                stats = entry.stat()
                files.append({
                    'name': entry.name,
                    'size': f"{stats.st_size / (1024 * 1024):.2f} MB",
                    'date': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M'),
                    'raw_time': stats.st_mtime
                })
        return sorted(files, key=lambda x: x['raw_time'], reverse=True)
    except:
        return []


# --- 4. Recording Logic ---
def record_stream(stream_url, name, duration_seconds, task_uuid, repeat, alt_name):
    with recordings_lock:
        active_recordings.append(name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Naming Logic: Channel_AltName_Timestamp or Channel_Timestamp
    clean_name = name.replace(' ', '_').replace('/', '-')
    suffix = f"_{alt_name.replace(' ', '_')}" if alt_name else ""
    filename = os.path.join(SAVE_FOLDER, f"{clean_name}{suffix}_{timestamp}.mp4")

    logging.info(f"STARTING: {name} (Alt: {alt_name})")

    command = ['ffmpeg', '-y', '-i', stream_url, '-t', str(duration_seconds), '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
               filename]

    try:
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=duration_seconds + 120)
    except Exception as e:
        logging.error(f"FFMPEG ERROR on {name}: {e}")
    finally:
        with recordings_lock:
            if name in active_recordings: active_recordings.remove(name)
        if not repeat:
            db = load_db()
            db = [t for t in db if t['uuid'] != task_uuid]
            save_db(db)
            schedule.clear(task_uuid)


def job_wrapper(target_id, duration_seconds, task_uuid, repeat, alt_name):
    try:
        data = requests.get(JSON_API_URL, timeout=10).json()
        for key in data:
            if isinstance(data[key], list):
                for item in data[key]:
                    if str(item.get('channel_id')) == target_id or str(item.get('fixture_id')) == target_id:
                        url = item.get('secure_url')
                        name = item.get('channel_name') or item.get('fixture_name')
                        threading.Thread(target=record_stream,
                                         args=(url, name, duration_seconds, task_uuid, repeat, alt_name)).start()
                        return
    except Exception as e:
        logging.error(f"Job Wrapper Error: {e}")


def register_schedule(task):
    days_map = {d: getattr(schedule.every(), d) for d in
                ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']}
    for day in task['days']:
        if day in days_map:
            days_map[day].at(task['time']).do(job_wrapper, task['id'], task['duration'], task['uuid'], task['repeat'],
                                              task.get('alt_name', '')).tag(task['uuid'])


def run_scheduler():
    for task in load_db(): register_schedule(task)
    while True:
        schedule.run_pending()
        time.sleep(1)


# --- 5. Routes ---
@app.route('/')
def index():
    try:
        api_data = requests.get(JSON_API_URL, timeout=5).json()
    except:
        api_data = {}
    return render_template('index.html', data=api_data, schedules=load_db(), active=active_recordings,
                           files=get_saved_files())


@app.route('/add', methods=['POST'])
def add_schedule():
    hours = float(request.form.get('hours'))
    task = {
        'uuid': str(uuid.uuid4()),
        'id': request.form.get('stream_id'),
        'name': request.form.get('stream_name'),
        'alt_name': request.form.get('alt_name', '').strip(),
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


if __name__ == '__main__':
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host='0.0.0.0', port=5050, debug=True)