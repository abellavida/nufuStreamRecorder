import hashlib
import logging
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone

import requests


ESPN_SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team}/schedule"
ESPN_EVENT_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary"
ESPN_LEAGUES = {
    "NHL": ("hockey", "nhl"),
    "NFL": ("football", "nfl"),
}
SCHEDULE_LOOKAHEAD_DAYS = 60

TEAMS = {
    "NHL": {
        "ANA": ("Anaheim Ducks", "Ducks"),
        "BOS": ("Boston Bruins", "Bruins"),
        "BUF": ("Buffalo Sabres", "Sabres"),
        "CAR": ("Carolina Hurricanes", "Hurricanes", "Canes"),
        "CBJ": ("Columbus Blue Jackets", "Blue Jackets"),
        "CGY": ("Calgary Flames", "Flames"),
        "CHI": ("Chicago Blackhawks", "Blackhawks"),
        "COL": ("Colorado Avalanche", "Avalanche", "Avs"),
        "DAL": ("Dallas Stars", "Stars"),
        "DET": ("Detroit Red Wings", "Red Wings"),
        "EDM": ("Edmonton Oilers", "Oilers"),
        "FLA": ("Florida Panthers", "Panthers"),
        "LAK": ("Los Angeles Kings", "LA Kings", "Kings"),
        "MIN": ("Minnesota Wild", "Minnesota", "Wild"),
        "MTL": ("Montreal Canadiens", "Canadiens", "Habs"),
        "NSH": ("Nashville Predators", "Predators", "Preds"),
        "NJD": ("New Jersey Devils", "NJ Devils", "Devils"),
        "NYI": ("New York Islanders", "NY Islanders", "Islanders"),
        "NYR": ("New York Rangers", "NY Rangers", "Rangers"),
        "OTT": ("Ottawa Senators", "Senators", "Sens"),
        "PHI": ("Philadelphia Flyers", "Flyers"),
        "PIT": ("Pittsburgh Penguins", "Penguins", "Pens"),
        "SEA": ("Seattle Kraken", "Kraken"),
        "SJS": ("San Jose Sharks", "San Jose", "SJ", "SJ Sharks", "Sharks"),
        "STL": ("St. Louis Blues", "St Louis Blues", "Blues"),
        "TBL": ("Tampa Bay Lightning", "Lightning", "Bolts"),
        "TOR": ("Toronto Maple Leafs", "Maple Leafs", "Leafs"),
        "UTA": ("Utah Mammoth", "Mammoth"),
        "VAN": ("Vancouver Canucks", "Canucks"),
        "VGK": ("Vegas Golden Knights", "Golden Knights"),
        "WPG": ("Winnipeg Jets", "Jets"),
        "WSH": ("Washington Capitals", "Capitals", "Caps"),
    },
    "NFL": {
        "ARI": ("Arizona Cardinals", "Cardinals"),
        "ATL": ("Atlanta Falcons", "Falcons"),
        "BAL": ("Baltimore Ravens", "Ravens"),
        "BUF": ("Buffalo Bills", "Bills"),
        "CAR": ("Carolina Panthers", "Panthers"),
        "CHI": ("Chicago Bears", "Bears"),
        "CIN": ("Cincinnati Bengals", "Bengals"),
        "CLE": ("Cleveland Browns", "Browns"),
        "DAL": ("Dallas Cowboys", "Cowboys"),
        "DEN": ("Denver Broncos", "Broncos"),
        "DET": ("Detroit Lions", "Lions"),
        "GB": ("Green Bay Packers", "Packers"),
        "HOU": ("Houston Texans", "Texans"),
        "IND": ("Indianapolis Colts", "Colts"),
        "JAX": ("Jacksonville Jaguars", "Jaguars", "Jags"),
        "KC": ("Kansas City Chiefs", "Chiefs"),
        "LAC": ("Los Angeles Chargers", "LA Chargers", "Chargers"),
        "LAR": ("Los Angeles Rams", "LA Rams", "Rams"),
        "LV": ("Las Vegas Raiders", "Raiders"),
        "MIA": ("Miami Dolphins", "Dolphins"),
        "MIN": ("Minnesota Vikings", "Vikings"),
        "NE": ("New England Patriots", "Patriots"),
        "NO": ("New Orleans Saints", "Saints"),
        "NYG": ("New York Giants", "NY Giants", "Giants"),
        "NYJ": ("New York Jets", "NY Jets", "Jets"),
        "PHI": ("Philadelphia Eagles", "Eagles"),
        "PIT": ("Pittsburgh Steelers", "Steelers"),
        "SEA": ("Seattle Seahawks", "Seahawks"),
        "SF": ("San Francisco 49ers", "49ers", "Niners"),
        "TB": ("Tampa Bay Buccaneers", "Buccaneers", "Bucs"),
        "TEN": ("Tennessee Titans", "Titans"),
        "WAS": ("Washington Commanders", "Commanders"),
    },
}

