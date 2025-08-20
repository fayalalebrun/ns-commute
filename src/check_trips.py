#!/usr/bin/env python3
import sys
import json
import requests
from datetime import datetime, timedelta


def load_config(config_path="config.json"):
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

    response = requests.get(url, headers=headers, params=params)
    print(f"Request URL: {response.url}")
    if response.status_code != 200:
        print(f"Error response: {response.text}")
    response.raise_for_status()
    return response.json()


def format_trip(trip):
    departure = trip["legs"][0]["origin"]["plannedDateTime"]
    arrival = trip["legs"][-1]["destination"]["plannedDateTime"]
    transfers = len(trip["legs"]) - 1

    dep_time = datetime.fromisoformat(departure.replace("Z", "+00:00")).strftime(
        "%H:%M"
    )
    arr_time = datetime.fromisoformat(arrival.replace("Z", "+00:00")).strftime("%H:%M")

    duration = trip["plannedDurationInMinutes"]

    return f"{dep_time} → {arr_time} ({duration}min, {transfers} transfers)"


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

    config = load_config(config_path)

    try:
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

        filtered_trips.sort(
            key=lambda x: (len(x["legs"]) - 1, x["plannedDurationInMinutes"])
        )

        top_trips = filtered_trips[:3]

        message_lines = [f"<b>{from_station} → {to_station}</b> at {departure_time}"]
        message_lines.extend([format_trip(trip) for trip in top_trips])

        message = "\n".join(message_lines)

        send_telegram_message(
            config["telegram_api_key"], config["telegram_chat_id"], message
        )
        print(f"Sent notification for {from_station} → {to_station}")

    except Exception as e:
        error_msg = f"Error checking {from_station} → {to_station}: {str(e)}"
        send_telegram_message(
            config["telegram_api_key"], config["telegram_chat_id"], error_msg
        )
        print(error_msg)


if __name__ == "__main__":
    main()
