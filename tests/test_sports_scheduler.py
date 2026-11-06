import sys
import json
import threading
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".venv"))

import app
import sports_scheduler


class SportsPageTests(unittest.TestCase):
    def setUp(self):
        app.team_schedule_refresh_requested.clear()
        self.client = app.app.test_client()

    def tearDown(self):
        app.team_schedule_refresh_requested.clear()

    def test_favorites_have_a_separate_page_and_are_absent_from_settings(self):
        sports_page = self.client.get("/favorites")
        settings_page = self.client.get("/settings")

        self.assertEqual(sports_page.status_code, 200)
        self.assertIn(b"FAVORITE-TEAM SCHEDULE", sports_page.data)
        self.assertEqual(settings_page.status_code, 200)
        self.assertNotIn(b"FAVORITE SPORTS TEAMS", settings_page.data)

    def test_saving_favorites_uses_favorites_page_and_requests_schedule_refresh(self):
        current = {
            "paid_api": "https://example.invalid/feed",
            "save_path": "/recordings",
            "sports_favorites": [],
        }
        with (
            patch.object(app, "load_settings", return_value=current),
            patch.object(app, "sports_favorites", return_value=[]),
            patch.object(app, "save_app_settings") as save_settings,
        ):
            response = self.client.post(
                "/favorites",
                data={"action": "refresh", "sports_favorites": ["NHL:BOS", "NFL:BUF"]},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(current["sports_favorites"], ["NFL:BUF", "NHL:BOS"])
        save_settings.assert_called_once_with(current)
        self.assertTrue(app.team_schedule_refresh_requested.is_set())

    def test_saving_favorites_persists_and_reloads_them(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_file = Path(directory) / "settings.json"
            with (
                patch.object(app, "SETTINGS_FILE", str(settings_file)),
                patch.object(app, "SETTINGS_TEXT_FILE", str(Path(directory) / "settings.txt")),
            ):
                response = self.client.post(
                    "/favorites",
                    data={"action": "refresh", "sports_favorites": ["NHL:BOS", "NFL:BUF"]},
                )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(json.loads(settings_file.read_text())["sports_favorites"],
                                 ["NFL:BUF", "NHL:BOS"])
                self.assertEqual(
                    app.load_settings()["sports_favorites"],
                    ["NFL:BUF", "NHL:BOS"],
                )

                page = self.client.get("/favorites")

        self.assertEqual(page.status_code, 200)
        favorites_json = (
            page.data.split(b"let favoriteSportsTeams = ", 1)[1]
            .split(b";", 1)[0]
            .decode()
        )
        self.assertEqual(json.loads(favorites_json), ["NFL:BUF", "NHL:BOS"])

    def test_settings_post_preserves_favorites(self):
        current = {
            "paid_api": "https://example.invalid/feed",
            "save_path": "/recordings",
            "sports_favorites": ["NHL:BOS"],
        }
        with (
            patch.object(app, "load_settings", return_value=current),
            patch.object(app, "save_app_settings") as save_settings,
        ):
            response = self.client.post(
                "/settings",
                data={"paid_api": "https://example.invalid/new-feed", "save_path": "/new-recordings"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(current["sports_favorites"], ["NHL:BOS"])
        save_settings.assert_called_once_with(current)
        self.assertFalse(app.team_schedule_refresh_requested.is_set())

    def test_recent_refresh_attempt_is_not_bypassed_by_force(self):
        current = datetime(2026, 10, 23, 23, 0, tzinfo=timezone.utc)
        cached = {
            "updated_at": "",
            "refresh_attempt_at": (current - timedelta(minutes=5)).isoformat(),
            "refresh_attempt_favorites": ["NHL:BOS"],
            "favorites": ["NHL:BOS"],
            "games": [],
        }

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return current

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "sports_favorites", return_value=["NHL:BOS"]),
            patch.object(app, "load_team_schedule", return_value=cached),
            patch.object(app.sports, "fetch_favorite_schedules") as fetch_schedules,
        ):
            app.refresh_team_schedule(force=True)

        fetch_schedules.assert_not_called()

    def test_monitor_startup_uses_normal_refresh_policy(self):
        with patch.object(app, "refresh_team_schedule", side_effect=RuntimeError("stop")) as refresh:
            with self.assertRaisesRegex(RuntimeError, "stop"):
                app.run_sports_monitor_loop()

        refresh.assert_called_once_with()

    def test_settings_page_does_not_require_an_ai_key(self):
        with patch.object(
            app, "load_settings",
            return_value={"paid_api": "https://example.invalid/feed", "save_path": "/recordings"},
        ):
            response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"GROQ API KEY", response.data)
        self.assertNotIn(b'name="groq_key"', response.data)
        self.assertNotIn(b"Gemini", response.data)

    def test_dashboard_shows_next_seven_days_with_explicit_visitor_home_order(self):
        now = datetime(2026, 10, 29, 17, 0, tzinfo=timezone.utc)
        game_start = now + timedelta(days=2, hours=3)
        game = {
            "game_id": "game-1",
            "league": "NHL",
            "favorite_teams": ["NHL:BOS"],
            "start_at": game_start.isoformat(),
            "away_name": "Visitor Team",
            "home_name": "Home Team",
            "game_date": "incorrect date",
            "game_time": "incorrect time",
            "recording_state": "scheduled",
        }
        outside_window = {
            **game,
            "game_id": "game-later",
            "start_at": (now + timedelta(days=8)).isoformat(),
            "away_name": "Later Visitor",
            "home_name": "Later Home",
        }
        settings = {"paid_api": "https://example.invalid/feed", "save_path": "/missing"}
        response_data = {"games": [], "channels": []}

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "load_settings", return_value=settings),
            patch.object(app, "load_schedules", return_value=[]),
            patch.object(app, "sports_favorites", return_value=["NHL:BOS"]),
            patch.object(
                app, "load_team_schedule",
                return_value={"games": [outside_window, game]},
            ),
            patch.object(app.requests, "get", return_value=SimpleNamespace(json=lambda: response_data)),
            patch.object(app, "active_recordings", {}),
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPCOMING FAVORITE-TEAM GAMES", response.data)
        self.assertIn(b"Visitor Team @ Home Team", response.data)
        self.assertNotIn(b"Later Visitor @ Later Home", response.data)
        self.assertIn(game_start.astimezone().strftime("%Y-%m-%d").encode(), response.data)
        self.assertIn(game_start.astimezone().strftime("%I:%M %p").encode(), response.data)


