#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime, timedelta
from crontab import CronTab


def load_config():
    with open("config.json", "r") as f:
        return json.load(f)


def time_to_minutes(time_str):
    hours, minutes = map(int, time_str.split(":"))
    return hours * 60 + minutes


def parse_offset(offset_str):
    import re

    if "h" in offset_str and "m" in offset_str:
        match = re.match(r"(\d+)h(\d+)m", offset_str)
        if match:
            hours, minutes = map(int, match.groups())
            return hours * 60 + minutes
    elif offset_str.endswith("h"):
        return int(offset_str[:-1]) * 60
    elif offset_str.endswith("m"):
        return int(offset_str[:-1])

    return int(offset_str)


def minutes_to_cron_time(minutes):
    hours = minutes // 60
    mins = minutes % 60
    return f"{mins} {hours}"


def setup_cron_jobs():
    config = load_config()
    script_path = os.path.abspath("src/check_trips.py")
    config_path = os.path.abspath("config.json")
    python_path = sys.executable

    cron = CronTab(user=True)
    cron.remove_all(comment="ns-commute")

    for route in config["routes"]:
        departure_station = route["departure_station"]
        arrival_station = route["arrival_station"]
        departure_time = route["departure_time"]
        cron_offsets = route["cron_offsets"]

        departure_minutes = time_to_minutes(departure_time)

        for offset in cron_offsets:
            offset_minutes = parse_offset(offset)
            notification_minutes = departure_minutes - offset_minutes

            if notification_minutes < 0:
                notification_minutes += 24 * 60

            cron_time = minutes_to_cron_time(notification_minutes)

            command = f"{python_path} {script_path} {departure_station} {arrival_station} {departure_time} {config_path}"

            job = cron.new(command=command, comment="ns-commute")
            job.setall(cron_time + " * * *")

            print(f"Added cron job: {cron_time} * * * {command}")

    cron.write()
    print(
        f"Created {len([job for job in cron if job.comment == 'ns-commute'])} cron jobs"
    )


def list_cron_jobs():
    cron = CronTab(user=True)
    jobs = [job for job in cron if job.comment == "ns-commute"]

    if not jobs:
        print("No ns-commute cron jobs found")
        return

    print("Current ns-commute cron jobs:")
    for job in jobs:
        print(f"  {job.slices} {job.command}")


def remove_cron_jobs():
    cron = CronTab(user=True)
    removed = cron.remove_all(comment="ns-commute")
    cron.write()
    print(f"Removed {removed} ns-commute cron jobs")


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python setup_cron.py <setup|list|remove>")
        sys.exit(1)

    action = sys.argv[1]

    if action == "setup":
        setup_cron_jobs()
    elif action == "list":
        list_cron_jobs()
    elif action == "remove":
        remove_cron_jobs()
    else:
        print("Invalid action. Use: setup, list, or remove")
        sys.exit(1)


if __name__ == "__main__":
    main()
