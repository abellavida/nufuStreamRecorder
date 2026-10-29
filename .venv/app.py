import subprocess
import json
import ast
import requests
import os
import time
import threading
import uuid
import logging  # Added back
import re
from datetime import datetime, timedelta, timezone
from flask import Flask, abort, render_template, request, redirect
import schedule
import sports_scheduler as sports

app = Flask(__name__)

# --- 1. Configuration & Persistence ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "schedules.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
SETTINGS_TEXT_FILE = os.path.join(BASE_DIR, "settings.txt")
TEAM_SCHEDULE_FILE = os.path.join(BASE_DIR, "team_schedule.json")
DEFAULT_SETTINGS = {
    "paid_api": "https://nufu.tv/json/jcarter@abellavida.com",
    "save_path": os.path.join(os.path.expanduser("~"), "Videos", "Recordings"),
    "sports_favorites": []
}

# --- LOGGING SETUP ---
logging.basicConfig(
    filename=os.path.join(BASE_DIR, "dvr_production.log"),
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    encoding="utf-8",
)


def log_ffmpeg(task_name, message):
    """Specific logger for FFmpeg output."""
    with open(os.path.join(BASE_DIR, "stream_log.log"), 'a', encoding="utf-8") as f:
        f.write(f"{datetime.now()} [{task_name}]: {message}\n")


def filename_component(value):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value))
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    return cleaned or "recording"


def load_settings():
    loaded = DEFAULT_SETTINGS.copy()
    if os.path.exists(SETTINGS_TEXT_FILE):
        try:
            with open(SETTINGS_TEXT_FILE, 'r', encoding="utf-8") as f:
                for line in f:
                    key, separator, value = line.partition('=')
                    key = key.strip()
                    if separator and key in DEFAULT_SETTINGS:
                        try:
                            loaded[key] = ast.literal_eval(value.strip())
                        except (SyntaxError, ValueError):
                            loaded[key] = value.strip()
        except OSError as exc:
            logging.error("Unable to load text settings: %s", exc)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                loaded.update({key: data[key] for key in DEFAULT_SETTINGS if key in data})
                if "paid_api" not in data and data.get("api_url"):
                    loaded["paid_api"] = data["api_url"]
                if "save_path" not in data and data.get("recordings_path"):
                    loaded["save_path"] = data["recordings_path"]
                default_save_path = DEFAULT_SETTINGS["save_path"]
                if not loaded.get("save_path") or not os.path.isdir(loaded["save_path"]):
                    loaded["save_path"] = default_save_path
                if "sports_favorites" not in data:
                    legacy_favorites = data.get("favorite_nhl_teams", [])
                    if not isinstance(legacy_favorites, list):
                        legacy_favorites = []
                    loaded["sports_favorites"] = [
                        sports.team_id("NHL", abbr)
                        for abbr in legacy_favorites
                        if sports.parse_team_id(f"NHL:{abbr}")
                    ]
        except (OSError, json.JSONDecodeError) as exc:
            logging.error("Unable to load JSON settings: %s", exc)
    configured_favorites = loaded.get("sports_favorites", [])
    if not isinstance(configured_favorites, list):
        configured_favorites = []
    loaded["sports_favorites"] = sorted({
        sports.team_id(*parsed)
        for favorite in configured_favorites
        if (parsed := sports.parse_team_id(favorite))
    })
    return loaded


def save_app_settings(data):
    stored = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, dict):
                stored = existing
        except (OSError, json.JSONDecodeError) as exc:
            logging.error("Unable to preserve existing JSON settings: %s", exc)
    stored.update(data)
    stored.pop("gemini_key", None)
    stored.pop("groq_key", None)
    stored.pop("groq_api_key", None)
    stored.pop("favorite_nhl_teams", None)
    save_json(SETTINGS_FILE, stored)

def load_schedules():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except:
            return []
    return []