class VideoDurationTests(unittest.TestCase):
    def test_save_json_replaces_file_with_complete_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_text('{"old": true}', encoding="utf-8")

            app.save_json(path, {"games": [{"game_id": "game-1"}]})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"games": [{"game_id": "game-1"}]},
            )
            self.assertEqual([entry.name for entry in Path(directory).iterdir()], ["schedule.json"])

    def test_video_duration_uses_ffprobe_and_formats_hours_and_minutes(self):
        with patch.object(
            app.subprocess, "run",
            return_value=SimpleNamespace(stdout="8340.25\n"),
        ) as run:
            duration = app.video_duration_display("/recordings/game.mp4")

        self.assertEqual(duration, "2 HR 19 MIN")
        self.assertEqual(run.call_args.args[0][0], "ffprobe")
        self.assertIn("/recordings/game.mp4", run.call_args.args[0])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_invalid_ffprobe_duration_is_not_reported_as_a_valid_estimate(self):
        with patch.object(
            app.subprocess, "run",
            return_value=SimpleNamespace(stdout="N/A"),
        ):
            with self.assertRaisesRegex(ValueError, "invalid duration"):
                app.video_duration_display("/recordings/game.mp4")


class SportsFeedSelectionTests(unittest.TestCase):
    def setUp(self):
        self.game = {
            "league": "NHL",
            "home_team": "BOS",
            "away_team": "NYR",
            "broadcast_channels": ["ESPN"],
        }

    def test_games_feed_requires_fixture_id(self):
        feed = {
            "games": [
                {
                    "fixture_name": "New York Rangers at Boston Bruins",
                    "channel_id": "wrong-id",
                    "secure_url": "https://example.invalid/channel",
                },
                {
                    "fixture_name": "New York Rangers at Boston Bruins",
                    "fixture_id": "fixture-id",
                    "secure_url": "https://example.invalid/fixture",
                },
            ]
        }

        result = sports_scheduler.find_live_game(self.game, feed)

        self.assertEqual(result["fixture_id"], "fixture-id")

    def test_games_feed_matches_team_names_joined_without_spaces(self):
        game = {
            "league": "NHL",
            "home_team": "STL",
            "away_team": "BOS",
            "broadcast_channels": ["ESPN+", "Blues App", "NESN", "BN+"],
        }
        fixture = {
            "fixture_name": "St.LouisBlues @ BostonBruins",
            "fixture_id": "bruins-blues-fixture",
            "secure_url": "https://example.invalid/game",
        }

        result = sports_scheduler.find_live_game(game, {"games": [fixture]})

        self.assertIs(result, fixture)

    def test_games_feed_matches_single_character_team_name_typo(self):
        game = {
            "league": "NHL",
            "home_team": "NYI",
            "away_team": "CAR",
        }
        fixture = {
            "fixture_name": "slanders @ CarolinaHurricanes",
            "fixture_id": "islanders-hurricanes",
            "secure_url": "https://example.invalid/game",
        }

        result = sports_scheduler.find_live_game(game, {"games": [fixture]})

        self.assertIs(result, fixture)

    def test_games_feed_skips_expired_stream_url_when_refreshing(self):
        game = {
            "league": "NHL",
            "home_team": "BOS",
            "away_team": "PIT",
        }
        expired = {
            "fixture_name": "Pittsburgh-Penguins @ Boston-Bruins",
            "fixture_id": "fixture-old",
            "secure_url": "https://example.invalid/expired.m3u8",
        }
        refreshed = {
            **expired,
            "fixture_id": "fixture-new",
            "secure_url": "https://example.invalid/refreshed.m3u8",
        }

        result = sports_scheduler.find_live_game(
            game,
            {"games": [expired, refreshed]},
            excluded_urls={expired["secure_url"]},
        )

        self.assertIs(result, refreshed)

    def test_fixture_refresh_requires_same_fixture_id(self):
        fixture = {
            "fixture_id": "fixture-123",
            "secure_url": "https://example.invalid/refreshed.m3u8",
        }
        unrelated_fixture = {
            "fixture_id": "fixture-456",
            "secure_url": "https://example.invalid/other.m3u8",
        }

        result = sports_scheduler.find_fixture_by_id(
            {"games": [unrelated_fixture, fixture]}, "fixture-123"
        )

        self.assertIs(result, fixture)

    def test_channel_fallback_requires_channel_id(self):
        feed = {
            "channels": [
                {
                    "channel_name": "ESPN",
                    "fixture_id": "wrong-id",
                    "secure_url": "https://example.invalid/fixture",
                },
                {
                    "channel_name": "ESPN",
                    "channel_id": "channel-id",
                    "secure_url": "https://example.invalid/channel",
                },
            ]
        }

        result = sports_scheduler.find_broadcast_channel(self.game, feed)

        self.assertEqual(result["channel_id"], "channel-id")

    def test_sports_recording_starts_with_fixture_when_both_sources_match(self):
        game = {
            "game_id": "game-1",
            "league": "NHL",
            "home_team": "BOS",
            "away_team": "NYR",
            "home_name": "Boston Bruins",
            "away_name": "New York Rangers",
            "broadcast_channels": ["NHL Network"],
            "recording_state": "scheduled",
        }
        fixture = {
            "fixture_name": "Boston Bruins at New York Rangers",
            "fixture_id": "fixture-123",
            "secure_url": "https://example.invalid/fixture.m3u8",
        }
        channel = {
            "channel_name": "NHL Network",
            "channel_id": "nhl-network",
            "secure_url": "https://example.invalid/nhl-network.m3u8",
        }

        with (
            patch.object(app, "load_team_schedule", return_value={"games": [game]}),
            patch.object(app, "get_nufu_feed", return_value={
                "games": [fixture],
                "channels": [channel],
            }),
            patch.object(app, "set_sports_recording_data"),
            patch.object(app.threading, "Thread") as thread,
        ):
            app.start_sports_recording("game-1")

        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["args"][0], fixture["secure_url"])
        self.assertEqual(thread.call_args.kwargs["kwargs"]["sports_source"], "games")
        self.assertEqual(thread.call_args.kwargs["kwargs"]["selected_feed_id"], "fixture-123")

    def test_sports_recording_uses_espn_broadcast_channel_when_fixture_is_unavailable(self):
        game = {
            "game_id": "game-2",
            "league": "NHL",
            "home_team": "BOS",
            "away_team": "NYR",
            "home_name": "Boston Bruins",
            "away_name": "New York Rangers",
            "broadcast_channels": ["NHL Network"],
            "recording_state": "scheduled",
        }
        channel = {
            "channel_name": "NHL Network",
            "channel_id": "nhl-network",
            "secure_url": "https://example.invalid/nhl-network.m3u8",
        }

        with (
            patch.object(app, "load_team_schedule", return_value={"games": [game]}),
            patch.object(app, "get_nufu_feed", return_value={"games": [], "channels": [channel]}),
            patch.object(app, "set_sports_recording_data"),
            patch.object(app.threading, "Thread") as thread,
        ):
            app.start_sports_recording("game-2")

        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["args"][0], channel["secure_url"])
        self.assertEqual(thread.call_args.kwargs["kwargs"]["sports_source"], "channels")
        self.assertEqual(thread.call_args.kwargs["kwargs"]["selected_feed_id"], "nhl-network")

    def test_espn_plus_broadcast_does_not_match_espn_channel(self):
        game = {**self.game, "broadcast_channels": ["ESPN+"]}
        feed = {
            "channels": [
                {
                    "channel_name": "ESPN",
                    "channel_id": "espn",
                    "secure_url": "https://example.invalid/espn",
                },
                {
                    "channel_name": "ESPN+",
                    "channel_id": "espn-plus",
                    "secure_url": "https://example.invalid/espn-plus",
                },
            ]
        }

        result = sports_scheduler.find_broadcast_channel(game, feed)

        self.assertEqual(result["channel_id"], "espn-plus")

    def test_espn_channel_does_not_match_espn_plus_broadcast(self):
        game = {**self.game, "broadcast_channels": ["ESPN"]}
        feed = {
            "channels": [
                {
                    "channel_name": "ESPN+",
                    "channel_id": "espn-plus",
                    "secure_url": "https://example.invalid/espn-plus",
                },
            ]
        }

        self.assertIsNone(sports_scheduler.find_broadcast_channel(game, feed))

    def test_game_identity_survives_start_time_change_on_same_date(self):
        now = datetime(2026, 10, 23, tzinfo=timezone.utc)
        favorites = ["NHL:BOS"]
        sources = [{"url": "https://example.invalid/schedule", "title": "Schedule"}]
        initial = {
            "favorite_team": "NHL:BOS",
            "home_team": "BOS",
            "away_team": "NYR",
            "start_at": "2026-10-24T19:00:00-04:00",
            "broadcast_channels": ["ESPN"],
        }
        moved = {**initial, "start_at": "2026-10-24T20:00:00-04:00"}

        first = sports_scheduler._validate_games([initial], favorites, sources, now)[0]
        second = sports_scheduler._validate_games([moved], favorites, sources, now)[0]

        self.assertEqual(first["game_id"], second["game_id"])
        self.assertNotEqual(first["start_at"], second["start_at"])

    def test_espn_abbreviation_table_covers_every_supported_team(self):
        self.assertEqual(
            {
                league: set(abbreviations)
                for league, abbreviations in sports_scheduler.ESPN_TEAM_ABBREVIATIONS.items()
            },
            {league: set(teams) for league, teams in sports_scheduler.TEAMS.items()},
        )
        for league, abbreviations in sports_scheduler.ESPN_TEAM_ABBREVIATIONS.items():
            for team_abbreviation, espn_abbreviation in abbreviations.items():
                self.assertEqual(
                    sports_scheduler.resolve_team(league, espn_abbreviation),
                    team_abbreviation,
                    f"{league} ESPN abbreviation {espn_abbreviation} should map to {team_abbreviation}",
                )
                self.assertEqual(
                    sports_scheduler._espn_team_id(league, team_abbreviation),
                    espn_abbreviation.lower(),
                )

    def test_san_jose_schedule_uses_espn_team_id_and_resolves_sj_abbreviation(self):
        response = SimpleNamespace(
            json=lambda: {"events": [{
                "id": "san-jose-event",
                "date": "2026-11-04T23:00:00Z",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "SJ"}},
                        {"homeAway": "away", "team": {"abbreviation": "LAK"}},
                    ],
                    "status": {"type": {"state": "pre", "description": "Scheduled"}},
                }],
            }]},
            raise_for_status=unittest.mock.Mock(),
        )
        now = datetime(2026, 11, 3, tzinfo=timezone.utc)

        with patch.object(sports_scheduler.requests, "get", return_value=response) as get:
            games = sports_scheduler.fetch_favorite_schedules(["NHL:SJS"], now=now)

        self.assertEqual(
            get.call_args.args[0],
            "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams/sj/schedule",
        )
        self.assertEqual(get.call_args.kwargs["params"], {"season": 2027})
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["home_team"], "SJS")
        self.assertEqual(games[0]["away_team"], "LAK")
        self.assertEqual(games[0]["favorite_teams"], ["NHL:SJS"])

    def test_schedules_are_fetched_from_espn_for_selected_teams(self):
        def event(home, away, *, completed=False, broadcasts=None):
            return {
                "id": f"{home}-{away}",
                "date": "2026-10-24T23:00:00Z",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": home}},
                        {"homeAway": "away", "team": {"abbreviation": away}},
                    ],
                    "status": {"type": {
                        "completed": completed,
                        "state": "post" if completed else "pre",
                        "description": "Final" if completed else "Scheduled",
                    }},
                    "broadcasts": broadcasts or [],
                }],
            }

        responses = [
            SimpleNamespace(
                json=lambda: {"events": [event("BUF", "NE", broadcasts=[
                    {"media": {"shortName": "CBS"}},
                    {"media": {"shortName": "ESPN"}},
                ])]},
                raise_for_status=unittest.mock.Mock(),
            ),
            SimpleNamespace(
                json=lambda: {"events": [
                    event("BOS", "NYR", completed=True),
                    event("BOS", "NYR", broadcasts=[
                        {"media": {"shortName": "ESPN+"},
                         "market": {"type": "National"}},
                    ]),
                ]},
                raise_for_status=unittest.mock.Mock(),
            ),
        ]
        now = datetime(2026, 10, 23, tzinfo=timezone.utc)

        with patch.object(sports_scheduler.requests, "get", side_effect=responses) as get:
            games = sports_scheduler.fetch_favorite_schedules(
                ["NHL:BOS", "NFL:NE"], now=now
            )

        self.assertEqual(get.call_count, 2)
        requested_urls = [call.args[0] for call in get.call_args_list]
        self.assertEqual(requested_urls, [
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/ne/schedule",
            "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams/bos/schedule",
        ])
        self.assertEqual(
            [call.kwargs["params"]["season"] for call in get.call_args_list],
            [2026, 2027],
        )
        self.assertEqual(
            [game["favorite_teams"] for game in games],
            [["NFL:NE"], ["NHL:BOS"]],
        )
        self.assertEqual(games[0]["broadcast_channels"], ["CBS", "ESPN"])
        self.assertEqual(games[1]["broadcast_channels"], ["ESPN+"])
        self.assertEqual([game["espn_event_id"] for game in games], ["BUF-NE", "BOS-NYR"])
        self.assertTrue(all(not game["sources"] for game in games))
        self.assertTrue(all("fixture_id" not in game for game in games))
        self.assertTrue(all(call.kwargs["timeout"] == 20 for call in get.call_args_list))


