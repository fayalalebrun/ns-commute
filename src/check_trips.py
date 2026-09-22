#!/usr/bin/env python3
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_config(config_path="config.json"):
    # Try environment variables first (for systemd services)
    if all(
        k in os.environ for k in ["TELEGRAM_API_KEY", "TELEGRAM_CHAT_ID", "NS_API_KEY"]
    ):
        return {
            "telegram_api_key": os.environ["TELEGRAM_API_KEY"],
            "telegram_chat_id": os.environ["TELEGRAM_CHAT_ID"],
            "ns_api_key": os.environ["NS_API_KEY"],
        }

    # Fall back to config file
    with open(config_path, "r") as f:
        return json.load(f)


def get_trips(api_key, from_station, to_station, departure_time):
    from datetime import datetime, timedelta

    # Convert HH:MM to ISO 8601 format, use tomorrow if time has passed
    now = datetime.now()
    departure_hour, departure_minute = map(int, departure_time.split(":"))

    departure_today = now.replace(
        hour=departure_hour, minute=departure_minute, second=0, microsecond=0
    )

    if departure_today <= now:
        departure_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        departure_date = now.strftime("%Y-%m-%d")

    iso_datetime = f"{departure_date}T{departure_time}:00"

    url = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/trips"
    headers = {"Ocp-Apim-Subscription-Key": api_key, "Accept": "application/json"}
    params = {
        "fromStation": from_station,
        "toStation": to_station,
        "dateTime": iso_datetime,
    }

    logger.info("requesting trips route=%s→%s departure=%s", from_station, to_station, iso_datetime)
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        logger.error("NS API response status=%s body=%s", response.status_code, response.text)
    response.raise_for_status()
    return response.json()


def planned_datetime(trip):
    return datetime.fromisoformat(
        trip["legs"][0]["origin"]["plannedDateTime"].replace("Z", "+00:00")
    )


def actual_datetime(stop):
    value = stop.get("actualDateTime") or stop["plannedDateTime"]
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_cancelled(trip):
    if trip.get("cancelled") or str(trip.get("status", "")).upper() == "CANCELLED":
        return True
    return any(
        stop.get("cancelled")
        or str(stop.get("departureStatus", "")).upper() == "CANCELLED"
        for leg in trip["legs"]
        for stop in (leg["origin"], leg["destination"])
    )


def departure_delay_minutes(trip):
    origin = trip["legs"][0]["origin"]
    return (
        actual_datetime(origin)
        - datetime.fromisoformat(origin["plannedDateTime"].replace("Z", "+00:00"))
    ).total_seconds() / 60


def arrival_datetime(trip):
    return actual_datetime(trip["legs"][-1]["destination"])


def is_unavailable(trip, trips):
    if is_cancelled(trip):
        return True
    if departure_delay_minutes(trip) <= 10:
        return False
    return any(
        other is not trip
        and not is_cancelled(other)
        and arrival_datetime(other) < arrival_datetime(trip)
        for other in trips
    )


def trip_sort_key(trip):
    return len(trip["legs"]) - 1, trip["plannedDurationInMinutes"]


def station_identity(stop):
    return stop.get("stationCode") or stop.get("uicCode") or stop.get("name", "?")


def leg_identity(leg):
    return [station_identity(leg["origin"]), station_identity(leg["destination"])]


def trip_signature(trip):
    return {
        "departure": planned_datetime(trip).isoformat(),
        "arrival": datetime.fromisoformat(
            trip["legs"][-1]["destination"]["plannedDateTime"].replace("Z", "+00:00")
        ).isoformat(),
        "transfers": len(trip["legs"]) - 1,
        "legs": [leg_identity(leg) for leg in trip["legs"]],
    }


def route_changed(previous, current):
    if (
        previous["transfers"] != current["transfers"]
        or previous["legs"] != current["legs"]
    ):
        return True
    for field in ("departure", "arrival"):
        old = datetime.fromisoformat(previous[field])
        new = datetime.fromisoformat(current[field])
        if abs((new - old).total_seconds()) > 10 * 60:
            return True
    return False


def baseline_path(from_station, to_station, departure_time):
    state_dir = Path(
        os.environ.get(
            "NS_COMMUTE_STATE_DIR",
            os.environ.get(
                "STATE_DIRECTORY",
                Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                / "ns-commute",
            ),
        )
    )
    key = f"{from_station}\0{to_station}\0{departure_time}".encode()
    return state_dir / f"{hashlib.sha256(key).hexdigest()}.json"


def load_baseline(from_station, to_station, departure_time):
    path = baseline_path(from_station, to_station, departure_time)
    try:
        with path.open() as state_file:
            baseline = json.load(state_file)
            logger.info("loaded baseline route=%s→%s time=%s date=%s", from_station, to_station, departure_time, baseline["date"])
            return baseline
    except FileNotFoundError:
        logger.info("no baseline route=%s→%s time=%s", from_station, to_station, departure_time)
        return None


