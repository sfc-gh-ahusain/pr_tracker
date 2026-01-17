import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
from github_api import (
    search_prs, get_pr_details, get_pr_reviews, get_pr_comments,
    parse_repo_from_url, get_first_approval_time, get_last_comment_time
)
from slack_notifier import load_config, save_config, send_reminders

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "slack_config.json")

st.set_page_config(page_title="PR Activity Tracker", page_icon="📊", layout="wide")
st.title("📊 PR Activity Tracker")

DEFAULT_ORGS = ["frostdb", "fdb-tls-tools", "snowflakedb"]
DEFAULT_USERNAMES = [
    "sfc-gh-alfeng", "sfc-gh-huliu", "sfc-gh-juliu", "sfc-gh-imubarek",
    "sfc-gh-ynannapaneni", "sfc-gh-speddi", "sfc-gh-tpendock", "sfc-gh-bravi",
    "sfc-gh-jshim", "sfc-gh-nwijetunga", "sfc-gh-hoyang"
]

with st.sidebar:
    st.header("⚙️ Configuration")
    
    orgs_input = st.text_area("GitHub Organizations (one per line)", value="\n".join(DEFAULT_ORGS))
    all_orgs = [o.strip() for o in orgs_input.strip().split("\n") if o.strip()]
    
    usernames_input = st.text_area(
        "Direct Reports GitHub Usernames (one per line)",
        value="\n".join(DEFAULT_USERNAMES),
        help="Update these to your team's actual GitHub usernames"
    )
    all_usernames = [u.strip() for u in usernames_input.strip().split("\n") if u.strip()]
    
    st.divider()
    st.subheader("🎯 Filters")
    
    select_all = st.checkbox("Select All Team Members", value=True)
    
    if select_all:
        selected_users = all_usernames
        st.multiselect(
            "Team Members",
            options=all_usernames,
            default=all_usernames,
            disabled=True,
            help="Uncheck 'Select All' to pick individuals"
        )
    else:
        selected_users = st.multiselect(
            "Team Members",
            options=all_usernames,
            default=[],
            help="Choose one or more team members"
        )
    
    pr_state = st.radio(
        "PR Status",
        options=["Open", "Closed", "Both"],
        horizontal=True
    )
    
    days_back = st.slider("Days to look back", 7, 365, 90)
    
    if st.button("🔄 Refresh Data", type="primary"):
        st.cache_data.clear()

if not all_orgs or not selected_users:
    st.warning("Please configure organizations and select at least one user.")
    st.stop()

st.caption(f"Showing **{len(selected_users)}** team member(s) | **{pr_state}** PRs | Last **{days_back}** days")

def display_open_prs(prs):
    if not prs:
        st.info("No open PRs found.")
        return
    
    rows = []
    progress = st.progress(0)
    for i, pr in enumerate(prs):
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        reviews = get_pr_reviews(owner, repo, pr_number) if owner and repo else []
        comments = get_pr_comments(owner, repo, pr_number) if owner and repo else []
        first_approval = get_first_approval_time(reviews)
        last_comment = get_last_comment_time(comments)
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        
        is_draft = pr.get("draft", False)
        
        rows.append({
            "Author": pr.get("user", {}).get("login", "Unknown"),
            "Repository": f"{owner}/{repo}",
            "PR #": pr_number,
            "Title": pr.get("title", ""),
            "Draft": "📝 Draft" if is_draft else "",
            "Submit Time": created_at.strftime("%Y-%m-%d %H:%M"),
            "Last Comment": last_comment.strftime("%Y-%m-%d %H:%M") if last_comment else "—",
            "First Approval": first_approval.strftime("%Y-%m-%d %H:%M") if first_approval else "—",
            "Age (days)": (datetime.utcnow() - created_at.replace(tzinfo=None)).days,
            "URL": pr.get("html_url", "")
        })
        progress.progress((i + 1) / len(prs))
    
    progress.empty()
    df = pd.DataFrame(rows)
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Open PRs", len(df))
    col2.metric("Drafts", len(df[df["Draft"] != ""]))
    col3.metric("Awaiting Approval", len(df[df["First Approval"] == "—"]))
    col4.metric("Avg Age (days)", f"{df['Age (days)'].mean():.1f}" if len(df) > 0 else "—")
    
    st.dataframe(
        df,
        column_config={
            "URL": st.column_config.LinkColumn("Link", display_text="View"),
            "Age (days)": st.column_config.NumberColumn(format="%d days")
        },
        use_container_width=True,
        hide_index=True
    )