class SportsStatusTests(unittest.TestCase):
    def test_event_status_is_fetched_by_espn_event_id(self):
        schedule_response = SimpleNamespace(
            json=lambda: {"events": [{
                "id": "401-event",
                "date": "2026-11-02T23:00:00Z",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "BOS"}},
                        {"homeAway": "away", "team": {"abbreviation": "NYR"}},
                    ],
                    "status": {
                        "type": {"state": "in", "description": "In Progress", "completed": False},
                        "period": 3,
                        "displayClock": "04:12",
                    },
                }],
            }]},
            raise_for_status=unittest.mock.Mock(),
        )
        summary_response = SimpleNamespace(
            json=lambda: {"header": {
                "competitions": [{
                    "status": {
                        "type": {"state": "in", "description": "In Progress", "completed": False},
                        "period": 3,
                        "displayClock": "04:12",
                    },
                }],
            }},
            raise_for_status=unittest.mock.Mock(),
        )
        game = {
            "league": "NHL",
            "espn_event_id": "401-event",
            "favorite_teams": ["NHL:BOS"],
            "home_team": "BOS",
            "away_team": "NYR",
            "start_at": "2026-11-02T23:00:00+00:00",
        }

        with patch.object(
            sports_scheduler.requests, "get",
            side_effect=[schedule_response, summary_response],
        ) as get:
            status = sports_scheduler.fetch_game_status(game)

        self.assertEqual(status["event_id"], "401-event")
        self.assertFalse(status["completed"])
        self.assertFalse(status["schedule_completed"])
        self.assertEqual(status["period"], 3)
        self.assertEqual(status["clock"], "04:12")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            get.call_args_list[-1].args[0],
            "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary",
        )
        self.assertEqual(get.call_args_list[-1].kwargs["params"], {"event": "401-event"})

    def test_summary_final_is_not_enough_when_team_schedule_is_still_live(self):
        schedule_response = SimpleNamespace(
            json=lambda: {"events": [{
                "id": "401-event",
                "date": "2026-11-02T23:00:00Z",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "BOS"}},
                        {"homeAway": "away", "team": {"abbreviation": "NYR"}},
                    ],
                    "status": {
                        "type": {"state": "in", "description": "In Progress", "completed": False},
                        "period": 3,
                        "displayClock": "04:12",
                    },
                }],
            }]},
            raise_for_status=unittest.mock.Mock(),
        )
        summary_response = SimpleNamespace(
            json=lambda: {"header": {
                "competitions": [{
                    "status": {
                        "type": {"state": "post", "description": "Final", "completed": True},
                        "period": 0,
                        "displayClock": "",
                    },
                }],
            }},
            raise_for_status=unittest.mock.Mock(),
        )
        game = {
            "league": "NHL",
            "espn_event_id": "401-event",
            "favorite_teams": ["NHL:BOS"],
            "home_team": "BOS",
            "away_team": "NYR",
            "start_at": "2026-11-02T23:00:00+00:00",
        }

        with patch.object(
            sports_scheduler.requests, "get",
            side_effect=[schedule_response, summary_response],
        ):
            status = sports_scheduler.fetch_game_status(game)

        self.assertTrue(status["summary_completed"])
        self.assertFalse(status["schedule_completed"])
        self.assertFalse(status["completed"])

    def test_missing_event_id_is_resolved_from_team_schedule(self):
        response = SimpleNamespace(
            json=lambda: {"events": [{
                "id": "401-legacy-event",
                "date": "2026-11-02T23:00:00Z",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "BOS"}},
                        {"homeAway": "away", "team": {"abbreviation": "NYR"}},
                    ],
                    "status": {
                        "type": {"state": "in", "description": "In Progress"},
                        "period": 2,
                        "displayClock": "10:00",
                    },
                }],
            }]},
            raise_for_status=unittest.mock.Mock(),
        )
        game = {
            "game_id": "game-1",
            "league": "NHL",
            "favorite_teams": ["NHL:BOS"],
            "home_team": "BOS",
            "away_team": "NYR",
            "start_at": "2026-11-02T23:00:00+00:00",
        }

        with patch.object(sports_scheduler.requests, "get", return_value=response) as get:
            status = sports_scheduler.fetch_game_status(game)

        self.assertEqual(status["event_id"], "401-legacy-event")
        self.assertEqual(status["period"], 2)
        self.assertEqual(get.call_args.args[0], (
            "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams/bos/schedule"
        ))

    def test_san_jose_status_lookup_uses_espn_team_id(self):
        response = SimpleNamespace(
            json=lambda: {"events": [{
                "id": "san-jose-event",
                "date": "2026-11-04T23:00:00Z",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "SJ"}},
                        {"homeAway": "away", "team": {"abbreviation": "LAK"}},
                    ],
                    "status": {
                        "type": {"state": "in", "description": "In Progress"},
                        "period": 2,
                        "displayClock": "10:00",
                    },
                }],
            }]},
            raise_for_status=unittest.mock.Mock(),
        )
        game = {
            "game_id": "game-1",
            "league": "NHL",
            "favorite_teams": ["NHL:SJS"],
            "home_team": "SJS",
            "away_team": "LAK",
            "start_at": "2026-11-04T23:00:00+00:00",
        }

        with patch.object(sports_scheduler.requests, "get", return_value=response) as get:
            status = sports_scheduler.fetch_game_status(game)

        self.assertEqual(status["event_id"], "san-jose-event")
        self.assertEqual(
            get.call_args.args[0],
            "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams/sj/schedule",
        )

    def test_active_sports_recording_stops_when_espn_reports_final(self):
        started = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
        now = started + timedelta(hours=3)
        proc = SimpleNamespace(send_signal=unittest.mock.Mock())
        recording = {
            "proc": proc,
            "started_at": started,
            "stop_reason": "",
            "last_status_check": None,
            "last_game_status": {},
        }
        game = {
            "game_id": "game-1",
            "league": "NHL",
            "espn_event_id": "401-event",
            "recording_state": "recording",
        }
        app.active_recordings["sports-game-1"] = recording
        try:
            with (
                patch.object(app, "load_team_schedule", return_value={"games": [game]}),
                patch.object(app, "set_sports_recording_data") as save_game,
                patch.object(
                    app.sports, "fetch_game_status",
                    return_value={"event_id": "401-event", "completed": True, "period": 3},
                ),
            ):
                app.check_active_sports_recordings(now)

            self.assertEqual(recording["stop_reason"], "espn_final")
            proc.send_signal.assert_called_once_with(app.signal.SIGINT)
            self.assertIn("last_game_status", save_game.call_args.args[1])
        finally:
            app.active_recordings.pop("sports-game-1", None)

    def test_active_sports_recording_polls_less_often_until_late_game(self):
        started = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
        recording = {
            "proc": SimpleNamespace(send_signal=unittest.mock.Mock()),
            "started_at": started,
            "stop_reason": "",
            "last_status_check": started,
            "last_game_status": {"period": 1, "completed": False},
        }
        game = {"game_id": "game-1", "league": "NHL", "recording_state": "recording"}
        app.active_recordings["sports-game-1"] = recording
        try:
            with (
                patch.object(app, "load_team_schedule", return_value={"games": [game]}),
                patch.object(app, "set_sports_recording_data"),
                patch.object(
                    app.sports, "fetch_game_status",
                    return_value={"event_id": "401-event", "completed": False, "period": 1},
                ) as fetch_status,
            ):
                app.check_active_sports_recordings(started + timedelta(minutes=2))
                fetch_status.assert_not_called()

                recording["last_game_status"] = {"period": 3, "completed": False}
                app.check_active_sports_recordings(started + timedelta(minutes=2))
                fetch_status.assert_called_once_with(game)
        finally:
            app.active_recordings.pop("sports-game-1", None)

    def test_active_sports_recording_stops_at_maximum_runtime_without_espn(self):
        started = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
        proc = SimpleNamespace(send_signal=unittest.mock.Mock())
        recording = {
            "proc": proc,
            "started_at": started,
            "stop_reason": "",
            "last_status_check": None,
            "last_game_status": {},
        }
        game = {"game_id": "game-1", "league": "NFL", "recording_state": "recording"}
        app.active_recordings["sports-game-1"] = recording
        try:
            with (
                patch.object(app, "load_team_schedule", return_value={"games": [game]}),
                patch.object(app.sports, "fetch_game_status") as fetch_status,
            ):
                app.check_active_sports_recordings(started + timedelta(hours=5))

            self.assertEqual(recording["stop_reason"], "maximum_runtime")
            proc.send_signal.assert_called_once_with(app.signal.SIGINT)
            fetch_status.assert_not_called()
        finally:
            app.active_recordings.pop("sports-game-1", None)


