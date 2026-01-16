# PR Activity Tracker

A Streamlit dashboard to track GitHub Pull Request activity for your team.

## Features

- View open and closed PRs for selected team members
- Filter by individual users or view all
- Track PR submit time, last comment, and first approval
- View line counts (additions/deletions) for closed PRs
- Highlight draft PRs
- Configurable GitHub organizations and usernames

## Setup

### 1. Clone/copy the project

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

To generate a token:
1. Go to https://github.com/settings/tokens
2. Generate new token (classic) with `repo` scope
3. For private/enterprise orgs, click "Configure SSO" and authorize the token

### 4. Run the dashboard

```bash
source venv/bin/activate
streamlit run streamlit_app.py
```

The dashboard will open at http://localhost:8501

## Configuration

Edit the sidebar in the app to:
- Add/remove GitHub organizations
- Add/remove team member usernames
- Select specific users to view
- Toggle between Open/Closed/Both PRs
- Adjust the lookback period (7-365 days)

## Files

| File | Description |
|------|-------------|
| `streamlit_app.py` | Main dashboard application |
| `github_api.py` | GitHub API helper functions |
| `requirements.txt` | Python dependencies |
| `.streamlit/secrets.toml` | GitHub token (do not commit!) |
