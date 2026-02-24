import os
import json
import requests
from datetime import datetime, timedelta
import pytz
from github_api import search_prs, get_pr_reviews, get_pr_comments, get_pr_review_comments, get_pr_details, parse_repo_from_url, get_first_approval_time, get_last_comment_time, get_last_activity_time, search_review_requested_prs, get_multiple_prs_full_details

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "slack_config.json")

LAST_RUN_FILE = os.path.join(os.path.dirname(__file__), ".schedule_last_run.json")

def load_last_run():
    if os.path.exists(LAST_RUN_FILE):
        with open(LAST_RUN_FILE, "r") as f:
            return json.load(f)
    return {}

def save_last_run(data):
    with open(LAST_RUN_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_schedule_for_user(username: str, config: dict) -> dict:
    schedules = config.get("schedules", {})
    user_overrides = schedules.get("user_overrides", {})
    if username in user_overrides:
        return user_overrides[username]
    return schedules.get("team_default", {"enabled": True, "frequency": "weekly", "days_of_week": ["Monday"], "time": "09:00", "timezone": "America/Los_Angeles"})

def should_run_now(schedule: dict, last_run_time: datetime = None) -> bool:
    if not schedule.get("enabled", True):
        return False
    
    tz = pytz.timezone(schedule.get("timezone", "America/Los_Angeles"))
    now = datetime.now(tz)
    scheduled_time = datetime.strptime(schedule.get("time", "09:00"), "%H:%M").time()
    
    current_hour_min = now.strftime("%H:%M")
    scheduled_hour_min = schedule.get("time", "09:00")
    
    time_match = abs(now.hour * 60 + now.minute - int(scheduled_hour_min.split(":")[0]) * 60 - int(scheduled_hour_min.split(":")[1])) <= 5
    
    if not time_match:
        return False
    
    frequency = schedule.get("frequency", "weekly").lower()
    
    if frequency == "daily":
        if last_run_time:
            last_run_tz = last_run_time.astimezone(tz) if last_run_time.tzinfo else tz.localize(last_run_time)
            if last_run_tz.date() == now.date():
                return False
        return True
    
    elif frequency == "weekly":
        days_of_week = schedule.get("days_of_week", ["Monday"])
        current_day = now.strftime("%A")
        if current_day not in days_of_week:
            return False
        if last_run_time:
            last_run_tz = last_run_time.astimezone(tz) if last_run_time.tzinfo else tz.localize(last_run_time)
            if last_run_tz.date() == now.date():
                return False
        return True
    
    elif frequency == "monthly":
        day_of_month = schedule.get("day_of_month", 1)
        if now.day != day_of_month:
            return False
        if last_run_time:
            last_run_tz = last_run_time.astimezone(tz) if last_run_time.tzinfo else tz.localize(last_run_time)
            if last_run_tz.month == now.month and last_run_tz.year == now.year:
                return False
        return True
    
    elif frequency == "custom interval":
        interval_days = schedule.get("interval_days", 7)
        if last_run_time:
            last_run_tz = last_run_time.astimezone(tz) if last_run_time.tzinfo else tz.localize(last_run_time)
            days_since = (now.date() - last_run_tz.date()).days
            if days_since < interval_days:
                return False
        return True
    
    return False

def get_users_to_notify(config: dict) -> list:
    usernames = config.get("usernames", [])
    last_runs = load_last_run()
    users_to_notify = []
    
    for username in usernames:
        schedule = get_schedule_for_user(username, config)
        last_run_str = last_runs.get(username)
        last_run_time = datetime.fromisoformat(last_run_str) if last_run_str else None
        
        if should_run_now(schedule, last_run_time):
            users_to_notify.append(username)
    
    return users_to_notify

def update_last_run_for_users(usernames: list):
    last_runs = load_last_run()
    now_str = datetime.now(pytz.UTC).isoformat()
    for username in usernames:
        last_runs[username] = now_str
    save_last_run(last_runs)

def is_cherrypick_pr(title: str, base_branch: str = "") -> bool:
    title_lower = title.lower()
    if any(pattern in title_lower for pattern in ['cherry-pick', 'cherrypick', 'cherry pick', '[cp]', '(cp)']):
        return True
    if base_branch.startswith('release/'):
        return True
    return False

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def get_config_with_defaults():
    config = load_config()
    if "repos" not in config:
        config["repos"] = ["snowflakedb/frostdb", "snowflakedb/fdb-tls-tools"]
    if "orgs" in config:
        del config["orgs"]
    if "usernames" not in config:
        config["usernames"] = [
            "sfc-gh-alfeng", "sfc-gh-huliu", "sfc-gh-juliu", "sfc-gh-imubarek",
            "sfc-gh-ynannapaneni", "sfc-gh-speddi", "sfc-gh-tpendock", "sfc-gh-bravi",
            "sfc-gh-jshim", "sfc-gh-nwijetunga", "sfc-gh-hoyang"
        ]
    if "days_back" not in config:
        config["days_back"] = 90
    return config

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_slack_token():
    config = load_config()
    return config.get("slack_bot_token", os.environ.get("SLACK_BOT_TOKEN", ""))

def get_user_slack_mapping():
    config = load_config()
    return config.get("user_slack_mapping", {})

def get_user_display_names():
    config = load_config()
    return config.get("user_display_names", {})

def send_slack_dm(slack_user_id: str, message: str) -> bool:
    token = get_slack_token()
    if not token:
        print("Error: No Slack bot token configured")
        return False
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    open_resp = requests.post(
        "https://slack.com/api/conversations.open",
        headers=headers,
        json={"users": slack_user_id}
    )
    
    if not open_resp.ok or not open_resp.json().get("ok"):
        print(f"Error opening DM: {open_resp.text}")
        return False
    
    channel_id = open_resp.json()["channel"]["id"]
    
    msg_resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        json={
            "channel": channel_id,
            "text": message,
            "mrkdwn": True
        }
    )
    
    if not msg_resp.ok or not msg_resp.json().get("ok"):
        print(f"Error sending message: {msg_resp.text}")
        return False
    
    return True