def display_closed_prs(prs):
    if not prs:
        st.info("No closed PRs found.")
        return
    
    rows = []
    progress = st.progress(0)
    for i, pr in enumerate(prs):
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        details = get_pr_details(owner, repo, pr_number) if owner and repo else None
        additions = details.get("additions", 0) if details else 0
        deletions = details.get("deletions", 0) if details else 0
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        closed_at = datetime.fromisoformat(pr["closed_at"].replace("Z", "+00:00")) if pr.get("closed_at") else None
        
        rows.append({
            "Author": pr.get("user", {}).get("login", "Unknown"),
            "Repository": f"{owner}/{repo}",
            "PR #": pr_number,
            "Title": pr.get("title", ""),
            "Merged": "✅" if (details and details.get("merged")) else "❌",
            "Created": created_at.strftime("%Y-%m-%d"),
            "Closed": closed_at.strftime("%Y-%m-%d") if closed_at else "—",
            "Lines Added": additions,
            "Lines Deleted": deletions,
            "Total Lines": additions + deletions,
            "URL": pr.get("html_url", "")
        })
        progress.progress((i + 1) / len(prs))
    
    progress.empty()
    df = pd.DataFrame(rows)
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Closed PRs", len(df))
    col2.metric("Merged", len(df[df["Merged"] == "✅"]))
    col3.metric("Total Lines Changed", f"{df['Total Lines'].sum():,}")
    col4.metric("Avg Lines/PR", f"{df['Total Lines'].mean():.0f}" if len(df) > 0 else "—")
    
    st.dataframe(
        df,
        column_config={
            "URL": st.column_config.LinkColumn("Link", display_text="View"),
            "Lines Added": st.column_config.NumberColumn(format="%d ➕"),
            "Lines Deleted": st.column_config.NumberColumn(format="%d ➖"),
            "Total Lines": st.column_config.NumberColumn(format="%d")
        },
        use_container_width=True,
        hide_index=True
    )
    
    st.subheader("📈 Lines Changed by Author")
    author_stats = df.groupby("Author").agg({
        "Total Lines": "sum",
        "PR #": "count"
    }).rename(columns={"PR #": "PR Count"}).sort_values("Total Lines", ascending=False)
    st.bar_chart(author_stats["Total Lines"])

if pr_state == "Open":
    st.subheader("🟢 Open Pull Requests")
    with st.spinner("Fetching open PRs..."):
        open_prs = search_prs(all_orgs, selected_users, state="open", days_back=days_back)
    display_open_prs(open_prs)

elif pr_state == "Closed":
    st.subheader("🔴 Closed Pull Requests")
    with st.spinner("Fetching closed PRs..."):
        closed_prs = search_prs(all_orgs, selected_users, state="closed", days_back=days_back)
    display_closed_prs(closed_prs)

else:
    tab_open, tab_closed = st.tabs(["🟢 Open PRs", "🔴 Closed PRs"])
    
    with tab_open:
        with st.spinner("Fetching open PRs..."):
            open_prs = search_prs(all_orgs, selected_users, state="open", days_back=days_back)
        display_open_prs(open_prs)
    
    with tab_closed:
        with st.spinner("Fetching closed PRs..."):
            closed_prs = search_prs(all_orgs, selected_users, state="closed", days_back=days_back)
        display_closed_prs(closed_prs)

st.divider()
st.header("🔔 Slack Reminders Configuration")

slack_config = load_config()

st.subheader("Reminder Thresholds")

col1, col2, col3, col4 = st.columns(4)
with col4:
    exclude_drafts = st.checkbox(
        "Exclude all draft PR reminders",
        value=slack_config.get("exclude_drafts", True),
        help="When checked, draft PRs are excluded from all reminders",
        key="exclude_drafts_check"
    )
with col1:
    hours_last_activity = st.number_input(
        "Last activity (hours)",
        min_value=1, max_value=168, value=slack_config.get("hours_last_activity", 24),
        help="Remind if no activity for this many hours",
        key="hours_last_activity"
    )