class SportsRecordingFallbackTests(unittest.TestCase):
    def test_sports_recording_restarts_after_early_clean_ffmpeg_exit(self):
        first_process = SimpleNamespace(stderr=[], returncode=0, wait=unittest.mock.Mock())
        second_process = SimpleNamespace(stderr=[], returncode=0, wait=unittest.mock.Mock())
        game = {
            "game_id": "game-1",
            "league": "NHL",
            "home_team": "BOS",
            "away_team": "NYR",
            "broadcast_channels": ["NHL Network"],
        }
        refreshed_fixture = {
            "fixture_id": "fixture-123",
            "fixture_name": "Rangers at Bruins",
            "secure_url": "https://example.invalid/refreshed.m3u8",
        }
        app.active_recordings.pop("sports-game-1", None)

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(app, "settings", {"save_path": directory}),
                patch.object(
                    app.subprocess, "Popen",
                    side_effect=[first_process, second_process],
                ) as popen,
                patch.object(app, "get_nufu_feed", return_value={"games": [refreshed_fixture]}),
                patch.object(
                    app.sports, "find_live_game", return_value=refreshed_fixture,
                ),
                patch.object(app, "set_sports_recording_data") as save_game,
                patch.object(app, "load_schedules", return_value=[]),
                patch.object(app, "log_ffmpeg"),
                patch.object(app.time, "monotonic", side_effect=[0, 0, 10, 10, 10, 3600]),
                patch.object(app.time, "sleep") as sleep,
            ):
                app.record_stream(
                    "https://example.invalid/expired.m3u8",
                    "Rangers at Bruins",
                    3600,
                    "sports-game-1",
                    "NHL",
                    sports_game=game,
                    sports_source="games",
                    selected_feed_id="fixture-123",
                )

        self.assertEqual(popen.call_count, 2)
        input_urls = [
            call.args[0][call.args[0].index("-i") + 1]
            for call in popen.call_args_list
        ]
        self.assertEqual(
            input_urls,
            [
                "https://example.invalid/expired.m3u8",
                refreshed_fixture["secure_url"],
            ],
        )
        output_paths = [
            call.args[0][-1]
            for call in popen.call_args_list
        ]
        self.assertNotEqual(output_paths[0], output_paths[1])
        self.assertTrue(output_paths[1].endswith("_part02.mp4"))
        sleep.assert_called_once_with(15)
        self.assertEqual(save_game.call_args_list[-1].args[1]["recording_state"], "completed")
        self.assertNotIn("sports-game-1", app.active_recordings)

    def test_fixture_recording_retries_on_broadcast_channel_after_failure(self):
        fixture_process = SimpleNamespace(stderr=[], returncode=1, wait=unittest.mock.Mock())
        def finish_game():
            app.active_recordings["sports-game-1"]["stop_reason"] = "espn_final"

        channel_process = SimpleNamespace(
            stderr=[], returncode=0, wait=unittest.mock.Mock(side_effect=finish_game)
        )
        channel = {
            "channel_id": "nhl-network",
            "channel_name": "NHL Network",
            "secure_url": "https://example.invalid/nhl-network.m3u8",
        }
        game = {
            "game_id": "game-1",
            "league": "NHL",
            "home_team": "BOS",
            "away_team": "NYR",
            "broadcast_channels": ["NHL Network"],
        }
        app.active_recordings.pop("sports-game-1", None)

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(app, "settings", {"save_path": directory}),
                patch.object(
                    app.subprocess, "Popen",
                    side_effect=[fixture_process, channel_process],
                ) as popen,
                patch.object(app, "get_nufu_feed", return_value={
                    "games": [{
                        "fixture_id": "another-fixture",
                        "fixture_name": "Bruins at Rangers",
                        "secure_url": "https://example.invalid/another-fixture.m3u8",
                    }],
                    "channels": [channel],
                }),
                patch.object(app.sports, "find_broadcast_channel", return_value=channel),
                patch.object(app, "set_sports_recording_data") as save_game,
                patch.object(app, "load_schedules", return_value=[]),
                patch.object(app, "log_ffmpeg"),
                patch.object(app.time, "sleep"),
            ):
                app.record_stream(
                    "https://example.invalid/fixture.m3u8",
                    "Bruins at Golden Knights",
                    6 * 3600,
                    "sports-game-1",
                    "NHL",
                    sports_game=game,
                    sports_source="games",
                    selected_feed_id="fixture-123",
                )

        self.assertEqual(popen.call_count, 2)
        self.assertEqual(
            popen.call_args_list[0].args[0][popen.call_args_list[0].args[0].index("-i") + 1],
            "https://example.invalid/fixture.m3u8",
        )
        self.assertEqual(
            popen.call_args_list[1].args[0][popen.call_args_list[1].args[0].index("-i") + 1],
            channel["secure_url"],
        )
        self.assertEqual(save_game.call_args_list[-1].args[1]["recording_state"], "completed")
        self.assertEqual(save_game.call_args_list[-1].args[1]["stop_reason"], "espn_final")
        self.assertTrue(any(
            call.args[1].get("selected_feed_id") == "nhl-network"
            for call in save_game.call_args_list
        ))
        self.assertNotIn("sports-game-1", app.active_recordings)

    def test_channel_feed_is_not_consulted_when_fixture_recording_succeeds(self):
        fixture_process = SimpleNamespace(stderr=[], returncode=0, wait=unittest.mock.Mock())

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(app, "settings", {"save_path": directory}),
                patch.object(app.subprocess, "Popen", return_value=fixture_process) as popen,
                patch.object(app, "get_nufu_feed") as get_feed,
                patch.object(app, "set_sports_recording_data"),
                patch.object(app, "load_schedules", return_value=[]),
                patch.object(app, "log_ffmpeg"),
                patch.object(app.time, "monotonic", side_effect=[0, 0, 6 * 3600]),
            ):
                app.record_stream(
                    "https://example.invalid/fixture.m3u8",
                    "Bruins at Golden Knights",
                    6 * 3600,
                    "sports-game-2",
                    "NHL",
                    sports_game={"game_id": "game-2", "league": "NHL"},
                    sports_source="games",
                    selected_feed_id="fixture-123",
                )

        popen.assert_called_once()
        get_feed.assert_not_called()