def save_baseline(from_station, to_station, departure_time, date, signature):
    path = baseline_path(from_station, to_station, departure_time)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    with temporary_path.open("w") as state_file:
        json.dump({"date": date, "signature": signature}, state_file)
    temporary_path.replace(path)
    logger.info("saved baseline route=%s→%s time=%s date=%s", from_station, to_station, departure_time, date)


def format_trip(trip):
    departure = trip["legs"][0]["origin"]["plannedDateTime"]
    arrival = trip["legs"][-1]["destination"]["plannedDateTime"]
    transfers = len(trip["legs"]) - 1

    dep_time = datetime.fromisoformat(departure.replace("Z", "+00:00")).strftime(
        "%H:%M"
    )
    arr_time = datetime.fromisoformat(arrival.replace("Z", "+00:00")).strftime("%H:%M")

    duration = trip["plannedDurationInMinutes"]
    platform = trip["legs"][0]["origin"].get("plannedTrack", "?")

    return f"{dep_time} → {arr_time} (platform {platform}, {duration}min, {transfers} transfers)"


def send_telegram_message(api_key, chat_id, message):
    url = f"https://api.telegram.org/bot{api_key}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    response = requests.post(url, data=data)
    response.raise_for_status()


def main():
    if len(sys.argv) < 4 or len(sys.argv) > 5:
        print(
            "Usage: python check_trips.py <from_station> <to_station> <departure_time> [config_path]"
        )
        sys.exit(1)

    from_station = sys.argv[1]
    to_station = sys.argv[2]
    departure_time = sys.argv[3]
    config_path = sys.argv[4] if len(sys.argv) == 5 else "config.json"

    try:
        config = load_config(config_path)
        trips_data = get_trips(
            config["ns_api_key"], from_station, to_station, departure_time
        )
        trips = trips_data.get("trips", [])

        # Filter out trips that depart before the requested time
        departure_hour, departure_minute = map(int, departure_time.split(":"))
        filtered_trips = []

        for trip in trips:
            first_leg = trip["legs"][0]
            planned_departure = first_leg["origin"]["plannedDateTime"]
            trip_time = datetime.fromisoformat(planned_departure.replace("Z", "+00:00"))

            if trip_time.hour > departure_hour or (
                trip_time.hour == departure_hour
                and trip_time.minute >= departure_minute
            ):
                filtered_trips.append(trip)

        filtered_trips.sort(key=trip_sort_key)
        if not filtered_trips:
            raise ValueError(f"No trips found at or after {departure_time}")

        usual_trip = filtered_trips[0]
        unavailable = [
            trip for trip in filtered_trips if is_unavailable(trip, filtered_trips)
        ]
        available = [trip for trip in filtered_trips if trip not in unavailable]
        top_trips = (available if usual_trip in unavailable else filtered_trips)[:3]

        message_lines = [f"<b>{from_station} → {to_station}</b> at {departure_time}"]
        usual_signature = trip_signature(usual_trip)
        usual_date = planned_datetime(usual_trip).date().isoformat()
        baseline = load_baseline(from_station, to_station, departure_time)

        route_unavailable = usual_trip in unavailable
        route_changed_since_previous_day = (
            baseline
            and baseline["date"] < usual_date
            and route_changed(baseline["signature"], usual_signature)
        )
        logger.info(
            "route decision route=%s→%s time=%s trips=%d usual=%s unavailable=%s changed=%s baseline_date=%s",
            from_station,
            to_station,
            departure_time,
            len(filtered_trips),
            format_trip(usual_trip),
            route_unavailable,
            route_changed_since_previous_day,
            baseline["date"] if baseline else None,
        )

        if route_unavailable:
            message_lines.append(
                f"⚠️ Usual route unavailable: {format_trip(usual_trip)}"
            )
        if route_changed_since_previous_day:
            message_lines.append("⚠️ Usual route changed since the previous journey day")
        if route_unavailable or route_changed_since_previous_day:
            if not top_trips:
                message_lines.append("⚠️ No available alternatives found")
            else:
                message_lines.extend(format_trip(trip) for trip in top_trips)

        save_baseline(
            from_station,
            to_station,
            departure_time,
            usual_date,
            usual_signature,
        )
        if route_unavailable or route_changed_since_previous_day:
            message = "\n".join(message_lines)
            logger.info(
                "sending alert route=%s→%s message=%r",
                from_station,
                to_station,
                message,
            )
            send_telegram_message(
                config["telegram_api_key"], config["telegram_chat_id"], message
            )
            logger.info("sent alert route=%s→%s", from_station, to_station)
        else:
            logger.info("no alert route=%s→%s", from_station, to_station)

    except Exception:
        logger.exception("error checking route=%s→%s", from_station, to_station)
        error_msg = f"Error checking {from_station} → {to_station}"
        try:
            config
        except UnboundLocalError:
            return
        try:
            send_telegram_message(
                config["telegram_api_key"], config["telegram_chat_id"], error_msg
            )
        except Exception:
            logger.exception("failed to send error alert route=%s→%s", from_station, to_station)


if __name__ == "__main__":
    main()
