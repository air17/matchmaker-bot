# Matchmaker Bot

A Telegram matchmaking bot (Russian-language UI) built with **aiogram 3** and backed by **SQLite** + **Google Sheets**.

## Features

- User registration with name, gender, and preferences collected via FSM (finite-state machine)
- Questionnaire-based matching: users answer a set of questions synced from Google Sheets
- Pairwise match scoring based on compatible answers
- Admin controls: sync questions, reset users, manage admins
- Results mirrored to Google Sheets in real time

## Prerequisites

- Python 3.10+
- A [Telegram bot token](https://core.telegram.org/bots#botfather)
- A Google Cloud service account with the Google Sheets API enabled
  - Download the service account key as `credentials.json`
  - Share both spreadsheets with the service account email

## Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/air17/matchmaker-bot.git
   cd matchmaker-bot
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**

   Copy the example file and fill in the required values:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`:

   | Variable | Description |
   |---|---|
   | `BOT_TOKEN` | Telegram bot token from BotFather |
   | `GOOGLE_CREDENTIALS_FILE` | Path to the service account JSON key (default: `credentials.json`) |
   | `SPREADSHEET_A_ID` | Google Sheet ID for questions input |
   | `SPREADSHEET_B_ID` | Google Sheet ID for answers/results output |
   | `INITIAL_ADMIN_ID` | Telegram user ID of the first admin (seeded on startup) |
   | `INITIAL_ADMIN_USERNAME` | Telegram username of the first admin |

4. **Place `credentials.json`** (Google service account key) in the project root (or the path set in `GOOGLE_CREDENTIALS_FILE`).

5. **Set up Google Sheets**

   - **Spreadsheet A** — questions sheet with columns: `Question_Text`, `Option_1`, `Option_2`, …
   - **Spreadsheet B** — output sheet; tabs are created automatically by the bot.

6. **Run the bot**

   ```bash
   python bot.py
   ```

## Usage

### User flow

```
/start → enter name → choose gender → choose preference → answer questions → matched!
```

User statuses progress through: `pending` → `registering` → `answering` → `complete`

### Admin commands

| Command | Description |
|---|---|
| `/update` | Sync questions & answers from Sheets, recalculate matches |
| `/reset_user <@username\|id>` | Remove a user from DB and Sheets, recalculate matches |
| `/add_admin <user_id> <username>` | Grant admin privileges to a user |
| `/list_admins` | List all current admins |

## Architecture

| File | Role |
|---|---|
| `bot.py` | Entry point: initializes DB, seeds admin, starts polling |
| `database.py` | All SQLite access via a `get_db()` context manager |
| `google_sheets.py` | All gspread operations; writes run via `asyncio.to_thread` |
| `handlers.py` | Single aiogram `Router` with all user-facing and admin commands |
| `states.py` | FSM state groups: `RegistrationStates` and `AnsweringStates` |

## Development

```bash
# Lint and auto-fix
ruff check --fix .
ruff format .

# Run all pre-commit hooks
pre-commit run --all-files
```

## License

MIT
