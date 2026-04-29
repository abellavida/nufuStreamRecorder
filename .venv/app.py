import subprocess
import json
import requests
import os
import time
import logging
import threading
import uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for
from logging.handlers import RotatingFileHandler
import schedule

# --- 1. Global Setup & Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.txt")
DB_FILE = os.path.join(BASE_DIR, "schedules.json")
SERVER_LOG = os.path.join(BASE_DIR, "dvr_production.log")
STREAM_LOG = os.path.join(BASE_DIR, "stream_debug.log")

app = Flask(__name__)

# --- 2. Logging Setup ---
server_handler = RotatingFileHandler(SERVER_LOG, maxBytes=10 * 1024 * 1024, backupCount=5)
logging.basicConfig(
    handlers=[server_handler],
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s'
)


# --- 3. Configuration Management ---
def get_settings():
    defaults = {
        "api_key": "",
        "api_url": "https://nufu.tv/json/jcarter@abellavida.com",
        "save_path": os.path.join(BASE_DIR, "recordings")
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            settings = defaults.copy()
            with open(SETTINGS_FILE, 'r') as f:
                for line in f:
                    if '=' in line:
                        key, val = line.split('=', 1)
                        settings[key.strip()] = val.strip().strip('"').strip("'")
            return settings
        except Exception as e:
            logging.error(f"Error reading settings: {e}")
    return defaults


def save_settings(new_settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            for k, v in new_settings.items():
                f.write(f'{k}="{v}"\n')
        return True
    except Exception as e:
        logging.error(f"Error saving settings: {e}")
        return False


# Global State
CONFIG = get_settings()
ai_client = None
active_recordings = []
recordings_lock = threading.Lock()


def reload_configuration():
    global CONFIG, ai_client
    CONFIG = get_settings()
    try:
        os.makedirs(CONFIG['save_path'], exist_ok=True)
    except Exception as e:
        logging.error(f"STORAGE ERROR: {e}")

    if CONFIG.get("api_key"):
        try:
            from google import genai
            ai_client = genai.Client(api_key=CONFIG["api_key"])
            logging.info("Gemini 2.0 AI Engine Online.")
        except Exception as e:
            logging.error(f"AI INIT FAILED: {e}")
    else:
        ai_client = None


reload_configuration()


# --- 4. DVR Core Logic with Mid-Stream Recovery ---
def ai_is_same_event(original_name, candidate_name):
    if not ai_client: return False
    prompt = f"Original: '{original_name}'. Candidate: '{candidate_name}'. Are these the same sports event? Respond ONLY 'YES' or 'NO'."
    try:
        response = ai_client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        return "YES" in response.text.upper()
    except Exception as e:
        logging.error(f"AI Search Error: {e}")
        return False


def record_stream(stream_url, name, duration_seconds, task_uuid, repeat, alt_name):
    """Records stream with automatic retry if connection drops mid-game."""
    with recordings_lock:
        active_recordings.append(name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = name.replace(' ', '_').replace('/', '-')
    suffix = f"_{alt_name.replace(' ', '_')}" if alt_name else ""
    filename = os.path.join(CONFIG['save_path'], f"{clean_name}{suffix}_{timestamp}.mp4")

    # Recovery variables
    remaining_duration = duration_seconds
    max_retries = 3
    retry_count = 0

    logging.info(f"STARTING REC: {name} (Duration: {duration_seconds}s)")

    while remaining_duration > 30 and retry_count < max_retries:
        start_attempt_time = time.time()

        # Verbose FFmpeg command
        command = [
            'ffmpeg', '-y', '-loglevel', 'verbose',
            '-http_persistent', '0', '-ignore_unknown',
            '-i', stream_url, '-t', str(int(remaining_duration)),
            '-c', 'copy', '-bsf:a', 'aac_adtstoasc', filename if retry_count == 0 else f"{filename}.part{retry_count}"
        ]

        try:
            with open(STREAM_LOG, "a") as f_debug:
                f_debug.write(f"\n--- ATTEMPT {retry_count + 1}: {name} | {datetime.now()} ---\n")
                f_debug.flush()

                process = subprocess.Popen(command, stdout=f_debug, stderr=f_debug, text=True)
                process.wait(timeout=remaining_duration + 300)

                if process.returncode == 0:
                    logging.info(f"COMPLETED: {name}")
                    break  # Success, exit loop
                else:
                    # Connection dropped - calculate time spent
                    elapsed = time.time() - start_attempt_time
                    remaining_duration -= elapsed
                    retry_count += 1

                    if remaining_duration > 30:
                        logging.warning(
                            f"REC DROPPED: {name}. Retrying in 30s ({retry_count}/{max_retries}). Remaining: {int(remaining_duration)}s")
                        time.sleep(30)

        except Exception as e:
            logging.error(f"RUNTIME ERROR during {name}: {e}")
            break

    with recordings_lock:
        if name in active_recordings: active_recordings.remove(name)

    if not repeat:
        db = load_db()
        db = [t for t in db if t['uuid'] != task_uuid]
        save_db(db)
        schedule.clear(task_uuid)


# --- 5. Scheduling Utilities ---
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
    try:
        path = CONFIG['save_path']
        if not os.path.exists(path): return []
        for entry in os.scandir(path):
            if entry.is_file() and entry.name.lower().endswith('.mp4'):
                stats = entry.stat()
                files.append({
                    'name': entry.name, 'size': f"{stats.st_size / (1024 * 1024):.2f} MB",
                    'date': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M'),
                    'raw_time': stats.st_mtime
                })
        return sorted(files, key=lambda x: x['raw_time'], reverse=True)
    except:
        return []


def smart_job_wrapper(target_id, duration_seconds, task_uuid, repeat, alt_name, original_name):
    try:
        resp = requests.get(CONFIG['api_url'], timeout=15).json()
    except:
        reschedule_task(task_uuid, 5);
        return

    found_item = None
    for cat, items in resp.items():
        if isinstance(items, list):
            for item in items:
                if str(item.get('fixture_id')) == str(target_id) or str(item.get('channel_id')) == str(target_id):
                    found_item = item;
                    break

    if not found_item and ai_client:
        for cat, items in resp.items():
            if isinstance(items, list):
                for item in items:
                    cur_name = item.get('fixture_name') or item.get('channel_name') or ""
                    if ai_is_same_event(original_name, cur_name):
                        found_item = item;
                        break
            if found_item: break

    if found_item and found_item.get('secure_url'):
        threading.Thread(target=record_stream,
                         args=(found_item['secure_url'], original_name, duration_seconds, task_uuid, repeat,
                               alt_name)).start()
    else:
        logging.warning(f"Event {original_name} not in feed. Retrying in 5 mins.")
        reschedule_task(task_uuid, 5)


def reschedule_task(task_uuid, delay_minutes):
    db = load_db()
    for task in db:
        if task['uuid'] == task_uuid:
            new_time = (datetime.now() + timedelta(minutes=delay_minutes)).strftime("%H:%M")
            task['time'] = new_time
            save_db(db);
            schedule.clear(task_uuid);
            register_schedule(task)
            break


def register_schedule(task):
    days_map = {d: getattr(schedule.every(), d) for d in
                ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']}
    for day in task['days']:
        day = day.lower()
        if day in days_map:
            days_map[day].at(task['time']).do(
                smart_job_wrapper, task['id'], task['duration'], task['uuid'], task['repeat'], task.get('alt_name', ''),
                task['name']
            ).tag(task['uuid'])


def run_scheduler():
    for task in load_db(): register_schedule(task)
    while True:
        schedule.run_pending();
        time.sleep(1)


# --- 6. Flask Routes ---
@app.route('/')
def index():
    try:
        api_data = requests.get(CONFIG['api_url'], timeout=5).json()
    except:
        api_data = {}
    return render_template('index.html', data=api_data, schedules=load_db(), active=active_recordings,
                           files=get_saved_files())


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if request.method == 'POST':
        new_config = {
            "api_key": request.form.get('api_key'),
            "api_url": request.form.get('api_url'),
            "save_path": request.form.get('save_path')
        }
        if save_settings(new_config):
            reload_configuration()
            return redirect(url_for('index'))
    return render_template('settings.html', settings=get_settings())


@app.route('/add', methods=['POST'])
def add_schedule():
    hours = float(request.form.get('hours'))

    # FIX: Default to today if no days selected
    selected_days = request.form.getlist('days')
    if not selected_days:
        today = datetime.now().strftime('%A').lower()
        selected_days = [today]
        logging.info(f"Defaulting {request.form.get('stream_name')} to {today}")

    task = {'uuid': str(uuid.uuid4()), 'id': request.form.get('stream_id'), 'name': request.form.get('stream_name'),
            'alt_name': request.form.get('alt_name', '').strip(), 'time': request.form.get('time'),
            'duration': int(hours * 3600), 'days': selected_days,
            'repeat': True if request.form.get('repeat') else False}

    db = load_db();
    db.append(task);
    save_db(db);
    register_schedule(task)
    return redirect(url_for('index'))


@app.route('/delete/<task_uuid>')
def delete(task_uuid):
    db = load_db();
    db = [t for t in db if t['uuid'] != task_uuid];
    save_db(db);
    schedule.clear(task_uuid)
    return redirect(url_for('index'))


if __name__ == '__main__':
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)