TEAM_LOOKUP = {
    (league, normalize): abbr
    for league, teams in TEAMS.items()
    for abbr, names in teams.items()
    for name in (abbr, *names)
    for normalize in [re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()]
}
ESPN_TEAM_ABBREVIATIONS = {
    "NHL": {
        "ANA": "ANA", "BOS": "BOS", "BUF": "BUF", "CAR": "CAR",
        "CBJ": "CBJ", "CGY": "CGY", "CHI": "CHI", "COL": "COL",
        "DAL": "DAL", "DET": "DET", "EDM": "EDM", "FLA": "FLA",
        "LAK": "LA", "MIN": "MIN", "MTL": "MTL", "NSH": "NSH",
        "NJD": "NJ", "NYI": "NYI", "NYR": "NYR", "OTT": "OTT",
        "PHI": "PHI", "PIT": "PIT", "SEA": "SEA", "SJS": "SJ",
        "STL": "STL", "TBL": "TB", "TOR": "TOR", "UTA": "UTAH",
        "VAN": "VAN", "VGK": "VGK", "WPG": "WPG", "WSH": "WSH",
    },
    "NFL": {
        "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BUF": "BUF",
        "CAR": "CAR", "CHI": "CHI", "CIN": "CIN", "CLE": "CLE",
        "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GB": "GB",
        "HOU": "HOU", "IND": "IND", "JAX": "JAX", "KC": "KC",
        "LAC": "LAC", "LAR": "LAR", "LV": "LV", "MIA": "MIA",
        "MIN": "MIN", "NE": "NE", "NO": "NO", "NYG": "NYG",
        "NYJ": "NYJ", "PHI": "PHI", "PIT": "PIT", "SEA": "SEA",
        "SF": "SF", "TB": "TB", "TEN": "TEN", "WAS": "WSH",
    },
}
for league, abbreviations in ESPN_TEAM_ABBREVIATIONS.items():
    TEAM_LOOKUP.update({
        (league, re.sub(r"[^a-z0-9]+", " ", espn_abbreviation.lower()).strip()): abbreviation
        for abbreviation, espn_abbreviation in abbreviations.items()
        if espn_abbreviation != abbreviation
    })


class SportsScheduleError(ValueError):
    pass


def normalize_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _normalize_broadcast_name(value):
    normalized = re.sub(r"[^a-z0-9+]+", " ", str(value).lower()).strip()
    normalized = re.sub(r"\s*\+\s*", "+", normalized)
    aliases = {
        "nhl net": "nhl network",
        "nbc sports ba": "nbc sports bay area",
    }
    return aliases.get(normalized, normalized)


def resolve_team(league, value):
    return TEAM_LOOKUP.get((league.upper(), normalize_text(value)))


def team_id(league, abbreviation):
    return f"{league.upper()}:{abbreviation.upper()}"


def parse_team_id(value):
    league, separator, abbreviation = str(value).partition(":")
    league = league.upper()
    abbreviation = abbreviation.upper()
    if not separator or league not in TEAMS or abbreviation not in TEAMS[league]:
        return None
    return league, abbreviation


def team_display(league, abbreviation):
    return TEAMS[league][abbreviation][0]


def _espn_team_id(league, abbreviation):
    league = league.upper()
    abbreviation = abbreviation.upper()
    return ESPN_TEAM_ABBREVIATIONS[league][abbreviation].lower()


def team_aliases(league, abbreviation):
    names = TEAMS[league][abbreviation]
    return (abbreviation, *names)