def find_stale_prs(repos: list, username: str, hours_inactive: int = 24, exclude_drafts: bool = True, days_back: int = 90, exclude_cherrypicks: bool = False) -> list:
    prs = search_prs(repos, [username], state="open", days_back=days_back)
    stale = []
    now = datetime.utcnow()
    
    for pr in prs:
        if exclude_drafts and pr.get("draft", False):
            continue
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        details = get_pr_details(owner, repo, pr_number) if owner and repo else None
        base_branch = details.get("base", {}).get("ref", "") if details else ""
        
        if exclude_cherrypicks and is_cherrypick_pr(pr.get("title", ""), base_branch):
            continue
        
        issue_comments = get_pr_comments(owner, repo, pr_number) if owner and repo else []
        review_comments = get_pr_review_comments(owner, repo, pr_number) if owner and repo else []
        reviews = get_pr_reviews(owner, repo, pr_number) if owner and repo else []
        last_activity = get_last_activity_time(issue_comments, review_comments, reviews)
        
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        last_activity_dt = last_activity.replace(tzinfo=None) if last_activity else created_at
        
        hours_since_activity = (now - last_activity_dt).total_seconds() / 3600
        
        if hours_since_activity >= hours_inactive:
            stale.append({
                "pr": pr,
                "hours_inactive": int(hours_since_activity),
                "reason": "no_activity",
                "base_branch": base_branch
            })
    
    return stale

def find_stale_drafts(repos: list, username: str, days_draft_stale: int = 7, days_back: int = 90, exclude_cherrypicks: bool = False) -> list:
    prs = search_prs(repos, [username], state="open", days_back=days_back)
    stale_drafts = []
    now = datetime.utcnow()
    
    for pr in prs:
        if not pr.get("draft", False):
            continue
        
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        details = get_pr_details(owner, repo, pr_number) if owner and repo else None
        base_branch = details.get("base", {}).get("ref", "") if details else ""
        
        if exclude_cherrypicks and is_cherrypick_pr(pr.get("title", ""), base_branch):
            continue
        
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        days_as_draft = (now - created_at).days
        
        if days_as_draft >= days_draft_stale:
            stale_drafts.append({
                "pr": pr,
                "days_as_draft": days_as_draft,
                "reason": "stale_draft",
                "base_branch": base_branch
            })
    
    return stale_drafts

