import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "check_trips", Path(__file__).parents[1] / "src" / "check_trips.py"
)
check_trips = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_trips)


def trip(
    departure="08:30",
    arrival="09:30",
    actual_departure=None,
    cancelled=False,
    number="1",
    date="2025-01-02",
):
    origin = {
        "plannedDateTime": f"{date}T{departure}:00+01:00",
        "plannedTrack": "1",
        "stationCode": "Asd",
    }
    if actual_departure:
        origin["actualDateTime"] = f"{date}T{actual_departure}:00+01:00"
    return {
        "cancelled": cancelled,
        "plannedDurationInMinutes": 60,
        "legs": [
            {
                "origin": origin,
                "destination": {
                    "plannedDateTime": f"{date}T{arrival}:00+01:00",
                    "stationCode": "Rtd",
                },
                "product": {"number": number},
            }
        ],
    }


class CheckTripsTests(unittest.TestCase):
    def test_cancelled_trip_is_unavailable(self):
        cancelled = trip(cancelled=True)
        self.assertTrue(check_trips.is_unavailable(cancelled, [cancelled]))

    def test_delay_of_ten_minutes_is_still_available(self):
        delayed = trip(actual_departure="08:40")
        alternative = trip(departure="08:35", arrival="09:20", number="2")
        self.assertFalse(check_trips.is_unavailable(delayed, [delayed, alternative]))

    def test_long_delay_is_unavailable_when_an_alternative_arrives_earlier(self):
        delayed = trip(actual_departure="08:45", arrival="09:45")
        alternative = trip(departure="08:35", arrival="09:30", number="2")
        self.assertTrue(check_trips.is_unavailable(delayed, [delayed, alternative]))

    def test_route_change_requires_a_changed_itinerary_or_more_than_ten_minutes(self):
        previous = check_trips.trip_signature(trip())
        self.assertFalse(
            check_trips.route_changed(
                previous, check_trips.trip_signature(trip(departure="08:40", arrival="09:40"))
            )
        )
        self.assertFalse(
            check_trips.route_changed(previous, check_trips.trip_signature(trip(number="2")))
        )
        self.assertFalse(
            check_trips.route_changed(
                previous,
                check_trips.trip_signature(trip(date="2025-01-03")),
            )
        )
        self.assertTrue(
            check_trips.route_changed(
                previous, check_trips.trip_signature(trip(departure="08:41", arrival="09:41"))
            )
        )

    def test_route_change_detects_a_different_transfer_station(self):
        previous = check_trips.trip_signature(trip())
        changed = trip()
        changed["legs"][0]["destination"]["stationCode"] = "Ut"
        self.assertTrue(check_trips.route_changed(previous, check_trips.trip_signature(changed)))

    def test_main_warns_and_sends_available_alternatives(self):
        usual = trip(cancelled=True)
        alternative = trip(departure="08:35", arrival="09:20", number="2")
        messages = []
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"NS_COMMUTE_STATE_DIR": state_dir}):
                with patch.object(sys, "argv", ["check_trips.py", "Asd", "Rtd", "08:30"]):
                    with patch.object(
                        check_trips,
                        "load_config",
                        return_value={
                            "ns_api_key": "key",
                            "telegram_api_key": "bot",
                            "telegram_chat_id": "chat",
                        },
                    ), patch.object(
                        check_trips,
                        "get_trips",
                        return_value={"trips": [usual, alternative]},
                    ), patch.object(
                        check_trips,
                        "send_telegram_message",
                        side_effect=lambda _, __, message: messages.append(message),
                    ):
                        check_trips.main()

        self.assertIn("⚠️ Usual route unavailable", messages[0])
        self.assertIn("08:35 → 09:20", messages[0])
        self.assertEqual(messages[0].count("08:30 → 09:30"), 1)

    def test_main_logs_the_decision_when_an_alert_is_suppressed(self):
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"NS_COMMUTE_STATE_DIR": state_dir}):
                with patch.object(sys, "argv", ["check_trips.py", "Asd", "Rtd", "08:30"]):
                    with patch.object(
                        check_trips,
                        "load_config",
                        return_value={
                            "ns_api_key": "key",
                            "telegram_api_key": "bot",
                            "telegram_chat_id": "chat",
                        },
                    ), patch.object(
                        check_trips, "get_trips", return_value={"trips": [trip()]}
                    ), patch.object(check_trips, "send_telegram_message"):
                        with self.assertLogs(check_trips.logger, "INFO") as logs:
                            check_trips.main()

        self.assertIn("route decision route=Asd→Rtd", "\n".join(logs.output))
        self.assertIn("no alert route=Asd→Rtd", "\n".join(logs.output))

    def test_main_does_not_notify_for_an_identical_route_on_the_next_day(self):
        messages = []
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"NS_COMMUTE_STATE_DIR": state_dir}):
                with patch.object(sys, "argv", ["check_trips.py", "Asd", "Rtd", "08:30"]):
                    with patch.object(
                        check_trips,
                        "load_config",
                        return_value={
                            "ns_api_key": "key",
                            "telegram_api_key": "bot",
                            "telegram_chat_id": "chat",
                        },
                    ), patch.object(
                        check_trips,
                        "get_trips",
                        return_value={"trips": [trip(date="2025-01-03")]},
                    ), patch.object(
                        check_trips,
                        "send_telegram_message",
                        side_effect=lambda _, __, message: messages.append(message),
                    ):
                        check_trips.save_baseline(
                            "Asd",
                            "Rtd",
                            "08:30",
                            "2025-01-02",
                            check_trips.trip_signature(trip(date="2025-01-02")),
                        )
                        check_trips.main()

        self.assertEqual(messages, [])

    def test_main_does_not_notify_for_an_unchanged_route(self):
        messages = []
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"NS_COMMUTE_STATE_DIR": state_dir}):
                with patch.object(sys, "argv", ["check_trips.py", "Asd", "Rtd", "08:30"]):
                    with patch.object(
                        check_trips,
                        "load_config",
                        return_value={
                            "ns_api_key": "key",
                            "telegram_api_key": "bot",
                            "telegram_chat_id": "chat",
                        },
                    ), patch.object(
                        check_trips,
                        "get_trips",
                        return_value={"trips": [trip()]},
                    ), patch.object(
                        check_trips,
                        "send_telegram_message",
                        side_effect=lambda _, __, message: messages.append(message),
                    ):
                        check_trips.main()

        self.assertEqual(messages, [])

    def test_main_warns_when_the_usual_route_changed_since_yesterday(self):
        messages = []
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"NS_COMMUTE_STATE_DIR": state_dir}):
                with patch.object(sys, "argv", ["check_trips.py", "Asd", "Rtd", "08:30"]):
                    with patch.object(
                        check_trips,
                        "load_config",
                        return_value={
                            "ns_api_key": "key",
                            "telegram_api_key": "bot",
                            "telegram_chat_id": "chat",
                        },
                    ), patch.object(
                        check_trips,
                        "get_trips",
                        return_value={"trips": [trip(departure="08:41", arrival="09:41")]},
                    ), patch.object(
                        check_trips,
                        "send_telegram_message",
                        side_effect=lambda _, __, message: messages.append(message),
                    ):
                        check_trips.save_baseline(
                            "Asd",
                            "Rtd",
                            "08:30",
                            "2025-01-01",
                            check_trips.trip_signature(trip(number="1")),
                        )
                        check_trips.main()

        self.assertIn("⚠️ Usual route changed", messages[0])


if __name__ == "__main__":
    unittest.main()