def _validate_games(raw_games, favorites, sources, now):
    allowed = set(favorites)
    minimum = now - timedelta(hours=4)
    maximum = now + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS, hours=24)
    validated = {}
    for raw in raw_games:
        if not isinstance(raw, dict):
            continue
        favorite_id = raw.get("favorite_team", "")
        favorite = parse_team_id(favorite_id)
        if not favorite:
            continue
        favorite_id = team_id(*favorite)
        if favorite_id not in allowed:
            continue
        league, favorite_abbr = favorite
        home = resolve_team(league, raw.get("home_team", ""))
        away = resolve_team(league, raw.get("away_team", ""))
        if not home or not away or home == away or favorite_abbr not in {home, away}:
            continue
        start_value = raw.get("start_at", "")
        try:
            start = datetime.fromisoformat(str(start_value).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:
            continue
        scheduled_date = start.date().isoformat()
        start = start.astimezone(timezone.utc)
        if not minimum <= start <= maximum:
            continue
        channel_values = raw.get("broadcast_channels", [])
        if not isinstance(channel_values, list):
            channel_values = []
        channels = list(dict.fromkeys(
            str(value).strip() for value in channel_values
            if isinstance(value, str) and value.strip()
        ))
        identity = "|".join((league, scheduled_date, home, away))
        game_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        game = {
            "game_id": game_id,
            "league": league,
            "espn_event_id": str(raw.get("espn_event_id", "")),
            "favorite_teams": [team_id(league, favorite_abbr)],
            "recording_state": "scheduled",
            "home_team": home,
            "away_team": away,
            "home_name": team_display(league, home),
            "away_name": team_display(league, away),
            "start_at": start.isoformat(),
            "game_date": start.astimezone().date().isoformat(),
            "game_time": start.astimezone().strftime("%H:%M"),
            "broadcast_channels": channels,
            "sources": sources,
        }
        existing = validated.get(game_id)
        if existing:
            existing["favorite_teams"] = sorted(set(existing["favorite_teams"] + game["favorite_teams"]))
            existing["broadcast_channels"] = list(dict.fromkeys(
                existing["broadcast_channels"] + game["broadcast_channels"]
            ))
        else:
            validated[game_id] = game
    return list(validated.values())


def _event_competition(event):
    competitions = event.get("competitions")
    if isinstance(competitions, list) and competitions:
        return competitions[0] if isinstance(competitions[0], dict) else {}
    return event


def _team_abbreviation(competitor):
    team = competitor.get("team", {})
    if not isinstance(team, dict):
        return ""
    return team.get("abbreviation") or team.get("displayName") or team.get("name") or ""


def _schedule_events(data):
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        raise SportsScheduleError("ESPN schedule response did not contain an events list")
    return events


def _event_status(event):
    competition = _event_competition(event)
    status = competition.get("status", event.get("status", {}))
    status_type = status.get("type", {}) if isinstance(status, dict) else {}
    description = str(status_type.get("description", "")).lower()
    state = str(status_type.get("state", "")).lower()
    completed = (
        status_type.get("completed") is True
        or state in {"post", "completed"}
        or any(word in description for word in ("final", "complete", "cancel", "postpone"))
    )
    return {
        "event_id": str(event.get("id", "")),
        "state": state,
        "description": str(status_type.get("description", "")),
        "completed": completed,
        "period": status.get("period", 0) if isinstance(status, dict) else 0,
        "clock": status.get("displayClock", "") if isinstance(status, dict) else "",
    }


def _find_team_schedule_event(game, league, abbreviation, start):
    sport, espn_league = ESPN_LEAGUES[league]
    season = start.year
    if league == "NHL" and start.month >= 7:
        season += 1
    elif league == "NFL" and start.month <= 3:
        season -= 1
    url = ESPN_SCHEDULE_URL.format(
        sport=sport,
        league=espn_league,
        team=_espn_team_id(league, abbreviation),
    )
    response = requests.get(url, params={"season": season}, timeout=15)
    response.raise_for_status()
    expected_event_id = str(game.get("espn_event_id", "")).strip()
    for event in _schedule_events(response.json()):
        if not isinstance(event, dict):
            continue
        if expected_event_id and str(event.get("id", "")) == expected_event_id:
            return _event_status(event)
        competition = _event_competition(event)
        competitors = competition.get("competitors", [])
        if not isinstance(competitors, list):
            continue
        home = next(
            (resolve_team(league, _team_abbreviation(team)) for team in competitors
             if isinstance(team, dict) and team.get("homeAway") == "home"),
            "",
        )
        away = next(
            (resolve_team(league, _team_abbreviation(team)) for team in competitors
             if isinstance(team, dict) and team.get("homeAway") == "away"),
            "",
        )
        event_date = event.get("date") or competition.get("date", "")
        try:
            event_start = datetime.fromisoformat(str(event_date).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (
            home == game.get("home_team")
            and away == game.get("away_team")
            and event_start.date() == start.date()
        ):
            return _event_status(event)
    return None


def fetch_game_status(game):
    league = str(game.get("league", "")).upper()
    if league not in ESPN_LEAGUES:
        raise SportsScheduleError(f"Unsupported ESPN league: {league}")

    sport, espn_league = ESPN_LEAGUES[league]
    event_id = str(game.get("espn_event_id", "")).strip()
    favorites = game.get("favorite_teams", [])
    favorite = next(
        (parsed for value in favorites if (parsed := parse_team_id(value)) and parsed[0] == league),
        None,
    )
    if not favorite:
        raise SportsScheduleError(f"No {league} favorite team is available to find the ESPN event")
    _, abbreviation = favorite
    try:
        start = datetime.fromisoformat(str(game["start_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise SportsScheduleError("Game has no valid scheduled start time") from exc
    schedule_status = _find_team_schedule_event(game, league, abbreviation, start)
    if not schedule_status:
        raise SportsScheduleError(f"Unable to find the ESPN schedule event for game {game.get('game_id', '')}")
    if not event_id:
        return schedule_status

    response = requests.get(
        ESPN_EVENT_SUMMARY_URL.format(sport=sport, league=espn_league),
        params={"event": event_id},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    header = data.get("header", {}) if isinstance(data, dict) else {}
    if not isinstance(header, dict):
        raise SportsScheduleError("ESPN event response did not contain a valid header")
    summary_status = _event_status({**header, "id": event_id})
    summary_status["summary_completed"] = summary_status["completed"]
    summary_status["completed"] = (
        summary_status["completed"] and schedule_status["completed"]
    )
    summary_status["schedule_completed"] = schedule_status["completed"]
    return summary_status


def fetch_favorite_schedules(favorites, now=None):
    selected = sorted({
        team_id(*parsed)
        for favorite in favorites
        if (parsed := parse_team_id(favorite))
    })
    if not selected:
        return []
    now = now or datetime.now(timezone.utc)
    raw_games = []
    for favorite in selected:
        league, abbreviation = parse_team_id(favorite)
        sport, espn_league = ESPN_LEAGUES[league]
        season = now.year
        if league == "NHL" and now.month >= 7:
            season += 1
        elif league == "NFL" and now.month <= 3:
            season -= 1
        url = ESPN_SCHEDULE_URL.format(
            sport=sport,
            league=espn_league,
            team=_espn_team_id(league, abbreviation),
        )
        response = requests.get(url, params={"season": season}, timeout=20)
        response.raise_for_status()
        for event in _schedule_events(response.json()):
            if not isinstance(event, dict):
                continue
            competition = _event_competition(event)
            competitors = competition.get("competitors", [])
            if not isinstance(competitors, list):
                continue
            home = next(
                (_team_abbreviation(team) for team in competitors
                 if isinstance(team, dict) and team.get("homeAway") == "home"),
                "",
            )
            away = next(
                (_team_abbreviation(team) for team in competitors
                 if isinstance(team, dict) and team.get("homeAway") == "away"),
                "",
            )
            if not home or not away:
                continue
            status = competition.get("status", event.get("status", {}))
            status_type = status.get("type", {}) if isinstance(status, dict) else {}
            description = str(status_type.get("description", "")).lower()
            if (
                status_type.get("completed") is True
                or str(status_type.get("state", "")).lower() in {"post", "completed"}
                or any(state in description for state in ("cancel", "postpone", "final", "complete"))
            ):
                continue
            broadcasts = competition.get("broadcasts", [])
            broadcast_channels = []
            if isinstance(broadcasts, list):
                for broadcast in broadcasts:
                    if not isinstance(broadcast, dict):
                        continue
                    media = broadcast.get("media")
                    if isinstance(media, dict) and media.get("shortName"):
                        broadcast_channels.append(str(media["shortName"]))
            raw_games.append({
                "favorite_team": favorite,
                "espn_event_id": str(event.get("id", "")),
                "home_team": home,
                "away_team": away,
                "start_at": event.get("date") or competition.get("date", ""),
                "broadcast_channels": broadcast_channels,
            })
    games = _validate_games(raw_games, selected, [], now)
    logging.info("ESPN returned %d scheduled games for %d favorite teams", len(games), len(selected))
    return games


def _item_text(item):
    fields = (
        "fixture_name", "channel_name", "name", "title", "event", "event_name",
        "matchup", "game", "game_name", "home_team", "away_team", "homeTeam", "awayTeam",
        "home", "away", "league", "sport",
    )
    parts = []
    for field in fields:
        value = item.get(field)
        if isinstance(value, dict):
            parts.extend(str(child) for child in value.values() if isinstance(child, (str, int)))
        elif isinstance(value, (str, int)):
            parts.append(str(value))
    return " ".join(parts)


def _contains_alias(text, aliases):
    normalized_text = normalize_text(text)
    text_words = normalized_text.split()
    for alias in aliases:
        words = normalize_text(alias).split()
        if not words:
            continue
        pattern = r"(?<![a-z0-9])" + r"[\W_]*".join(map(re.escape, words)) + r"(?![a-z0-9])"
        if re.search(pattern, str(text), re.IGNORECASE):
            return True
        compact_alias = "".join(words)
        if len(compact_alias) < 7:
            continue
        for start in range(len(text_words)):
            for word_count in range(1, min(len(words), len(text_words) - start) + 1):
                candidate = "".join(text_words[start:start + word_count])
                if (
                    len(candidate) >= 7
                    and SequenceMatcher(None, compact_alias, candidate).ratio() >= 0.9
                ):
                    return True
    return False


def _has_fixture_id(item):
    value = item.get("fixture_id")
    return value is not None and bool(str(value).strip())


def _has_channel_id(item):
    value = item.get("channel_id")
    return value is not None and bool(str(value).strip())


def _resolve_feed_team(league, value):
    if isinstance(value, dict):
        for key in ("abbrev", "abbreviation", "name", "teamName", "team_name"):
            if value.get(key):
                result = resolve_team(league, value[key])
                if result:
                    return result
        return None
    return resolve_team(league, value)


def find_live_game(game, feed, excluded_urls=None):
    items = feed.get("games", [])
    if not isinstance(items, list):
        return None
    excluded_urls = excluded_urls or set()
    home_aliases = team_aliases(game["league"], game["home_team"])
    away_aliases = team_aliases(game["league"], game["away_team"])
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _item_text(item)
        home_value = item.get("home_team") or item.get("homeTeam")
        away_value = item.get("away_team") or item.get("awayTeam")
        structured_match = (
            home_value and away_value
            and _resolve_feed_team(game["league"], home_value) == game["home_team"]
            and _resolve_feed_team(game["league"], away_value) == game["away_team"]
        )
        if (
            (structured_match or (
                _contains_alias(text, home_aliases) and _contains_alias(text, away_aliases)
            ))
            and _has_fixture_id(item)
            and item.get("secure_url")
            and item.get("secure_url") not in excluded_urls
        ):
            return item
    return None


def find_fixture_by_id(feed, fixture_id, excluded_urls=None):
    target = str(fixture_id or "").strip()
    items = feed.get("games", [])
    if not target or not isinstance(items, list):
        return None
    excluded_urls = excluded_urls or set()
    for item in items:
        if (
            isinstance(item, dict)
            and str(item.get("fixture_id", "")) == target
            and item.get("secure_url")
            and item["secure_url"] not in excluded_urls
        ):
            return item
    return None


def find_broadcast_channel(game, feed, excluded_urls=None):
    channels = game.get("broadcast_channels", [])
    excluded_urls = excluded_urls or set()
    candidates = []
    for category, items in feed.items():
        if "channel" not in str(category).lower() or not isinstance(items, list):
            continue
        for item in items:
            if (
                not isinstance(item, dict)
                or not item.get("secure_url")
                or item.get("secure_url") in excluded_urls
                or not _has_channel_id(item)
            ):
                continue
            name = _normalize_broadcast_name(
                item.get("channel_name") or item.get("fixture_name") or ""
            )
            if not name:
                continue
            for index, broadcast in enumerate(channels):
                target = _normalize_broadcast_name(broadcast)
                if name == target or name.startswith(f"{target} "):
                    candidates.append((index, 0 if name == target else 1, item))
                    break
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[:2])[2]


def feed_stream_by_id(feed, stream_id):
    target = str(stream_id)
    for items in feed.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if (isinstance(item, dict)
                    and target in {str(item.get("fixture_id", "")), str(item.get("channel_id", ""))}):
                return item
    return None
