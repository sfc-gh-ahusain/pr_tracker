# PR Activity Tracker

A Streamlit dashboard to track GitHub Pull Request activity for your team, with automated Slack reminders.

## Features

- View open and closed PRs for selected team members
- Filter by individual users or view all
- Track PR submit time, last comment, and first approval
- View line counts (additions/deletions) for closed PRs
- Highlight draft PRs
- **Slack Integration**: Send DM reminders for stale PRs
- **Automated Reminders**: Weekly Monday notifications via cron

## Setup

### 1. Navigate to project

```bash
cd /Users/ahusain/pr-dashboard
```

### 2. Create virtual environment (if not exists)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure GitHub Token

Create `.streamlit/secrets.toml`:

```toml
GITHUB_TOKEN = "your_github_token_here"
```

### 4. Run the dashboard

```bash
source venv/bin/activate
streamlit run streamlit_app.py
```

The dashboard will open at http://localhost:8501

---

## Slack Integration Setup

### 1. Create Slack App

1. Go to https://api.slack.com/apps
2. Click **"Create New App"** → **"From scratch"**
3. Name: `PR Reminder Bot`, select your workspace

### 2. Add Bot Permissions

In **"OAuth & Permissions"**, add these Bot Token Scopes:
- `chat:write` - Send messages
- `users:read` - Look up users
- `im:write` - Open DMs

### 3. Install & Get Token

1. Click **"Install to Workspace"**
2. Copy the **Bot User OAuth Token** (`xoxb-...`)

### 4. Get Slack Member IDs

For each team member:
- In Slack, click their profile → **"More"** → **"Copy member ID"**

### 5. Configure in Dashboard

1. Open the dashboard
2. Expand **"Configure Slack Integration"**
3. Paste the Bot Token
4. Enter Slack Member IDs for each GitHub user
5. Click **"Save Slack Configuration"**

### 6. Test

1. Expand **"Preview & Send Reminders"**
2. Click **"Preview Messages"** to see what would be sent
3. Click **"Send Reminders NOW"** to send DMs

---

## Automated Monday Reminders

To send reminders every Monday at 9 AM:

```bash
crontab -e
```

Add this line:

```
0 9 * * 1 cd /Users/ahusain/pr-dashboard && ./venv/bin/python slack_notifier.py >> /tmp/pr-reminder.log 2>&1
```

### Manual Run

```bash
# Preview (dry run)
./venv/bin/python slack_notifier.py --dry-run

# Send for real
./venv/bin/python slack_notifier.py

# Custom inactivity threshold
./venv/bin/python slack_notifier.py --days 5
```

---

## Files

| File | Description |
|------|-------------|
| `streamlit_app.py` | Main dashboard application |
| `github_api.py` | GitHub API helper functions |
| `slack_notifier.py` | Slack DM reminder script |
| `slack_config.json` | Slack configuration (token, user mapping) |
| `requirements.txt` | Python dependencies |
| `.streamlit/secrets.toml` | GitHub token (do not commit!) |

---

## TODO (Pending Setup)

- [ ] Get Slack Bot Token approved and add to `slack_config.json`
- [ ] Collect Slack Member IDs for all 11 team members
- [ ] Test with dry-run: `python slack_notifier.py --dry-run`
- [ ] Set up Monday cron job