def find_approved_not_merged(repos: list, username: str, days_threshold: int = 1, exclude_drafts: bool = True, days_back: int = 90, exclude_cherrypicks: bool = False) -> list:
    prs = search_prs(repos, [username], state="open", days_back=days_back)
    approved_pending = []
    
    for pr in prs:
        if exclude_drafts and pr.get("draft", False):
            continue
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        details = get_pr_details(owner, repo, pr_number) if owner and repo else None
        base_branch = details.get("base", {}).get("ref", "") if details else ""
        
        if exclude_cherrypicks and is_cherrypick_pr(pr.get("title", ""), base_branch):
            continue
        
        reviews = get_pr_reviews(owner, repo, pr_number) if owner and repo else []
        first_approval = get_first_approval_time(reviews)
        
        if first_approval:
            days_since_approval = (datetime.utcnow() - first_approval.replace(tzinfo=None)).days
            if days_since_approval >= days_threshold:
                approved_pending.append({
                    "pr": pr,
                    "days_since_approval": days_since_approval,
                    "reason": "approved_not_merged",
                    "base_branch": base_branch
                })
    
    return approved_pending

def format_reminder_message(username: str, stale_prs: list, approved_prs: list, stale_drafts: list, awaiting_your_review: list = None, display_name: str = None) -> str:
    awaiting_your_review = awaiting_your_review or []
    total = len(stale_prs) + len(approved_prs) + len(stale_drafts) + len(awaiting_your_review)
    if total == 0:
        return ""
    
    greeting_name = display_name.split()[0] if display_name else "there"
    lines = [f"*PR Reminder* - {datetime.now().strftime('%b %d, %Y')}\n"]
    lines.append(f"Hi {greeting_name}! You have *{total}* PR(s) that need attention:\n")
    
    open_prs_count = len(stale_prs) + len(approved_prs) + len(stale_drafts)
    if open_prs_count > 0:
        lines.append("*📂 Your Open PRs:*")
        
        if stale_prs:
            lines.append("\n  _⏰ Inactive:_")
            for item in stale_prs:
                pr = item["pr"]
                hours = item["hours_inactive"]
                base = item.get("base_branch", "")
                base_str = f" → `{base}`" if base else ""
                time_str = f"{int(hours // 24)}d {int(hours % 24)}h" if hours >= 24 else f"{int(hours)}h"
                lines.append(f"    • <{pr['html_url']}|PR #{pr['number']}>{base_str}: \"{pr['title'][:50]}\" - {time_str}")
        
        if approved_prs:
            lines.append("\n  _✅ Approved - Awaiting Merge:_")
            for item in approved_prs:
                pr = item["pr"]
                base = item.get("base_branch", "")
                base_str = f" → `{base}`" if base else ""
                lines.append(f"    • <{pr['html_url']}|PR #{pr['number']}>{base_str}: \"{pr['title'][:50]}\" - {item['days_since_approval']}d ago")
        
        if stale_drafts:
            lines.append("\n  _📝 Stale Drafts:_")
            for item in stale_drafts:
                pr = item["pr"]
                base = item.get("base_branch", "")
                base_str = f" → `{base}`" if base else ""
                lines.append(f"    • <{pr['html_url']}|PR #{pr['number']}>{base_str}: \"{pr['title'][:50]}\" - Draft for {item['days_as_draft']}d")
    
    if awaiting_your_review:
        lines.append("\n*👀 PRs Awaiting Your Review:*")
        for item in awaiting_your_review:
            pr = item["pr"]
            hours = item.get("hours_waiting", 0)
            author = item.get("author", "Unknown")
            sla_indicator = "🔴" if hours >= 24 else "🟢"
            time_str = f"{int(hours // 24)}d {int(hours % 24)}h" if hours >= 24 else f"{int(hours)}h"
            lines.append(f"  • {sla_indicator} <{pr['html_url']}|PR #{pr['number']}>: \"{pr['title'][:50]}\" by {author} - {time_str}")
    
    lines.append("\n---")
    lines.append("Please take a moment to review these PRs. If any are stalled, we would like to understand the blockers so I can help move them forward. Is the inactivity due to:")
    lines.append("a) Pending reviews (stakeholders or area-experts)?")
    lines.append("b) Technical hurdles or shifting priorities?")
    lines.append("Let me know where we can step in to clear the path or nudge the right/concerned folks.")
    
    return "\n".join(lines)

