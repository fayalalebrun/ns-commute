# NS Commute

Automated NS train departure notifications via Telegram.

## Setup
1. Copy `config.example.json` to `config.json` and add your API keys
2. `python src/setup_cron.py setup` - Create cron jobs
3. `python src/check_trips.py Asd Rtd 08:30` - Manual test

Get API keys: [NS API Portal](https://apiportal.ns.nl/) and [Telegram BotFather](https://t.me/botfather).