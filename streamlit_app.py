import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
from github_api import (
    search_prs, get_pr_details, get_pr_reviews, get_pr_comments, get_pr_review_comments,
    parse_repo_from_url, get_first_approval_time, get_last_comment_time, get_last_activity_time
)
from slack_notifier import load_config, save_config, send_reminders, get_config_with_defaults

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "slack_config.json")

st.set_page_config(page_title="PR Activity Tracker", page_icon="📊", layout="wide")
st.title("📊 PR Activity Tracker")

saved_config = get_config_with_defaults()
saved_repos = saved_config.get("repos")
saved_usernames = saved_config.get("usernames")

with st.sidebar:
    st.header("⚙️ Configuration")
    
    repos_input = st.text_area("GitHub Repositories (one per line, e.g. snowflakedb/frostdb)", value="\n".join(saved_repos))
    all_repos = [r.strip() for r in repos_input.strip().split("\n") if r.strip()]
    
    usernames_input = st.text_area(
        "Participants List (GitHub usernames, one per line)",
        value="\n".join(saved_usernames),
        help="Update these to your team's actual GitHub usernames"
    )
    all_usernames = [u.strip() for u in usernames_input.strip().split("\n") if u.strip()]
    
    if st.button("💾 Save Config"):
        current_config = load_config()
        current_config.update({
            "repos": all_repos,
            "usernames": all_usernames
        })
        save_config(current_config)
        st.success("Configuration saved!")
        st.rerun()
    
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
    
    exclude_cherrypicks = st.checkbox(
        "Exclude Cherry-Pick PRs",
        value=True,
        help="Filter out PRs with 'cherry-pick', 'cherrypick', or 'cp-' in the title"
    )
    
    if st.button("🔄 Refresh Data", type="primary"):
        st.cache_data.clear()

if not all_repos or not selected_users:
    st.warning("Please configure repositories and select at least one user.")
    st.stop()

st.caption(f"Showing **{len(selected_users)}** team member(s) | **{pr_state}** PRs | Last **{days_back}** days")

def is_cherrypick_pr(title: str) -> bool:
    title_lower = title.lower()
    return any(pattern in title_lower for pattern in ['cherry-pick', 'cherrypick', 'cherry pick', '[cp]', '(cp)'])

