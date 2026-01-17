import os
import json
import requests
from datetime import datetime, timedelta
from github_api import search_prs, get_pr_reviews, get_pr_comments, parse_repo_from_url, get_first_approval_time, get_last_comment_time

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "slack_config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_slack_token():
    config = load_config()
    return config.get("slack_bot_token", os.environ.get("SLACK_BOT_TOKEN", ""))

def get_user_slack_mapping():
    config = load_config()
    return config.get("user_slack_mapping", {})

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

def find_stale_prs(orgs: list, username: str, days_inactive: int = 7) -> list:
    prs = search_prs(orgs, [username], state="open", days_back=90)
    stale = []
    now = datetime.utcnow()
    
    for pr in prs:
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        comments = get_pr_comments(owner, repo, pr_number) if owner and repo else []
        last_comment = get_last_comment_time(comments)
        
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        last_activity = last_comment.replace(tzinfo=None) if last_comment else created_at
        
        days_since_activity = (now - last_activity).days
        
        if days_since_activity >= days_inactive:
            stale.append({
                "pr": pr,
                "days_inactive": days_since_activity,
                "reason": "no_activity"
            })
    
    return stale

def find_approved_not_merged(orgs: list, username: str) -> list:
    prs = search_prs(orgs, [username], state="open", days_back=90)
    approved_pending = []
    
    for pr in prs:
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        reviews = get_pr_reviews(owner, repo, pr_number) if owner and repo else []
        first_approval = get_first_approval_time(reviews)
        
        if first_approval:
            days_since_approval = (datetime.utcnow() - first_approval.replace(tzinfo=None)).days
            approved_pending.append({
                "pr": pr,
                "days_since_approval": days_since_approval,
                "reason": "approved_not_merged"
            })
    
    return approved_pending

def format_reminder_message(username: str, stale_prs: list, approved_prs: list) -> str:
    if not stale_prs and not approved_prs:
        return ""
    
    lines = [f"*Weekly PR Reminder* - {datetime.now().strftime('%b %d, %Y')}\n"]
    lines.append(f"Hi! You have *{len(stale_prs) + len(approved_prs)}* PR(s) that need attention:\n")
    
    if stale_prs:
        lines.append("*No Activity:*")
        for item in stale_prs:
            pr = item["pr"]
            lines.append(f"  • <{pr['html_url']}|PR #{pr['number']}>: \"{pr['title'][:50]}\" - No activity for {item['days_inactive']} days")
    
    if approved_prs:
        lines.append("\n*Approved - Awaiting Merge:*")
        for item in approved_prs:
            pr = item["pr"]
            lines.append(f"  • <{pr['html_url']}|PR #{pr['number']}>: \"{pr['title'][:50]}\" - Approved {item['days_since_approval']} days ago")
    
    return "\n".join(lines)

def send_reminders(orgs: list, usernames: list, days_inactive: int = 7, dry_run: bool = False):
    user_slack_map = get_user_slack_mapping()
    results = []
    
    for username in usernames:
        stale = find_stale_prs(orgs, username, days_inactive)
        approved = find_approved_not_merged(orgs, username)
        
        message = format_reminder_message(username, stale, approved)
        
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
    parser.add_argument("--days", type=int, default=7, help="Days of inactivity threshold")
    args = parser.parse_args()
    
    config = load_config()
    orgs = config.get("orgs", ["frostdb", "fdb-tls-tools", "snowflakedb"])
    usernames = config.get("usernames", [
        "sfc-gh-alfeng", "sfc-gh-huliu", "sfc-gh-juliu", "sfc-gh-imubarek",
        "sfc-gh-ynannapaneni", "sfc-gh-speddi", "sfc-gh-tpendock", "sfc-gh-bravi",
        "sfc-gh-jshim", "sfc-gh-nwijetunga", "sfc-gh-hoyang"
    ])
    
    print(f"Running PR reminder ({'DRY RUN' if args.dry_run else 'LIVE'})...")
    print(f"Checking {len(usernames)} users for PRs inactive >= {args.days} days\n")
    
    results = send_reminders(orgs, usernames, args.days, dry_run=args.dry_run)
    
    print(f"\n--- Summary ---")
    for r in results:
        print(f"{r['user']}: {r['status']}")