with col2:
    days_draft_stale = st.number_input(
        "Draft stale (days)",
        min_value=1, max_value=30, value=slack_config.get("days_draft_stale", 7),
        help="Remind if PR is draft for this many days",
        key="days_draft_stale",
        disabled=exclude_drafts
    )
with col3:
    days_approved_not_merged = st.number_input(
        "Approved not merged (days)",
        min_value=1, max_value=14, value=slack_config.get("days_approved_not_merged", 1),
        help="Remind if approved but not merged after this many days",
        key="days_approved_not_merged"
    )

with st.expander("Configure Slack Integration", expanded=False):
    st.markdown("""
    **Setup Steps:**
    1. Create a Slack App at https://api.slack.com/apps
    2. Add Bot Token Scopes: `chat:write`, `users:read`, `im:write`
    3. Install to workspace and copy the Bot Token
    4. Get each user's Slack Member ID (Profile → More → Copy member ID)
    """)
    
    new_token = st.text_input(
        "Slack Bot Token",
        value=slack_config.get("slack_bot_token", ""),
        type="password",
        help="Starts with xoxb-"
    )
    
    st.subheader("GitHub → Slack User Mapping")
    st.caption("Enter Slack Member ID for each GitHub user")
    
    user_mapping = slack_config.get("user_slack_mapping", {})
    new_mapping = {}
    
    cols = st.columns(2)
    for i, username in enumerate(all_usernames):
        with cols[i % 2]:
            new_mapping[username] = st.text_input(
                f"{username}",
                value=user_mapping.get(username, ""),
                key=f"slack_{username}",
                placeholder="U0123456789"
            )
    
    if st.button("💾 Save Slack Configuration"):
        new_config = {
            "slack_bot_token": new_token,
            "orgs": all_orgs,
            "usernames": all_usernames,
            "user_slack_mapping": new_mapping,
            "hours_last_activity": hours_last_activity,
            "days_draft_stale": days_draft_stale,
            "days_approved_not_merged": days_approved_not_merged,
            "exclude_drafts": exclude_drafts
        }
        save_config(new_config)
        st.success("Configuration saved!")

with st.expander("Preview & Send Reminders", expanded=False):
    st.caption("Preview what messages would be sent before actually sending them")
    
    col1, col2 = st.columns(2)
    
    reminder_config = {
        "hours_last_activity": hours_last_activity,
        "days_draft_stale": days_draft_stale,
        "days_approved_not_merged": days_approved_not_merged,
        "exclude_drafts": exclude_drafts
    }
    
    with col1:
        if st.button("👁️ Preview Messages (Dry Run)"):
            with st.spinner("Analyzing PRs..."):
                results = send_reminders(all_orgs, selected_users, reminder_config, dry_run=True)
            
            for r in results:
                if r["status"] == "no_prs":
                    st.info(f"**{r['user']}**: No PRs need attention")
                else:
                    with st.container():
                        slack_id = r.get("slack_id", "NOT MAPPED")
                        st.markdown(f"**{r['user']}** (Slack: `{slack_id}`)")
                        st.code(r["message"], language=None)
    
    with col2:
        if st.button("🚀 Send Reminders NOW", type="primary"):
            token = slack_config.get("slack_bot_token", "")
            if not token or token == "xoxb-YOUR-BOT-TOKEN-HERE":
                st.error("Please configure a valid Slack Bot Token first!")
            else:
                with st.spinner("Sending Slack DMs..."):
                    results = send_reminders(all_orgs, selected_users, reminder_config, dry_run=False)
                
                sent = sum(1 for r in results if r["status"] == "sent")
                failed = sum(1 for r in results if r["status"] == "failed")
                no_slack = sum(1 for r in results if r["status"] == "no_slack_id")
                
                st.success(f"Sent: {sent} | Failed: {failed} | No Slack ID: {no_slack}")
                
                for r in results:
                    if r["status"] == "sent":
                        st.write(f"✅ {r['user']}")
                    elif r["status"] == "failed":
                        st.write(f"❌ {r['user']} - Failed to send")
                    elif r["status"] == "no_slack_id":
                        st.write(f"⚠️ {r['user']} - No Slack ID configured")

st.divider()
st.caption("""
**Automated Monday Reminders:** Run `crontab -e` and add:
```
0 9 * * 1 cd /Users/ahusain/pr-dashboard && ./venv/bin/python slack_notifier.py
```
""")