class SportsPreflightTests(unittest.TestCase):
    def test_failed_sports_start_is_retried_after_cooldown(self):
        now = datetime(2026, 11, 3, 0, 21, tzinfo=timezone.utc)
        game = {
            "game_id": "game-failed",
            "favorite_teams": ["NHL:BOS"],
            "start_at": "2026-11-03T00:00:00+00:00",
            "recording_state": "failed",
            "last_start_attempt": (now - timedelta(minutes=6)).isoformat(),
        }

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "sports_favorites", return_value=["NHL:BOS"]),
            patch.object(app, "load_team_schedule", return_value={"games": [game]}),
            patch.object(app, "set_sports_recording_data") as save_game,
            patch.object(app, "_queue_sports_recording_start") as queue_start,
        ):
            app.check_due_sports_recordings()

        save_game.assert_called_once_with("game-failed", {
            "recording_state": "scheduled",
            "last_start_error": "",
        })
        queue_start.assert_called_once_with("game-failed")

    def test_failed_sports_start_is_not_retried_before_cooldown(self):
        now = datetime(2026, 11, 3, 0, 3, tzinfo=timezone.utc)
        game = {
            "game_id": "game-failed",
            "favorite_teams": ["NHL:BOS"],
            "start_at": "2026-11-03T00:00:00+00:00",
            "recording_state": "failed",
            "last_start_attempt": (now - timedelta(minutes=2)).isoformat(),
        }

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "sports_favorites", return_value=["NHL:BOS"]),
            patch.object(app, "load_team_schedule", return_value={"games": [game]}),
            patch.object(app, "set_sports_recording_data") as save_game,
            patch.object(app, "_queue_sports_recording_start") as queue_start,
        ):
            app.check_due_sports_recordings()

        save_game.assert_not_called()
        queue_start.assert_not_called()

    def test_feed_is_checked_during_final_three_minutes_before_start(self):
        game = {
            "game_id": "game-1",
            "favorite_teams": ["NHL:BOS"],
            "start_at": "2026-10-24T20:03:00+00:00",
            "recording_state": "scheduled",
        }

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)

        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "sports_favorites", return_value=["NHL:BOS"]),
            patch.object(app, "load_team_schedule", return_value={"games": [game]}),
            patch.object(app, "start_sports_recording") as start_recording,
        ):
            app.check_due_sports_recordings()

        start_recording.assert_called_once_with("game-1", preflight=True)

    def test_simultaneous_due_games_start_on_independent_workers(self):
        now = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
        games = [
            {
                "game_id": "game-nhl",
                "favorite_teams": ["NHL:BOS"],
                "start_at": now.isoformat(),
                "recording_state": "scheduled",
            },
            {
                "game_id": "game-nfl",
                "favorite_teams": ["NFL:BUF"],
                "start_at": now.isoformat(),
                "recording_state": "scheduled",
            },
        ]
        barrier = threading.Barrier(2)
        native_thread = threading.Thread
        workers = []

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        def track_worker(*args, **kwargs):
            worker = native_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        def start_game(game_id, preflight=False):
            barrier.wait(timeout=2)

        app.sports_starts_in_progress.clear()
        with (
            patch.object(app, "datetime", FrozenDateTime),
            patch.object(app, "sports_favorites", return_value=["NHL:BOS", "NFL:BUF"]),
            patch.object(app, "load_team_schedule", return_value={"games": games}),
            patch.object(app, "start_sports_recording", side_effect=start_game),
            patch.object(app.threading, "Thread", side_effect=track_worker),
        ):
            app.check_due_sports_recordings()
            for worker in workers:
                worker.join(timeout=3)

        self.assertEqual(len(workers), 2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(app.sports_starts_in_progress, set())

    def test_simultaneous_manual_jobs_are_dispatched_independently(self):
        barrier = threading.Barrier(2)
        native_thread = threading.Thread
        workers = []

        def track_worker(*args, **kwargs):
            worker = native_thread(*args, **kwargs)
            workers.append(worker)
            return worker

        def run_job(task):
            barrier.wait(timeout=2)

        with (
            patch.object(app, "_run_scheduled_job", side_effect=run_job),
            patch.object(app.threading, "Thread", side_effect=track_worker),
        ):
            app.job_wrapper({"uuid": "manual-1"})
            app.job_wrapper({"uuid": "manual-2"})
            for worker in workers:
                worker.join(timeout=3)

        self.assertEqual(len(workers), 2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))


if __name__ == "__main__":
    unittest.main()
