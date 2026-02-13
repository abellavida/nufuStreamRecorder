import subprocess
import json
import requests
import os
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import schedule

# --- 1. Configuration ---
JSON_API_URL = "https://nufu.tv/json/jcarter@abellavida.com"
LOG_PATH = "/home/jc3/Videos/recording_log.txt"
SAVE_FOLDER = "/home/jc3/Videos/"

# --- 2. Logging Setup ---
log_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
logging.basicConfig(
    handlers=[log_handler],
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def get_stream_info(api_url, target_id):
    """
    Scans every list in the JSON (channels, fixtures, etc.) for a matching ID.
    """
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # If the JSON is a dictionary, iterate through every top-level key
        if isinstance(data, dict):
            for key in data:
                category_list = data[key]

                # Ensure the value is actually a list (to avoid errors on single strings/metadata)
                if isinstance(category_list, list):
                    for item in category_list:
                        # Extract IDs safely
                        c_id = str(item.get('channel_id', ''))
                        f_id = str(item.get('fixture_id', ''))

                        if target_id == c_id or target_id == f_id:
                            logging.info(f"ID {target_id} found in category: {key}")
                            return item

        # If the JSON is just a flat list
        elif isinstance(data, list):
            for item in data:
                if str(item.get('channel_id')) == target_id or str(item.get('fixture_id')) == target_id:
                    return item

        return None
    except Exception as e:
        logging.error(f"Failed to fetch or parse JSON: {e}")
        return None


def record_stream(stream_url, name, duration_seconds):
    """Executes FFmpeg with duration in seconds and notifies on completion."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_name = name.replace(' ', '_').replace('/', '-')
    filename = os.path.join(SAVE_FOLDER, f"{clean_name}_{timestamp}.mp4")

    logging.info(f"STARTING RECORDING: {name} (Duration: {duration_seconds}s)")
    print(f"\n[!] Recording Started: {name}")

    command = [
        'ffmpeg', '-y',
        '-i', stream_url,
        '-t', str(duration_seconds),
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        filename
    ]

    try:
        # Script stays here until FFmpeg finishes or hits the timeout
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=duration_seconds + 60)

        # --- Notification Logic ---
        success_msg = f"SUCCESS: Recording finished and file closed: {filename}"
        logging.info(success_msg)
        print(f"\n[✔] {success_msg}")

        # Optional: Trigger a Linux Desktop Notification
        os.system(f'notify-send "Recording Complete" "Finished recording {name}. File is now closed."')

    except subprocess.TimeoutExpired:
        msg = "TIMEOUT: FFmpeg process killed for safety. File has been closed out."
        logging.warning(msg)
        print(f"\n[!] {msg}")
        os.system(f'notify-send "Recording Timeout" "{name} recording was forced to close."')

    except subprocess.CalledProcessError as e:
        err_msg = f"FFMPEG ERROR: {e.stderr}"
        logging.error(err_msg)
        print(f"\n[✘] {err_msg}")


def schedule_recording(target_id, start_time, duration_seconds):
    """Handles the scheduling loop."""

    def task():
        logging.info(f"Scheduled time reached. Looking for ID: {target_id}")
        data = get_stream_info(JSON_API_URL, target_id)

        if data:
            m3u8_url = data.get('secure_url')
            name = data.get('channel_name') or data.get('fixture_name') or "Unknown_Stream"
            if m3u8_url:
                record_stream(m3u8_url, name, duration_seconds)
            else:
                logging.error("No secure_url found.")
        else:
            logging.error(f"ID {target_id} not found at recording time.")

    schedule.every().day.at(start_time).do(task)
    logging.info(f"Scheduler active for ID {target_id} at {start_time}. Duration: {duration_seconds}s")
    print(f"Monitoring... Waiting for {start_time}. Logging to {LOG_PATH}")

    while True:
        schedule.run_pending()
        time.sleep(1)


# --- 3. Interactive Execution ---
if __name__ == "__main__":
    print("--- Stream Recorder Setup ---")
    user_id = input("Enter Channel/Fixture ID: ").strip()
    user_time = input("Enter start time (HH:MM): ").strip()
    user_hours = input("Enter duration in HOURS (e.g., 1.5 or 3): ").strip()

    try:
        # CONVERSION LOGIC: Hours to Seconds
        # 1 hour = 3600 seconds
        duration_seconds = int(float(user_hours) * 3600)

        print(f"Verifying ID... (Will record for {duration_seconds} seconds)")
        initial_check = get_stream_info(JSON_API_URL, user_id)

        if initial_check:
            name = initial_check.get('channel_name') or initial_check.get('fixture_name')
            print(f"ID Verified! Target: {name}")
            schedule_recording(user_id, user_time, duration_seconds)
        else:
            print(f"Error: ID {user_id} not found in JSON.")

    except ValueError:
        print("Invalid input. Please ensure duration is a number.")