def save_json(path, data):
    with open(path, 'w', encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def load_team_schedule():
    if not os.path.exists(TEAM_SCHEDULE_FILE):
        return {"updated_at": "", "favorites": [], "games": []}
    try:
        with open(TEAM_SCHEDULE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("games"), list):
            return data
    except (OSError, json.JSONDecodeError) as exc:
        logging.error("Unable to load NHL team schedule: %s", exc)
    return {"updated_at": "", "favorites": [], "games": []}


team_schedule_lock = threading.Lock()
team_schedule_refresh_requested = threading.Event()
scheduler_lock = threading.RLock()
sports_start_lock = threading.Lock()
sports_starts_in_progress = set()


def sports_favorites():
    configured = load_settings().get("sports_favorites", [])
    if not isinstance(configured, list):
        return []
    return sorted({
        sports.team_id(*parsed)
        for favorite in configured
        if (parsed := sports.parse_team_id(favorite))
    })


def upcoming_sports_games(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Dashboard schedule time must include a timezone")
    now = now.astimezone(timezone.utc)
    end = now + timedelta(days=7)
    favorites = set(sports_favorites())
    upcoming = []
    for game in load_team_schedule().get("games", []):
        if not favorites.intersection(game.get("favorite_teams", [])):
            continue
        try:
            start = datetime.fromisoformat(str(game["start_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            logging.error("Skipping sports game with invalid start time: %s", game.get("game_id"))
            continue
        if start.tzinfo is None:
            logging.error("Skipping sports game with timezone-naive start time: %s", game.get("game_id"))
            continue
        if not now <= start.astimezone(timezone.utc) <= end:
            continue
        local_start = start.astimezone()
        upcoming.append((
            start.astimezone(timezone.utc),
            {
                **game,
                "game_date": local_start.strftime("%Y-%m-%d"),
                "game_time": local_start.strftime("%I:%M %p"),
            },
        ))
    return [game for _, game in sorted(upcoming, key=lambda entry: entry[0])]


def _merge_sports_game_data(previous, fresh_games):
    old_games = {game.get("game_id"): game for game in previous}
    preserved_keys = (
        "recording_state", "last_start_attempt", "last_start_error",
        "source_used", "selected_feed_id", "matched_fixture_name",
        "last_preflight_check", "preflight_source", "preflight_feed_id",
        "preflight_feed_error",
    )
    merged = []
    for game in fresh_games:
        old = old_games.get(game["game_id"], {})
        merged.append({
            **game,
            **{key: old[key] for key in preserved_keys if key in old},
        })
    return merged


def refresh_team_schedule(force=False):
    favorites = sports_favorites()
    now = datetime.now(timezone.utc)
    with team_schedule_lock:
        cached = load_team_schedule()
        if not favorites:
            if not cached.get("favorites") and not cached.get("games"):
                return
            save_json(TEAM_SCHEDULE_FILE, {
                "updated_at": now.isoformat(),
                "favorites": [],
                "games": [],
                "refresh_error": "",
            })
            return

        try:
            updated = datetime.fromisoformat(cached.get("updated_at", ""))
            fresh = now - updated.astimezone(timezone.utc) < timedelta(hours=6)
        except ValueError:
            fresh = False
        if not force and fresh and cached.get("favorites") == favorites:
            return

        try:
            attempted = datetime.fromisoformat(cached.get("refresh_attempt_at", ""))
            if (cached.get("refresh_attempt_favorites") == favorites
                    and now - attempted.astimezone(timezone.utc) < timedelta(minutes=10)):
                return
        except ValueError:
            pass

        cached["refresh_attempt_at"] = now.isoformat()
        cached["refresh_attempt_favorites"] = favorites
        save_json(TEAM_SCHEDULE_FILE, cached)

    try:
        new_games = sports.fetch_favorite_schedules(
            favorites, now=now
        )
    except (requests.RequestException, sports.SportsScheduleError, ValueError) as exc:
        logging.error("Unable to refresh schedules for favorite sports teams: %s", exc)
        with team_schedule_lock:
            cached = load_team_schedule()
            cached["refresh_error"] = str(exc)
            save_json(TEAM_SCHEDULE_FILE, cached)
        return

    with team_schedule_lock:
        cached = load_team_schedule()
        merged = _merge_sports_game_data(cached.get("games", []), new_games)
        refreshed = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "refresh_attempt_at": datetime.now(timezone.utc).isoformat(),
            "refresh_attempt_favorites": favorites,
            "favorites": favorites,
            "games": merged,
        }
        save_json(TEAM_SCHEDULE_FILE, refreshed)


def get_nufu_feed():
    response = requests.get(load_settings()["paid_api"], timeout=15)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("NUFU feed response must be a JSON object")
    return data


def start_sports_recording(game_id, preflight=False):
    games = load_team_schedule().get("games", [])
    game = next((entry for entry in games
                 if entry.get("game_id") == str(game_id)
                 and entry.get("recording_state") == "scheduled"), None)
    if not game:
        return

    now = datetime.now(timezone.utc)
    attempt_key = "last_preflight_check" if preflight else "last_start_attempt"
    last_attempt = game.get(attempt_key)
    if last_attempt:
        try:
            if now - datetime.fromisoformat(last_attempt).astimezone(timezone.utc) < timedelta(seconds=30):
                return
        except ValueError:
            pass

    attempt_data = {attempt_key: now.isoformat()}
    if not preflight:
        attempt_data["last_start_error"] = ""
    set_sports_recording_data(game_id, attempt_data)
    try:
        feed = get_nufu_feed()
        item = sports.find_live_game(game, feed)
        source_used = "games"
        if not item:
            item = sports.find_broadcast_channel(game, feed)
            source_used = "channels"
        stream_url = item.get("secure_url") if item else None
        if preflight:
            set_sports_recording_data(game_id, {
                "preflight_source": source_used if stream_url else "",
                "preflight_feed_id": (
                    str(item.get("fixture_id") if source_used == "games" else item.get("channel_id"))
                    if stream_url else ""
                ),
                "preflight_feed_error": (
                    "" if stream_url else "No matching live game or broadcast channel is available yet"
                ),
            })
            return
        if not stream_url:
            set_sports_recording_data(game_id, {
                "last_start_error": "No matching live game or broadcast channel is available yet",
            })
            logging.warning("No NUFU stream is available yet for scheduled %s game %s",
                            game["league"], game_id)
            return
        name = (
            item.get("fixture_name") or item.get("channel_name")
            or f"{game['away_name']} at {game['home_name']}"
        )
        task_uuid = f"sports-{game_id}"
        feed_id_field = "fixture_id" if source_used == "games" else "channel_id"
        set_sports_recording_data(game_id, {
            "recording_state": "recording",
            "source_used": source_used,
            "selected_feed_id": str(item.get(feed_id_field)),
            "matched_fixture_name": name,
            "last_start_error": "",
        })
        recorder = threading.Thread(
            target=record_stream,
            args=(stream_url, name, 4 * 3600, task_uuid, game["league"]),
            daemon=True,
        )
        recorder.start()
        logging.info("Started %s recording from NUFU %s feed: %s",
                     game["league"], source_used, name)
    except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError) as exc:
        error_key = "preflight_feed_error" if preflight else "last_start_error"
        updates = {error_key: str(exc)}
        if not preflight:
            updates["recording_state"] = "failed"
        set_sports_recording_data(game_id, updates)
        action = "check NUFU feed for" if preflight else "start"
        logging.error("Unable to %s scheduled sports recording %s: %s", action, game_id, exc)


def _run_sports_recording_start(game_id, preflight):
    try:
        start_sports_recording(game_id, preflight=preflight)
    finally:
        with sports_start_lock:
            sports_starts_in_progress.discard((str(game_id), preflight))


def _queue_sports_recording_start(game_id, preflight=False):
    key = (str(game_id), preflight)
    with sports_start_lock:
        if key in sports_starts_in_progress:
            return
        sports_starts_in_progress.add(key)
    worker = threading.Thread(
        target=_run_sports_recording_start,
        args=(game_id, preflight),
        daemon=True,
    )
    try:
        worker.start()
    except RuntimeError:
        with sports_start_lock:
            sports_starts_in_progress.discard(key)
        logging.exception("Unable to queue sports recording start for game %s", game_id)


def set_sports_recording_data(game_id, updates):
    with team_schedule_lock:
        data = load_team_schedule()
        for game in data.get("games", []):
            if game.get("game_id") == str(game_id):
                game.update(updates)
        save_json(TEAM_SCHEDULE_FILE, data)


def check_due_sports_recordings():
    now = datetime.now(timezone.utc)
    favorites = set(sports_favorites())
    for game in load_team_schedule().get("games", []):
        if not favorites.intersection(game.get("favorite_teams", [])):
            continue
        if game.get("recording_state") != "scheduled":
            continue
        try:
            start = datetime.fromisoformat(game["start_at"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            logging.error("Skipping sports game with invalid start time: %s", game.get("game_id"))
            continue
        if now > start + timedelta(hours=4):
            set_sports_recording_data(game["game_id"], {"recording_state": "missed"})
        elif start - timedelta(minutes=3) <= now < start:
            _queue_sports_recording_start(game["game_id"], preflight=True)
        elif now >= start:
            _queue_sports_recording_start(game["game_id"])


def run_sports_monitor_loop():
    refresh_team_schedule()
    while True:
        refresh_requested = team_schedule_refresh_requested.wait(30)
        team_schedule_refresh_requested.clear()
        refresh_team_schedule(force=refresh_requested)
        check_due_sports_recordings()

# Global State
settings = load_settings()
active_recordings = {}
recordings_lock = threading.Lock()


# --- 2. Recording Engine ---
# Added Logging Info back
def record_stream(stream_url, name, duration, task_uuid, custom_prefix=""):
    start_timestamp = datetime.now().strftime("%I:%M %p")
    recording_state = "failed"

    try:
        save_path = os.path.abspath(os.path.expanduser(settings['save_path']))
        os.makedirs(save_path, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        clean_name = filename_component(name)

        prefix = filename_component(custom_prefix) if custom_prefix.strip() else ""
        prefix_str = f"{prefix}_" if prefix else ""
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

        # Capture stderr to pipe it into our log file
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

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
        recording_state = "completed" if proc.returncode == 0 else "failed"

    except Exception as e:
        logging.error(f"ERROR DURING RECORDING ({name}): {e}")

    finally:
        with recordings_lock:
            active_recordings.pop(task_uuid, None)

        if task_uuid.startswith("sports-"):
            set_sports_recording_data(task_uuid.removeprefix("sports-"), {
                "recording_state": recording_state,
            })

        # ONE-TIME PURGE LOGIC
        db = load_schedules()
        task_data = next((t for t in db if t['uuid'] == task_uuid), None)
        if task_data and task_data.get('one_time'):
            new_db = [t for t in db if t['uuid'] != task_uuid]
            save_json(DB_FILE, new_db)
            with scheduler_lock:
                schedule.clear(task_uuid)
            logging.info(f"ONE-TIME TASK COMPLETED AND PURGED: {name}")

        logging.info(f"RECORDING FINISHED: {name}")


def _run_scheduled_job(task):
    try:
        item = sports.feed_stream_by_id(get_nufu_feed(), task['id'])

        if item and item.get('secure_url'):
            # Pass custom_prefix and duration_seconds correctly
            threading.Thread(target=record_stream,
                             args=(item['secure_url'], task['name'], task['duration_seconds'], task['uuid'],
                                   task.get('custom_prefix', ""))).start()
    except Exception as e:
        logging.error(f"Job Launcher Error for {task.get('name')}: {e}")


def job_wrapper(task):
    threading.Thread(target=_run_scheduled_job, args=(task,), daemon=True).start()


# --- 3. Scheduler Management ---
def register_all_tasks():
    tasks = load_schedules()
    day_map = {
        'mon': 'monday', 'tue': 'tuesday', 'wed': 'wednesday',
        'thu': 'thursday', 'fri': 'friday', 'sat': 'saturday', 'sun': 'sunday'
    }

    with scheduler_lock:
        for task in tasks:
            schedule.clear(task['uuid'])
        for t in tasks:
            for day_short in t.get('days', []):
                day_method = day_map.get(day_short.lower())
                if day_method:
                    getattr(schedule.every(), day_method).at(t['time']).do(job_wrapper, t).tag(t['uuid'], 'manual')

    logging.info(f"Scheduler Synced: {len(tasks)} tasks active.")


def run_scheduler_loop():
    register_all_tasks()
    while True:
        with scheduler_lock:
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
                           team_games=upcoming_sports_games(),
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
    with scheduler_lock:
        schedule.clear(uid)
    return redirect('/')


@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    global settings
    settings = load_settings()
    if request.method == 'POST':
        settings.update({
            'paid_api': request.form.get('paid_api'),
            'save_path': request.form.get('save_path'),
        })
        save_app_settings(settings)
        return redirect('/')

    return render_template('settings.html', settings=settings)


@app.route('/favorites', methods=['GET', 'POST'])
def favorites_page():
    global settings
    settings = load_settings()
    if request.method == 'POST':
        favorite_teams = []
        for value in request.form.getlist('sports_favorites'):
            parsed = sports.parse_team_id(value)
            if not parsed:
                abort(400, description=f"Unknown sports team: {value}")
            normalized = sports.team_id(*parsed)
            if normalized not in favorite_teams:
                favorite_teams.append(normalized)
        favorites_changed = sorted(favorite_teams) != sorted(sports_favorites())
        settings["sports_favorites"] = sorted(favorite_teams)
        save_app_settings(settings)
        if favorites_changed or request.form.get("action") == "refresh":
            team_schedule_refresh_requested.set()
        return redirect('/favorites')

    favorite_ids = set(settings.get("sports_favorites", []))
    games = [
        game for game in load_team_schedule().get('games', [])
        if favorite_ids.intersection(game.get("favorite_teams", []))
    ]
    games.sort(key=lambda game: game.get('start_at', ''))
    teams = [
        {"id": sports.team_id(league, abbr), "league": league, "abbr": abbr, "name": names[0]}
        for league, league_teams in sports.TEAMS.items()
        for abbr, names in league_teams.items()
    ]
    schedule = load_team_schedule()
    return render_template(
        'favorites.html',
        sports_teams=teams,
        sports_favorites=sorted(favorite_ids),
        team_games=games[:50],
        schedule_updated_at=schedule.get("updated_at", ""),
        schedule_error=schedule.get("refresh_error", ""),
    )


if __name__ == '__main__':
    threading.Thread(target=run_scheduler_loop, daemon=True).start()
    threading.Thread(target=run_sports_monitor_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5100, debug=True, use_reloader=False)

# Update 5.15.2026