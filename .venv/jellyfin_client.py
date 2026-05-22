"""
jellyfin_client.py
Thin wrapper around the Jellyfin REST API for DVR Pro / IPTV recording.
Handles authentication, channel listing, timer scheduling, and recordings.
"""

import requests
import logging
from datetime import datetime, timezone

JELLYFIN_URL = "http://localhost:8096"


def _headers(api_key):
    return {
        "X-Emby-Token": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── Connection ──────────────────────────────────────────────────────────────

def test_connection(api_key):
    """Returns (ok: bool, message: str)."""
    try:
        r = requests.get(
            f"{JELLYFIN_URL}/System/Info",
            headers=_headers(api_key),
            timeout=5
        )
        if r.status_code == 200:
            info = r.json()
            return True, f"Connected to {info.get('ServerName', 'Jellyfin')} v{info.get('Version', '?')}"
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, str(e)


# ── Channels ─────────────────────────────────────────────────────────────────

def get_channels(api_key):
    """
    Returns a list of dicts:
      { id, name, number, icon_url }
    """
    try:
        r = requests.get(
            f"{JELLYFIN_URL}/LiveTv/Channels",
            headers=_headers(api_key),
            params={"SortBy": "SortName", "SortOrder": "Ascending"},
            timeout=10
        )
        r.raise_for_status()
        items = r.json().get("Items", [])
        channels = []
        for ch in items:
            channels.append({
                "id": ch.get("Id"),
                "name": ch.get("Name", "Unknown"),
                "number": ch.get("ChannelNumber", ""),
                "icon_url": (
                    f"{JELLYFIN_URL}/Items/{ch['Id']}/Images/Primary?height=48"
                    if ch.get("ImageTags", {}).get("Primary") else ""
                ),
            })
        return channels
    except Exception as e:
        logging.error(f"[Jellyfin] get_channels error: {e}")
        return []


# ── Timers (scheduled recordings) ────────────────────────────────────────────

def get_timers(api_key):
    """Returns list of pending/active timer dicts."""
    try:
        r = requests.get(
            f"{JELLYFIN_URL}/LiveTv/Timers",
            headers=_headers(api_key),
            timeout=10
        )
        r.raise_for_status()
        items = r.json().get("Items", [])
        timers = []
        for t in items:
            timers.append({
                "id": t.get("Id"),
                "name": t.get("Name", "Unknown"),
                "channel_name": t.get("ChannelName", ""),
                "channel_id": t.get("ChannelId", ""),
                "start_date": t.get("StartDate", ""),
                "end_date": t.get("EndDate", ""),
                "status": t.get("Status", ""),
                "series_timer_id": t.get("SeriesTimerId", ""),
            })
        return timers
    except Exception as e:
        logging.error(f"[Jellyfin] get_timers error: {e}")
        return []


def create_timer(api_key, channel_id, start_iso, duration_seconds, name=""):
    """
    Schedule a one-time recording on Jellyfin.
    start_iso: ISO 8601 UTC string, e.g. "2026-05-22T20:00:00Z"
    Returns (ok: bool, message: str)
    """
    try:
        # Parse start, compute end
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        from datetime import timedelta
        end_dt = start_dt + timedelta(seconds=duration_seconds)

        payload = {
            "ChannelId": channel_id,
            "StartDate": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "EndDate": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Name": name,
            "Overview": "",
            "PrePaddingSeconds": 0,
            "PostPaddingSeconds": 0,
            "IsPrePaddingRequired": False,
            "IsPostPaddingRequired": False,
        }

        r = requests.post(
            f"{JELLYFIN_URL}/LiveTv/Timers",
            headers=_headers(api_key),
            json=payload,
            timeout=10
        )
        if r.status_code in (200, 204):
            return True, "Timer created successfully."
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        logging.error(f"[Jellyfin] create_timer error: {e}")
        return False, str(e)


def delete_timer(api_key, timer_id):
    """Cancel a pending timer. Returns (ok, message)."""
    try:
        r = requests.delete(
            f"{JELLYFIN_URL}/LiveTv/Timers/{timer_id}",
            headers=_headers(api_key),
            timeout=10
        )
        if r.status_code in (200, 204):
            return True, "Timer cancelled."
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


# ── Completed Recordings ──────────────────────────────────────────────────────

def get_recordings(api_key, limit=50):
    """Returns list of completed recording dicts."""
    try:
        r = requests.get(
            f"{JELLYFIN_URL}/LiveTv/Recordings",
            headers=_headers(api_key),
            params={"Limit": limit, "SortBy": "DateCreated", "SortOrder": "Descending"},
            timeout=10
        )
        r.raise_for_status()
        items = r.json().get("Items", [])
        recordings = []
        for rec in items:
            run_time_ticks = rec.get("RunTimeTicks", 0)
            minutes = int(run_time_ticks / 600_000_000) if run_time_ticks else 0
            size_mb = rec.get("Size", 0) // (1024 * 1024)

            recordings.append({
                "id": rec.get("Id"),
                "name": rec.get("Name", "Unknown"),
                "channel_name": rec.get("ChannelName", ""),
                "start_date": rec.get("StartDate", ""),
                "duration_min": minutes,
                "size_mb": size_mb,
                "status": rec.get("Status", ""),
                "path": rec.get("Path", ""),
            })
        return recordings
    except Exception as e:
        logging.error(f"[Jellyfin] get_recordings error: {e}")
        return []


def delete_recording(api_key, recording_id):
    """Delete a completed recording. Returns (ok, message)."""
    try:
        r = requests.delete(
            f"{JELLYFIN_URL}/LiveTv/Recordings/{recording_id}",
            headers=_headers(api_key),
            timeout=10
        )
        if r.status_code in (200, 204):
            return True, "Recording deleted."
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)