def display_open_prs(prs, exclude_cherrypicks=False):
    if exclude_cherrypicks:
        prs = [pr for pr in prs if not is_cherrypick_pr(pr.get("title", ""))]
    
    if not prs:
        st.info("No open PRs found.")
        return
    
    rows = []
    progress = st.progress(0)
    now = datetime.utcnow()
    
    for i, pr in enumerate(prs):
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        reviews = get_pr_reviews(owner, repo, pr_number) if owner and repo else []
        issue_comments = get_pr_comments(owner, repo, pr_number) if owner and repo else []
        review_comments = get_pr_review_comments(owner, repo, pr_number) if owner and repo else []
        details = get_pr_details(owner, repo, pr_number) if owner and repo else None
        first_approval = get_first_approval_time(reviews)
        last_activity = get_last_activity_time(issue_comments, review_comments, reviews)
        base_branch = details.get("base", {}).get("ref", "—") if details else "—"
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        
        is_draft = pr.get("draft", False)
        
        last_activity_dt = last_activity.replace(tzinfo=None) if last_activity else created_at.replace(tzinfo=None)
        hours_inactive = int((now - last_activity_dt).total_seconds() / 3600)
        
        attention_reasons = []
        if hours_inactive >= 24:
            attention_reasons.append(f"⏰ {hours_inactive}h inactive")
        if first_approval and (now - first_approval.replace(tzinfo=None)).days >= 1:
            attention_reasons.append("✅ Approved, not merged")
        if is_draft and (now - created_at.replace(tzinfo=None)).days >= 7:
            attention_reasons.append("📝 Stale draft")
        
        rows.append({
            "Author": pr.get("user", {}).get("login", "Unknown"),
            "Repository": f"{owner}/{repo}",
            "PR #": pr_number,
            "Title": pr.get("title", ""),
            "Base": base_branch,
            "Draft": "📝" if is_draft else "",
            "Submit Time": created_at.strftime("%Y-%m-%d %H:%M"),
            "Last Activity": last_activity_dt.strftime("%Y-%m-%d %H:%M") if last_activity else "—",
            "First Approval": first_approval.strftime("%Y-%m-%d %H:%M") if first_approval else "—",
            "Age (days)": (now - created_at.replace(tzinfo=None)).days,
            "Needs Attention": " | ".join(attention_reasons) if attention_reasons else "",
            "URL": pr.get("html_url", "")
        })
        progress.progress((i + 1) / len(prs))
    
    progress.empty()
    df = pd.DataFrame(rows)
    
    needs_attention_count = len(df[df["Needs Attention"] != ""])
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Open PRs", len(df))
    col2.metric("Needs Attention", needs_attention_count)
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
        base_branch = details.get("base", {}).get("ref", "—") if details else "—"
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        closed_at = datetime.fromisoformat(pr["closed_at"].replace("Z", "+00:00")) if pr.get("closed_at") else None
        
        rows.append({
            "Author": pr.get("user", {}).get("login", "Unknown"),
            "Repository": f"{owner}/{repo}",
            "PR #": pr_number,
            "Title": pr.get("title", ""),
            "Base": base_branch,
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
        open_prs = search_prs(all_repos, selected_users, state="open", days_back=days_back)
    display_open_prs(open_prs, exclude_cherrypicks)

elif pr_state == "Closed":
    st.subheader("🔴 Closed Pull Requests")
    with st.spinner("Fetching closed PRs..."):
        closed_prs = search_prs(all_repos, selected_users, state="closed", days_back=days_back)
    display_closed_prs(closed_prs)

else:
    tab_open, tab_closed = st.tabs(["🟢 Open PRs", "🔴 Closed PRs"])
    
    with tab_open:
        with st.spinner("Fetching open PRs..."):
            open_prs = search_prs(all_repos, selected_users, state="open", days_back=days_back)
        display_open_prs(open_prs, exclude_cherrypicks)
    
    with tab_closed:
        with st.spinner("Fetching closed PRs..."):
            closed_prs = search_prs(all_repos, selected_users, state="closed", days_back=days_back)
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
    
    my_slack_id = st.text_input(
        "My Slack ID (for CC myself)",
        value=slack_config.get("my_slack_id", ""),
        help="Your own Slack Member ID to receive copies of sent messages",
        placeholder="U0123456789"
    )
    
    st.subheader("GitHub → Slack User Mapping")
    st.caption("Enter Display Name and Slack Member ID for each GitHub user")
    
    user_mapping = slack_config.get("user_slack_mapping", {})
    user_names = slack_config.get("user_display_names", {})
    new_mapping = {}
    new_names = {}
    
    for username in all_usernames:
        col_gh, col_name, col_id = st.columns([2, 2, 2])
        with col_gh:
            st.text_input("GitHub", value=username, disabled=True, key=f"gh_{username}")
        with col_name:
            new_names[username] = st.text_input(
                "Display Name",
                value=user_names.get(username, ""),
                key=f"name_{username}",
                placeholder="First Last"
            )
        with col_id:
            new_mapping[username] = st.text_input(
                "Slack ID",
                value=user_mapping.get(username, ""),
                key=f"slack_{username}",
                placeholder="U0123456789"
            )
    
    st.subheader("Additional Slack Contacts (CC only)")
    st.caption("Add people without GitHub accounts who can be CC'd on messages")
    
    additional_contacts = slack_config.get("additional_slack_contacts", {})
    
    if "new_contacts" not in st.session_state:
        st.session_state.new_contacts = dict(additional_contacts)
    
    col_name, col_id, col_add = st.columns([2, 2, 1])
    with col_name:
        new_contact_name = st.text_input("Name", key="new_contact_name", placeholder="John Doe")
    with col_id:
        new_contact_id = st.text_input("Slack ID", key="new_contact_id", placeholder="U0123456789")
    with col_add:
        st.write("")  # spacing
        if st.button("➕ Add", key="add_contact_btn"):
            if new_contact_name and new_contact_id:
                st.session_state.new_contacts[new_contact_name] = new_contact_id
                st.rerun()
    
    if st.session_state.new_contacts:
        st.write("**Current additional contacts:**")
        contacts_to_remove = []
        for name, slack_id in st.session_state.new_contacts.items():
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                st.text(name)
            with col2:
                st.text(slack_id)
            with col3:
                if st.button("🗑️", key=f"remove_{name}"):
                    contacts_to_remove.append(name)
        for name in contacts_to_remove:
            del st.session_state.new_contacts[name]
            st.rerun()
    
    if st.button("💾 Save Slack Configuration"):
        current_config = load_config()
        current_config.update({
            "slack_bot_token": new_token,
            "my_slack_id": my_slack_id,
            "repos": all_repos,
            "usernames": all_usernames,
            "user_slack_mapping": new_mapping,
            "user_display_names": new_names,
            "additional_slack_contacts": st.session_state.new_contacts,
            "hours_last_activity": hours_last_activity,
            "days_draft_stale": days_draft_stale,
            "days_approved_not_merged": days_approved_not_merged,
            "exclude_drafts": exclude_drafts
        })
        save_config(current_config)
        st.success("Configuration saved!")
        st.rerun()

with st.expander("Preview & Send Reminders", expanded=False):
    st.caption("Preview what messages would be sent, edit if needed, then send")
    
    github_slack_users = {k: v for k, v in slack_config.get("user_slack_mapping", {}).items() if v}
    user_display_names = slack_config.get("user_display_names", {})
    additional_contacts = st.session_state.get("new_contacts", slack_config.get("additional_slack_contacts", {}))
    
    cc_options_map = {}
    for gh_user, slack_id in github_slack_users.items():
        display = user_display_names.get(gh_user) or gh_user
        cc_options_map[display] = {"type": "github", "github": gh_user, "slack_id": slack_id}
    for name, slack_id in additional_contacts.items():
        cc_options_map[f"📋 {name}"] = {"type": "additional", "slack_id": slack_id}
    
    all_cc_options = list(cc_options_map.keys())
    col_cc, col_myself = st.columns([3, 1])
    with col_cc:
        cc_recipients = st.multiselect(
            "CC additional recipients",
            options=all_cc_options,
            default=[],
            help="These people will receive a copy of all messages sent",
            key="cc_recipients"
        )
    with col_myself:
        my_slack_id_configured = slack_config.get("my_slack_id", "")
        cc_myself = st.checkbox(
            "CC myself",
            value=False,
            disabled=not my_slack_id_configured,
            help="Send a copy to yourself" if my_slack_id_configured else "Configure 'My Slack ID' in Slack Integration first"
        )
    
    reminder_config = {
        "hours_last_activity": hours_last_activity,
        "days_draft_stale": days_draft_stale,
        "days_approved_not_merged": days_approved_not_merged,
        "exclude_drafts": exclude_drafts,
        "days_back": days_back,
        "user_display_names": slack_config.get("user_display_names", {}),
        "user_slack_mapping": slack_config.get("user_slack_mapping", {})
    }
    
    if "preview_messages" not in st.session_state:
        st.session_state.preview_messages = {}
    if "preview_users" not in st.session_state:
        st.session_state.preview_users = []
    
    if set(st.session_state.preview_users) != set(selected_users):
        st.session_state.preview_messages = {}
        st.session_state.preview_users = selected_users.copy()
    
    if st.button("👁️ Generate Preview"):
        fresh_config = load_config()
        reminder_config.update({
            "user_display_names": fresh_config.get("user_display_names", {}),
            "user_slack_mapping": fresh_config.get("user_slack_mapping", {})
        })
        with st.spinner("Analyzing PRs..."):
            results = send_reminders(all_repos, selected_users, reminder_config, dry_run=True)
        st.session_state.preview_messages = {
            r["user"]: {"message": r["message"], "slack_id": r.get("slack_id"), "status": r["status"]}
            for r in results
        }
        st.session_state.preview_users = selected_users.copy()
        st.rerun()
    
    edited_messages = {}
    if st.session_state.preview_messages:
        for user, data in st.session_state.preview_messages.items():
            if data["status"] == "no_prs":
                st.info(f"**{user}**: No PRs need attention")
            else:
                slack_id = data.get("slack_id", "NOT MAPPED")
                st.markdown(f"**{user}** (Slack: `{slack_id}`)")
                edited_messages[user] = st.text_area(
                    f"Message for {user}",
                    value=data["message"],
                    height=250,
                    key=f"msg_{user}",
                    label_visibility="collapsed"
                )
        
        if cc_recipients:
            st.info(f"📋 CC: {', '.join(cc_recipients)}")
        
        st.divider()
        if st.button("🚀 Send Edited Messages", type="primary"):
            token = slack_config.get("slack_bot_token", "")
            if not token or token == "xoxb-YOUR-BOT-TOKEN-HERE":
                st.error("Please configure a valid Slack Bot Token first!")
            else:
                from slack_notifier import send_slack_dm, get_user_slack_mapping
                user_slack_map = get_user_slack_mapping()
                
                sent, failed, no_slack, cc_sent, cc_failed = 0, 0, 0, 0, 0
                for user, message in edited_messages.items():
                    if not message.strip():
                        continue
                    slack_id = user_slack_map.get(user)
                    if not slack_id or slack_id == "U_SLACK_ID_HERE":
                        st.write(f"⚠️ {user} - No Slack ID configured")
                        no_slack += 1
                        continue
                    
                    success = send_slack_dm(slack_id, message)
                    if success:
                        st.write(f"✅ {user}")
                        sent += 1
                    else:
                        st.write(f"❌ {user} - Failed to send")
                        failed += 1
                    
                    for cc_display_name in cc_recipients:
                        cc_info = cc_options_map.get(cc_display_name, {})
                        if cc_info.get("type") == "github" and cc_info.get("github") == user:
                            continue
                        cc_slack_id = cc_info.get("slack_id")
                        if cc_slack_id:
                            cc_message = f"📋 *CC - Message sent to {user}:*\n\n{message}"
                            if send_slack_dm(cc_slack_id, cc_message):
                                cc_sent += 1
                            else:
                                cc_failed += 1
                
                if cc_myself and my_slack_id_configured:
                    for user, message in edited_messages.items():
                        if not message.strip():
                            continue
                        cc_message = f"📋 *CC - Message sent to {user}:*\n\n{message}"
                        if send_slack_dm(my_slack_id_configured, cc_message):
                            cc_sent += 1
                        else:
                            cc_failed += 1
                
                summary = f"Sent: {sent} | Failed: {failed} | No Slack ID: {no_slack}"
                if cc_recipients or cc_myself:
                    summary += f" | CC sent: {cc_sent} | CC failed: {cc_failed}"
                st.success(summary)

st.divider()
st.caption("""
**Automated Monday Reminders:** Run `crontab -e` and add:
```
0 9 * * 1 cd /Users/ahusain/pr-dashboard && ./venv/bin/python slack_notifier.py
```
""")
