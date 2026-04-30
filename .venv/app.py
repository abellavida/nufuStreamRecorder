import os
import json
import time
import uuid
import logging
import signal
import threading
import subprocess
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, jsonify

# --- CONFIGURATION ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONFIG = {
    'api_url': 'YOUR_IPTV_JSON_URL_HERE',  # Replace with your actual feed URL
    'save_path': os.path.join(os.path.expanduser('~'), 'Videos', 'Recordings'),
    'db_file': os.path.join(BASE_DIR, 'schedules.json'),
    'fav_file': os.path.join(BASE_DIR, 'favorites.json')
}

# Ensure directories exist
os.makedirs(CONFIG['save_path'], exist_ok=True)

app = Flask(__name__)

# --- STATE MANAGEMENT ---
active_recordings = []
recordings_lock = threading.Lock()

# Orphaned Process Management: { "stream_url": subprocess_object }
active_processes = {}
processes_lock = threading.Lock()

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
STREAM_LOG = os.path.join(BASE_DIR, "stream_debug.log")


# --- DATABASE HELPERS ---
def load_db():
    if os.path.exists(CONFIG['db_file']):
        with open(CONFIG['db_file'], 'r') as f: return json.load(f)
    return []


def save_db(data):
    with open(CONFIG['db_file'], 'w') as f: json.dump(data, f, indent=4)


def load_favorites():
    if os.path.exists(CONFIG['fav_file']):
        try:
            with open(CONFIG['fav_file'], 'r') as f:
                return json.load(f)
        except:
            return []
    return []


def save_favorites(fav_list):
    with open(CONFIG['fav_file'], 'w') as f: json.dump(fav_list, f)


def get_saved_files():
    files = []
    for f in os.listdir(CONFIG['save_path']):
        if f.endswith('.mp4'):
            path = os.path.join(CONFIG['save_path'], f)
            stats = os.stat(path)
            files.append({
                'name': f,
                'size': f"{stats.st_size / (1024 * 1024):.1f} MB",
                'date': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M')
            })
    return sorted(files, key=lambda x: x['date'], reverse=True)


# --- CORE RECORDING LOGIC ---
def record_stream(stream_url, name, duration_seconds, task_uuid, repeat, alt_name):
    """Battle-hardened recording with overlap management and network resilience."""

    # 1. ORPHAN MANAGEMENT: Kill any existing recording using this same URL
    with processes_lock:
        if stream_url in active_processes:
            old_proc = active_processes[stream_url]
            logging.info(f"OVERLAP DETECTED: Terminating previous process for {stream_url}")
            try:
                # Try graceful 'q' stop for FFmpeg
                old_proc.communicate(input='q', timeout=2)
            except:
                os.kill(old_proc.pid, signal.SIGTERM)
            del active_processes[stream_url]

    # 2. PREP FILENAME
    with recordings_lock:
        active_recordings.append(name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = name.replace(' ', '_').replace('/', '-')
    suffix = f"_{alt_name.replace(' ', '_')}" if alt_name else ""
    filename = os.path.join(CONFIG['save_path'], f"{clean_name}{suffix}_{timestamp}.mp4")

    # 3. CONSTRUCT RESILIENT FFMPEG COMMAND
    # -reconnect flags help survive network glitches and stream flips
    command = [
        'ffmpeg', '-y', '-loglevel', 'warning',
        '-reconnect', '1', '-reconnect_at_eof', '1',
        '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
        '-i', stream_url,
        '-t', str(int(duration_seconds)),
        '-c', 'copy', '-map', '0', '-ignore_unknown',
        '-copyts',  # Keeps A/V in sync during stream switches
        '-bsf:a', 'aac_adtstoasc',
        filename
    ]

    try:
        with open(STREAM_LOG, "a") as f_debug:
            f_debug.write(f"\n--- SESSION: {name} | {datetime.now()} ---\n")

            # Start process with stdin=PIPE so we can send 'q' if we need to kill it later
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=f_debug, stderr=f_debug, text=True)

            with processes_lock:
                active_processes[stream_url] = process

            logging.info(f"RECORDING STARTED: {name} (PID: {process.pid})")
            process.wait()

    except Exception as e:
        logging.error(f"CRITICAL ERROR for {name}: {e}")
    finally:
        # CLEANUP
        with processes_lock:
            if stream_url in active_processes and active_processes[stream_url] == process:
                del active_processes[stream_url]
        with recordings_lock:
            if name in active_recordings: active_recordings.remove(name)
        logging.info(f"RECORDING FINISHED: {name}")


# --- ROUTES ---
@app.route('/')
def index():
    try:
        api_data = requests.get(CONFIG['api_url'], timeout=5).json()
    except:
        api_data = {}

    return render_template('index.html',
                           data=api_data,
                           schedules=load_db(),
                           active=active_recordings,
                           files=get_saved_files(),
                           favorites=load_favorites())


@app.route('/add', methods=['POST'])
def add_schedule():
    new_task = {
        "uuid": str(uuid.uuid4()),
        "name": request.form.get('stream_name'),
        "stream_id": request.form.get('stream_id'),
        "time": request.form.get('time'),
        "hours": float(request.form.get('hours', 3.0)),
        "favorite": request.form.get('favorite') == 'true'
    }

    db = load_db()
    db.append(new_task)
    save_db(db)
    return redirect('/')


@app.route('/toggle_fav/<stream_id>')
def toggle_fav(stream_id):
    favs = load_favorites()
    if stream_id in favs:
        favs.remove(stream_id)
    else:
        favs.append(stream_id)
    save_favorites(favs)
    return jsonify({"status": "success", "favorites": favs})


@app.route('/delete/<task_uuid>')
def delete_task(task_uuid):
    db = load_db()
    db = [t for t in db if t['uuid'] != task_uuid]
    save_db(db)
    return redirect('/')


# --- SCHEDULER THREAD ---
def scheduler_loop():
    while True:
        now = datetime.now().strftime("%H:%M")
        db = load_db()

        # This is a simplified example; you'd typically look up the stream URL
        # from your API here based on the stream_id stored in the DB.
        # For now, we assume your logic handles finding the URL.

        time.sleep(30)


if __name__ == '__main__':
    # Start background scheduler
    threading.Thread(target=scheduler_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)