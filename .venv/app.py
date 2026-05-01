import subprocess
import json
import requests
import os
import time
import threading
import uuid
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


def load_settings():
    """Loads configuration dictionary from disk."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except:
            return DEFAULT_SETTINGS
    return DEFAULT_SETTINGS


def load_schedules():
    """Loads scheduled tasks list from disk."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except:
            return []
    return []


def save_json(path, data):
    """Saves data to a JSON file."""
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)


# Global State
settings = load_settings()
active_recordings = {}
recordings_lock = threading.Lock()


# --- 2. Recording Engine ---
def record_stream(stream_url, name, duration, task_uuid, custom_prefix=""):
    """
    Executes the FFmpeg recording process.
    - custom_prefix: Prepends user text to the filename if provided.
    - task_uuid: Used to track the process in the 'Active Recordings' UI.
    """
    # 1. Prepare Paths
    save_path = os.path.abspath(os.path.expanduser(settings['save_path']))
    os.makedirs(save_path, exist_ok=True)

    # 2. Format Filename (Prefix + Name + Timestamp)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    clean_name = name.replace(' ', '_').replace('/', '-')

    # Prepend prefix if it exists, otherwise just use the name
    prefix_str = f"{custom_prefix.strip()}_" if custom_prefix.strip() else ""
    filename = os.path.join(save_path, f"{prefix_str}{clean_name}_{timestamp}.mp4")

    # 3. Define FFmpeg Command
    # Flags explained:
    # -reconnect: Force reconnect on disconnect
    # -reconnect_streamed: Keep trying if the source is a stream
    # -t: Limit recording to the scheduled duration (in seconds)
    # -c copy: Do not re-encode (saves CPU, keeps original quality)
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
        # 4. Launch FFmpeg Process
        # stdout/stderr are piped to DEVNULL to keep your console clean,
        # but you can pipe to subprocess.PIPE if you need to debug.
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        # 5. Register in Global State for the "Live" UI
        with recordings_lock:
            active_recordings[task_uuid] = {
                "name": name,
                "proc": proc,
                "start": start_timestamp
            }

        print(f"🔴 RECORDING STARTED: {filename}")

        # 6. Wait for recording to complete
        proc.wait()

    except Exception as e:
        print(f"❌ ERROR DURING RECORDING ({name}): {e}")

    finally:
        # 7. Cleanup: Remove from Active Recordings regardless of success/fail
        with recordings_lock:
            active_recordings.pop(task_uuid, None)
        print(f"🏁 RECORDING FINISHED: {name}")


def job_wrapper(task):
    """Fetches a fresh URL from the API right before recording starts."""
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
            threading.Thread(target=record_stream,
                             args=(found_url, task['name'], task['duration'], task['uuid'])).start()
    except Exception as e:
        print(f"Job Launcher Error: {e}")


# --- 3. Scheduler Management ---
def register_all_tasks():
    """Syncs the background schedule with the JSON database."""
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
                # Dynamically call e.g., schedule.every().monday.at("HH:MM").do(...)
                getattr(schedule.every(), day_method).at(t['time']).do(job_wrapper, t).tag(t['uuid'])

    print(f"📅 Scheduler Synced: {len(tasks)} tasks active.")


def run_scheduler_loop():
    """Background thread to process pending jobs."""
    register_all_tasks()
    while True:
        schedule.run_pending()
        time.sleep(5)


# --- 4. Web Routes ---
@app.route('/')
def index():
    global settings
    settings = load_settings()
    # ... (API loading logic) ...

    files = []
    save_path = os.path.abspath(os.path.expanduser(settings['save_path']))
    if os.path.exists(save_path):
        for f in os.listdir(save_path):
            if f.endswith('.mp4'):
                p = os.path.join(save_path, f)
                stat = os.stat(p)
                # Calculate duration based on size (Assumes ~3Mbps average bitrate for IPTV)
                # Formula: (Size in bytes * 8) / Bitrate_bps / 60 = Minutes
                approx_mins = (stat.st_size * 8) / (3000000) / 60
                duration_str = f"{int(approx_mins)} MIN" if approx_mins > 1 else "< 1 MIN"

                dt = datetime.fromtimestamp(stat.st_mtime)
                files.append({
                    'name': f,
                    'size': f"{stat.st_size // (1024 * 1024)} MB",
                    'date': dt.strftime('%b %d, %Y').upper(),
                    'time_created': dt.strftime('%I:%M %p'),
                    'duration': duration_str  # Real-world approximation
                })
        files.sort(key=lambda x: os.path.getmtime(os.path.join(save_path, x['name'])), reverse=True)

    return render_template('index.html', data=api_data, settings=settings,
                           schedules=load_schedules(), active=active_recordings.values(), files=files)


# --- Updated Route to handle Custom Name ---
@app.route('/add', methods=['POST'])
def add():
    db = load_schedules()
    selected_days = request.form.getlist('days')
    if not selected_days:
        selected_days = [datetime.now().strftime('%a').lower()]

    task = {
        'uuid': str(uuid.uuid4()),
        'id': request.form.get('stream_id'),
        'name': request.form.get('stream_name'),
        'custom_prefix': request.form.get('custom_prefix', '').strip(),  # New Field
        'time': request.form.get('time'),
        'duration_seconds': int(float(request.form.get('hours', 1)) * 3600),
        'duration_display': f"{request.form.get('hours')} HRS",  # Stored for the UI
        'days': selected_days
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
    # Start the background clock
    threading.Thread(target=run_scheduler_loop, daemon=True).start()
    # use_reloader=False is critical to prevent the background thread from running twice
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)