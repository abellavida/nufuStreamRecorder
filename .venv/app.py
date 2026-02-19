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
import schedule

# --- Configuration ---
JSON_API_URL = "https://nufu.tv/json/jcarter@abellavida.com"
SAVE_FOLDER = "/home/jc3/Videos/"
DB_FILE = "schedules.json"
os.makedirs(SAVE_FOLDER, exist_ok=True)

app = Flask(__name__)

active_recordings = []
recordings_lock = threading.Lock()


# --- Persistence ---
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []


def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)


def delete_from_db(task_uuid):
    db = load_db()
    db = [t for t in db if t['uuid'] != task_uuid]
    save_db(db)
    # Remove from active schedule queue
    schedule.clear(task_uuid)


# --- Logic ---
def record_stream(stream_url, name, duration_seconds, task_uuid, repeat):
    with recordings_lock:
        active_recordings.append(name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(SAVE_FOLDER, f"{name.replace(' ', '_')}_{timestamp}.mp4")

    command = ['ffmpeg', '-y', '-i', stream_url, '-t', str(duration_seconds), '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
               filename]

    try:
        subprocess.run(command, timeout=duration_seconds + 120)
    finally:
        with recordings_lock:
            if name in active_recordings: active_recordings.remove(name)
        # If not repeating, delete schedule after one run
        if not repeat:
            delete_from_db(task_uuid)


def get_stream_by_id(target_id):
    try:
        resp = requests.get(JSON_API_URL).json()
        for category in resp:
            if isinstance(resp[category], list):
                for item in resp[category]:
                    if str(item.get('channel_id')) == target_id or str(item.get('fixture_id')) == target_id:
                        return item
    except:
        return None
    return None


def job_wrapper(target_id, duration, task_uuid, repeat):
    data = get_stream_by_id(target_id)
    if data:
        url = data.get('secure_url')
        name = data.get('channel_name') or data.get('fixture_name')
        threading.Thread(target=record_stream, args=(url, name, duration, task_uuid, repeat)).start()


def register_schedule(task):
    """Registers a task with the schedule library based on selected days."""
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
                job_wrapper, task['id'], task['duration'], task['uuid'], task['repeat']
            ).tag(task['uuid'])


# --- Background Scheduler ---
def run_scheduler():
    saved_tasks = load_db()
    for task in saved_tasks:
        register_schedule(task)

    while True:
        schedule.run_pending()
        time.sleep(1)


# --- Routes ---
@app.route('/')
def index():
    try:
        resp = requests.get(JSON_API_URL).json()
    except:
        resp = {}
    return render_template('index.html', data=resp, schedules=load_db(), active=active_recordings)


@app.route('/add', methods=['POST'])
def add_schedule():
    task = {
        'uuid': str(uuid.uuid4()),
        'id': request.form.get('stream_id'),
        'name': request.form.get('stream_name'),  # Hidden field in HTML
        'time': request.form.get('time'),
        'duration': int(float(request.form.get('hours')) * 3600),
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
    delete_from_db(task_uuid)
    return redirect(url_for('index'))


if __name__ == '__main__':
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)