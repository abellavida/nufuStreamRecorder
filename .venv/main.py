import subprocess
import json
import requests
import os
import time
import logging
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime
import schedule

# --- 1. Configuration ---
JSON_API_URL = "https://nufu.tv/json/jcarter@abellavida.com"
LOG_PATH = "/home/jc3/Videos/recording_log.txt"
SAVE_FOLDER = "/home/jc3/Videos/"

# Ensure the save directory exists
os.makedirs(SAVE_FOLDER, exist_ok=True)

# Global thread-safe list to track active recordings
active_recordings = []
recordings_lock = threading.Lock()

# --- 2. Logging Setup (Rotating) ---
# Each log is max 5MB, keeps 5 historical backups
log_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
logging.basicConfig(
    handlers=[log_handler],
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_stream_info(api_url, target_id):
    """Searches across all categories (channels, fixtures, etc.) for target_id."""
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict):
            for key in data:
                category_list = data[key]
                if isinstance(category_list, list):
                    for item in category_list:
                        c_id = str(item.get('channel_id', ''))
                        f_id = str(item.get('fixture_id', ''))
                        if target_id == c_id or target_id == f_id:
                            logging.info(f"ID {target_id} found in category: {key}")
                            return item
        return None
    except Exception as e:
        logging.error(f"JSON Error: {e}")
        return None


def status_monitor():
    """Background thread that periodically displays active recording count."""
    while True:
        with recordings_lock:
            count = len(active_recordings)
            current_list = list(active_recordings)

        if count > 0:
            print(f"\n[STATUS MONITOR] {count} Active Recording(s): {', '.join(current_list)}")

        time.sleep(30)


def record_stream(stream_url, name, duration_seconds):
    """Executes FFmpeg in a dedicated thread."""
    # Add to active monitor
    with recordings_lock:
        active_recordings.append(name)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = name.replace(' ', '_').replace('/', '-')
    filename = os.path.join(SAVE_FOLDER, f"{clean_name}_{timestamp}.mp4")

    logging.info(f"STARTING RECORDING: {name} ({duration_seconds}s)")
    print(f"\n[!] Started: {name} (Closing in {duration_seconds}s)")

    command = [
        'ffmpeg', '-y',
        '-i', stream_url,
        '-t', str(duration_seconds),
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        filename
    ]

    try:
        # Blocks the child thread only; MainThread remains free
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=duration_seconds + 120)

        success_msg = f"SUCCESS: File closed and saved: {filename}"
        logging.info(success_msg)
        print(f"\n[✔] {success_msg}")
        os.system(f'notify-send "Recording Complete" "{name} is finished."')

    except subprocess.TimeoutExpired:
        logging.warning(f"TIMEOUT: {name} forced to close via safety kill switch.")
    except subprocess.CalledProcessError as e:
        logging.error(f"FFMPEG ERROR ({name}): {e.stderr}")
    finally:
        # Always remove from active monitor, even on failure
        with recordings_lock:
            if name in active_recordings:
                active_recordings.remove(name)


def add_to_schedule(target_id, start_time, duration_seconds):
    """Creates a scheduled job that spawns a recording thread."""

    def job_wrapper():
        logging.info(f"Clock hit {start_time}. Fetching stream for ID {target_id}...")
        data = get_stream_info(JSON_API_URL, target_id)

        if data:
            m_url = data.get('secure_url')
            name = data.get('channel_name') or data.get('fixture_name') or f"Stream_{target_id}"
            if m_url:
                # Spawn independent thread
                t = threading.Thread(target=record_stream, args=(m_url, name, duration_seconds),
                                     name=f"Rec-{target_id}")
                t.start()
            else:
                logging.error(f"URL missing for ID {target_id}")
        else:
            logging.error(f"ID {target_id} not found at start time.")

    schedule.every().day.at(start_time).do(job_wrapper)


# --- 3. Interactive Execution ---
if __name__ == "__main__":
    # Launch status monitor
    threading.Thread(target=status_monitor, daemon=True, name="Monitor").start()

    print("--- Multi-Threaded Stream Recorder ---")

    while True:
        u_id = input("\nEnter Channel or Fixture ID: ").strip()
        u_time = input("Enter start time (HH:MM): ").strip()
        u_hours = input("Enter duration in HOURS: ").strip()

        try:
            # Check for past-time issues
            now_str = datetime.now().strftime("%H:%M")
            if u_time < now_str:
                print(f"⚠️  WARNING: {u_time} is in the past. This will schedule for TOMORROW.")

            dur_sec = int(float(u_hours) * 3600)

            # Immediate verification
            check = get_stream_info(JSON_API_URL, u_id)
            if check:
                found_name = check.get('channel_name') or check.get('fixture_name')
                print(f"Verified: {found_name}")
                add_to_schedule(u_id, u_time, dur_sec)
                print(f"Scheduled {found_name} at {u_time}.")
            else:
                print(f"ID {u_id} not currently found, but added to schedule anyway.")
                add_to_schedule(u_id, u_time, dur_sec)

        except ValueError:
            print("Invalid number for hours.")

        if input("\nAdd another recording? (y/n): ").lower() != 'y':
            break

    print(f"\n[!] All tasks scheduled. Monitoring logs at {LOG_PATH}")
    print("Press Ctrl+C to stop the script.")

    while True:
        schedule.run_pending()
        time.sleep(1)