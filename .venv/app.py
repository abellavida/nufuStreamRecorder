import subprocess
import json
import requests
import os
import time
import threading
import uuid
import logging  # Added back
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
import schedule

app = Flask(__name__)

# --- 1. Configuration & Persistence ---
DB_FILE = "schedules.json"
SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "paid_api": "https://nufu.tv/json/jcarter@abellavida.com",
    "gemini_key": "",
    "save_path": "/home/jc-media/Videos/Recordings"
}

# --- LOGGING SETUP ---
logging.basicConfig(
    filename='dvr_production.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)


def log_ffmpeg(task_name, message):
    """Specific logger for FFmpeg output."""
    with open('stream_log.log', 'a') as f:
        f.write(f"{datetime.now()} [{task_name}]: {message}\n")


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except:
            return DEFAULT_SETTINGS
    return DEFAULT_SETTINGS


def load_schedules():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except:
            return []
    return []


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)


# Global State
settings = load_settings()
active_recordings = {}
recordings_lock = threading.Lock()


# --- 2. Recording Engine ---
def record_stream(stream_url, name, duration, task_uuid, custom_prefix=""):
    save_path = os.path.abspath(os.path.expanduser(settings['save_path']))
    os.makedirs(save_path, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    clean_name = name.replace(' ', '_').replace('/', '-')

    prefix_str = f"{custom_prefix.strip()}_" if custom_prefix.strip() else ""
    filename = os.path.join(save_path, f"{prefix_str}{clean_name}_{timestamp}.mp4")

    cmd = [
        'ffmpeg', '-y',
        '-reconnect', '1',
        '-reconnect_at_eof', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', stream_url,
        '-t', str(duration),
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        filename
    ]

    start_timestamp = datetime.now().strftime("%I:%M %p")

    try:
        # Capture stderr to pipe it into our log file
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        with recordings_lock:
            active_recordings[task_uuid] = {
                "name": name,
                "proc": proc,
                "start": start_timestamp
            }

        logging.info(f"RECORDING STARTED: {name} -> {filename}")

        # Log FFmpeg output in real-time
        for line in proc.stderr:
            log_ffmpeg(name, line.strip())

        proc.wait()

    except Exception as e:
        logging.error(f"ERROR DURING RECORDING ({name}): {e}")

    finally:
        with recordings_lock:
            active_recordings.pop(task_uuid, None)

        # ONE-TIME PURGE LOGIC
        db = load_schedules()
        task_data = next((t for t in db if t['uuid'] == task_uuid), None)
        if task_data and task_data.get('one_time'):
            new_db = [t for t in db if t['uuid'] != task_uuid]
            save_json(DB_FILE, new_db)
            schedule.clear(task_uuid)
            logging.info(f"ONE-TIME TASK COMPLETED AND PURGED: {name}")

        logging.info(f"RECORDING FINISHED: {name}")


def job_wrapper(task):
    try:
        r = requests.get(settings['paid_api'], timeout=10).json()
        target_id = str(task['id'])
        found_url = None

        for cat in r:
            if isinstance(r[cat], list):
                for item in r[cat]:
                    if str(item.get('channel_id')) == target_id or str(item.get('fixture_id')) == target_id:
                        found_url = item.get('secure_url')
                        break

        if found_url:
            # Pass custom_prefix and duration_seconds correctly
            threading.Thread(target=record_stream,
                             args=(found_url, task['name'], task['duration_seconds'], task['uuid'],
                                   task.get('custom_prefix', ""))).start()
    except Exception as e:
        logging.error(f"Job Launcher Error for {task.get('name')}: {e}")


# --- 3. Scheduler Management ---
def register_all_tasks():
    schedule.clear()
    tasks = load_schedules()

    day_map = {
        'mon': 'monday', 'tue': 'tuesday', 'wed': 'wednesday',
        'thu': 'thursday', 'fri': 'friday', 'sat': 'saturday', 'sun': 'sunday'
    }

    for t in tasks:
        for day_short in t.get('days', []):
            day_method = day_map.get(day_short.lower())
            if day_method:
                getattr(schedule.every(), day_method).at(t['time']).do(job_wrapper, t).tag(t['uuid'])

    logging.info(f"Scheduler Synced: {len(tasks)} tasks active.")


def run_scheduler_loop():
    register_all_tasks()
    while True:
        schedule.run_pending()
        time.sleep(5)


# --- 4. Web Routes ---
@app.route('/')
def index():
    global settings
    settings = load_settings()

    api_data = {}
    try:
        api_data = requests.get(settings['paid_api'], timeout=5).json()
    except Exception as e:
        logging.error(f"API Error: {e}")

    with recordings_lock:
        stale_keys = [uid for uid, rec in active_recordings.items() if rec['proc'].poll() is not None]
        for uid in stale_keys:
            active_recordings.pop(uid, None)

    files = []
    save_path = os.path.abspath(os.path.expanduser(settings['save_path']))
    if os.path.exists(save_path):
        for f in os.listdir(save_path):
            if f.endswith('.mp4'):
                p = os.path.join(save_path, f)
                try:
                    stat = os.stat(p)
                    approx_mins = (stat.st_size * 8) / (3000000) / 60
                    duration_str = f"{int(approx_mins)} MIN" if approx_mins > 1 else "< 1 MIN"

                    dt = datetime.fromtimestamp(stat.st_mtime)
                    files.append({
                        'name': f,
                        'size': f"{stat.st_size // (1024 * 1024)} MB",
                        'date': dt.strftime('%b %d, %Y').upper(),
                        'time_created': dt.strftime('%I:%M %p'),
                        'duration': duration_str
                    })
                except Exception as e:
                    print(f"Error processing file {f}: {e}")

        files.sort(key=lambda x: os.path.getmtime(os.path.join(save_path, x['name'])), reverse=True)

    return render_template('index.html',
                           data=api_data,
                           settings=settings,
                           schedules=load_schedules(),
                           active=active_recordings.values(),
                           files=files)


@app.route('/add', methods=['POST'])
def add():
    db = load_schedules()
    selected_days = request.form.getlist('days')
    one_time = False

    if not selected_days:
        selected_days = [datetime.now().strftime('%a').lower()]
        one_time = True

    task = {
        'uuid': str(uuid.uuid4()),
        'id': request.form.get('stream_id'),
        'name': request.form.get('stream_name'),
        'custom_prefix': request.form.get('custom_prefix', '').strip(),
        'time': request.form.get('time'),
        'duration_seconds': int(float(request.form.get('hours', 1)) * 3600),
        'duration_display': f"{request.form.get('hours')} HRS",
        'days': selected_days,
        'one_time': one_time  # Flag for auto-delete
    }

    db.append(task)
    save_json(DB_FILE, db)
    register_all_tasks()
    return redirect('/')


@app.route('/delete/<uid>')
def delete(uid):
    db = [t for t in load_schedules() if t['uuid'] != uid]
    save_json(DB_FILE, db)
    schedule.clear(uid)
    return redirect('/')


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    global settings
    if request.method == 'POST':
        settings.update({
            'paid_api': request.form.get('paid_api'),
            'gemini_key': request.form.get('gemini_key'),
            'save_path': request.form.get('save_path')
        })
        save_json(SETTINGS_FILE, settings)
        return redirect('/')
    return render_template('settings.html', settings=load_settings())


if __name__ == '__main__':
    threading.Thread(target=run_scheduler_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)