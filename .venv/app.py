import subprocess
import json
import requests
import os
import time
import logging
import threading
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
import schedule

# --- Configuration ---
JSON_API_URL = "https://nufu.tv/json/jcarter@abellavida.com"
SAVE_FOLDER = "/home/jc3/Videos/"
DB_FILE = "schedules.json"
os.makedirs(SAVE_FOLDER, exist_ok=True)

app = Flask(__name__)

# Global thread-safe list for active recordings
active_recordings = []
recordings_lock = threading.Lock()


# --- Persistence Logic ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return []


def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)


# --- Recording Logic ---
def record_stream(stream_url, name, duration_seconds):
    with recordings_lock:
        active_recordings.append(name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(SAVE_FOLDER, f"{name.replace(' ', '_')}_{timestamp}.mp4")

    command = [
        'ffmpeg', '-y', '-i', stream_url, '-t', str(duration_seconds),
        '-c', 'copy', '-bsf:a', 'aac_adtstoasc', filename
    ]
    try:
        subprocess.run(command, timeout=duration_seconds + 120)
    finally:
        with recordings_lock:
            if name in active_recordings: active_recordings.remove(name)


def get_stream_by_id(target_id):
    resp = requests.get(JSON_API_URL).json()
    for category in resp:
        if isinstance(resp[category], list):
            for item in resp[category]:
                if str(item.get('channel_id')) == target_id or str(item.get('fixture_id')) == target_id:
                    return item
    return None


def job_wrapper(target_id, duration_seconds):
    data = get_stream_by_id(target_id)
    if data:
        url = data.get('secure_url')
        name = data.get('channel_name') or data.get('fixture_name')
        threading.Thread(target=record_stream, args=(url, name, duration_seconds)).start()


# --- Background Scheduler ---
def run_scheduler():
    # On startup, load existing schedules from JSON
    saved_tasks = load_db()
    for task in saved_tasks:
        schedule.every().day.at(task['time']).do(
            job_wrapper, task['id'], task['duration']
        ).tag(task['id'])

    while True:
        schedule.run_pending()
        time.sleep(1)


# --- Flask Routes ---
@app.route('/')
def index():
    resp = requests.get(JSON_API_URL).json()
    saved_tasks = load_db()
    return render_template('index.html', data=resp, schedules=saved_tasks, active=active_recordings)


@app.route('/add', methods=['POST'])
def add_schedule():
    target_id = request.form.get('stream_id')
    start_time = request.form.get('time')
    hours = float(request.form.get('hours'))
    duration_seconds = int(hours * 3600)

    # Save to JSON
    db = load_db()
    db.append({'id': target_id, 'time': start_time, 'duration': duration_seconds})
    save_db(db)

    # Add to live scheduler
    schedule.every().day.at(start_time).do(job_wrapper, target_id, duration_seconds).tag(target_id)

    return redirect(url_for('index'))


if __name__ == '__main__':
    # Start scheduler in background thread
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)