def send_reminders(repos: list, usernames: list, config: dict = None, dry_run: bool = False):
    if config is None:
        config = load_config()
    
    hours_last_activity = config.get("hours_last_activity", 24)
    days_draft_stale = config.get("days_draft_stale", 7)
    days_approved_not_merged = config.get("days_approved_not_merged", 1)
    exclude_drafts = config.get("exclude_drafts", True)
    exclude_cherrypicks = config.get("exclude_cherrypicks", False)
    days_back = config.get("days_back", 90)
    
    user_slack_map = config.get("user_slack_mapping") or get_user_slack_mapping()
    user_display_names = config.get("user_display_names") or get_user_display_names()
    results = []
    
    awaiting_review_all = search_review_requested_prs(repos, usernames)
    
    for username in usernames:
        stale = find_stale_prs(repos, username, hours_last_activity, exclude_drafts, days_back, exclude_cherrypicks)
        stale_drafts = [] if exclude_drafts else find_stale_drafts(repos, username, days_draft_stale, days_back, exclude_cherrypicks)
        approved = find_approved_not_merged(repos, username, days_approved_not_merged, exclude_drafts, days_back, exclude_cherrypicks)
        
        awaiting_review_prs = awaiting_review_all.get(username, [])
        if exclude_drafts:
            awaiting_review_prs = [pr for pr in awaiting_review_prs if not pr.get("draft", False)]
        
        pr_list = []
        for pr in awaiting_review_prs:
            owner = pr.get("_owner", "")
            repo = pr.get("_repo", "")
            if not owner or not repo:
                owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
            pr_number = pr.get("number")
            if owner and repo and pr_number:
                pr_list.append((owner, repo, pr_number))
        
        all_pr_data = get_multiple_prs_full_details(pr_list) if pr_list else {}
        
        awaiting_review_items = []
        now = datetime.utcnow()
        for pr in awaiting_review_prs:
            owner = pr.get("_owner", "")
            repo = pr.get("_repo", "")
            if not owner or not repo:
                owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
            pr_number = pr.get("number")
            
            pr_details = all_pr_data.get((owner, repo, pr_number), {})
            reviews = pr_details.get("reviews", [])
            
            user_has_reviewed = any(
                r.get("user", {}).get("login", "").lower() == username.lower()
                for r in reviews
            )
            
            if user_has_reviewed:
                continue
            
            created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
            hours_waiting = int((now - created_at).total_seconds() / 3600)
            awaiting_review_items.append({
                "pr": pr,
                "hours_waiting": hours_waiting,
                "author": pr.get("user", {}).get("login", "Unknown")
            })
        
        display_name = user_display_names.get(username)
        message = format_reminder_message(username, stale, approved, stale_drafts, awaiting_review_items, display_name)
        
        if not message:
            results.append({"user": username, "status": "no_prs", "message": ""})
            continue
        
        slack_id = user_slack_map.get(username)
        
        if dry_run:
            results.append({"user": username, "status": "dry_run", "message": message, "slack_id": slack_id})
            print(f"\n--- Message for {username} (Slack: {slack_id or 'NOT MAPPED'}) ---")
            print(message)
            continue
        
        if not slack_id:
            results.append({"user": username, "status": "no_slack_id", "message": message})
            print(f"Warning: No Slack ID for {username}")
            continue
        
        success = send_slack_dm(slack_id, message)
        results.append({"user": username, "status": "sent" if success else "failed", "message": message})
    
    return results

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Send PR reminder Slack DMs")
    parser.add_argument("--dry-run", action="store_true", help="Preview messages without sending")
    parser.add_argument("--check-schedule", action="store_true", help="Check if any scheduled reminders should run now")
    parser.add_argument("--force", action="store_true", help="Force send to all users, ignoring schedule")
    args = parser.parse_args()
    
    config = get_config_with_defaults()
    repos = config.get("repos")
    all_usernames = config.get("usernames")
    
    if args.check_schedule and not args.force:
        users_to_notify = get_users_to_notify(config)
        if not users_to_notify:
            print(f"[{datetime.now().isoformat()}] No scheduled reminders to send at this time.")
            exit(0)
        print(f"[{datetime.now().isoformat()}] Scheduled reminders for: {', '.join(users_to_notify)}")
        usernames = users_to_notify
    else:
        usernames = all_usernames
    
    print(f"Running PR reminder ({'DRY RUN' if args.dry_run else 'LIVE'})...")
    print(f"Checking {len(usernames)} users")
    print(f"Config: {json.dumps({k: v for k, v in config.items() if k not in ['slack_bot_token', 'user_slack_mapping']}, indent=2)}\n")
    
    results = send_reminders(repos, usernames, config, dry_run=args.dry_run)
    
    if not args.dry_run and args.check_schedule:
        sent_users = [r['user'] for r in results if r['status'] == 'sent']
        if sent_users:
            update_last_run_for_users(sent_users)
            print(f"Updated last run time for: {', '.join(sent_users)}")
    
    print(f"\n--- Summary ---")
    for r in results:
        print(f"{r['user']}: {r['status']}")
