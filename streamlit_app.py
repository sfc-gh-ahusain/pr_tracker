import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
from github_api import (
    search_prs, get_pr_details, get_pr_reviews, get_pr_comments, get_pr_review_comments,
    parse_repo_from_url, get_first_approval_time, get_last_comment_time, get_last_activity_time,
    get_multiple_prs_full_details, search_merged_prs, search_reviewed_prs, 
    search_review_requested_prs, get_review_time_for_user, search_prs_where_user_is_reviewer
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
    
    col_save, col_clear = st.columns(2)
    with col_save:
        if st.button("💾 Save Config"):
            current_config = load_config()
            current_config.update({
                "repos": all_repos,
                "usernames": all_usernames
            })
            save_config(current_config)
            st.success("Configuration saved!")
            st.rerun()
    with col_clear:
        if st.button("🔄 Clear Cache"):
            st.cache_data.clear()
            st.success("Cache cleared!")
            st.rerun()
    
    st.divider()
    st.subheader("🎯 Filters")
    
    days_back = st.slider("Days to look back", 7, 365, 15)
    
    with st.container():
        st.markdown("**⏰ Inactivity Thresholds**")
        col_thresh1, col_thresh2 = st.columns(2)
        with col_thresh1:
            inactive_open_prs_days = st.number_input(
                "Open PRs (days)",
                min_value=1,
                max_value=30,
                value=1,
                help="PRs with no activity for this many days are flagged as inactive"
            )
        with col_thresh2:
            inactive_awaiting_review_days = st.number_input(
                "Awaiting Review (days)",
                min_value=1,
                max_value=30,
                value=1,
                help="PRs awaiting review for this many days are flagged"
            )
    
    inactive_open_prs_hours = inactive_open_prs_days * 24
    inactive_awaiting_review_hours = inactive_awaiting_review_days * 24
    
    select_all = st.checkbox("Select All Team Members", value=False)
    
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
    
    st.session_state.selected_users = selected_users
    
    pr_state = st.radio(
        "PR Status",
        options=["Open", "Closed", "Both"],
        horizontal=True
    )
    
    exclude_cherrypicks = st.checkbox(
        "Exclude Cherry-Pick PRs",
        value=True,
        key="exclude_cherrypicks_checkbox",
        help="Filter out PRs targeting release/* branches or with cherry-pick in title"
    )
    
    exclude_drafts = st.checkbox(
        "Exclude Draft PRs",
        value=False,
        key="exclude_drafts_checkbox",
        help="Filter out draft PRs from the table and metrics"
    )
    
    if st.session_state.get("last_exclude_cherrypicks") != exclude_cherrypicks:
        st.session_state.last_exclude_cherrypicks = exclude_cherrypicks
        st.session_state.preview_messages = {}
    
    if st.session_state.get("last_exclude_drafts") != exclude_drafts:
        st.session_state.last_exclude_drafts = exclude_drafts
        st.session_state.preview_messages = {}
    
    if st.button("🔄 Refresh Data", type="primary"):
        st.cache_data.clear()

if not all_repos or not selected_users:
    st.warning("Please configure repositories and select at least one user.")
    st.stop()

st.caption(f"Showing **{len(selected_users)}** team member(s) | **{pr_state}** PRs | Last **{days_back}** days")

def is_cherrypick_pr(title: str, base_branch: str = "") -> bool:
    title_lower = title.lower()
    if any(pattern in title_lower for pattern in ['cherry-pick', 'cherrypick', 'cherry pick', '[cp]', '(cp)']):
        return True
    if base_branch.startswith('release/'):
        return True
    return False

def generate_preview_from_table_rows(rows, user_display_names, user_slack_mapping, consolidated=False, awaiting_review_by_user=None):
    """Generate preview messages from the same data displayed in the table (single source of truth)."""
    from datetime import datetime
    awaiting_review_by_user = awaiting_review_by_user or {}
    results = {}
    prs_by_user = {}
    for row in rows:
        if not row.get("Needs Attention"):
            continue
        author = row["Author"]
        if author not in prs_by_user:
            prs_by_user[author] = []
        prs_by_user[author].append(row)
    
    all_users = set(prs_by_user.keys()) | set(awaiting_review_by_user.keys())
    today = datetime.now().strftime("%B %d, %Y")
    
    for user in all_users:
        user_prs = prs_by_user.get(user, [])
        user_awaiting = awaiting_review_by_user.get(user, [])
        display_name = user_display_names.get(user, user)
        slack_id = user_slack_mapping.get(user)
        
        total_count = len(user_prs) + len(user_awaiting)
        if total_count == 0:
            continue
        
        lines = [
            f"*PR Reminder - {today}*",
            "",
            f"Hi {display_name}! You have {total_count} PR(s) that need attention:",
            ""
        ]
        
        if user_prs:
            lines.append("*📂 Your Open PRs:*")
            for pr in user_prs:
                attention = pr.get("Needs Attention", "")
                title = pr["Title"][:50] + "..." if len(pr["Title"]) > 50 else pr["Title"]
                pr_url = pr["PR #"]
                pr_num = pr_url.split("/")[-1] if "/" in pr_url else pr_url
                
                status_parts = []
                
                if "inactive" in attention.lower():
                    hours_match = attention.split("h inactive")[0].split("⏰ ")[-1] if "⏰" in attention else "0"
                    try:
                        hours = int(hours_match)
                        days = hours // 24
                        remaining_hours = hours % 24
                        time_str = f"{days}d {remaining_hours}h" if days > 0 else f"{remaining_hours}h"
                    except:
                        time_str = "unknown"
                    status_parts.append(f"⏰ {time_str}")
                
                if "Approved" in attention:
                    first_approval = pr.get("First Approval", "—")
                    if first_approval != "—":
                        try:
                            approval_date = datetime.strptime(first_approval, "%Y-%m-%d %H:%M")
                            days_ago = (datetime.now() - approval_date).days
                            status_parts.append(f"✅ {days_ago}d ago")
                        except:
                            status_parts.append("✅ Approved")
                
                if "Stale draft" in attention:
                    status_parts.append("📝 Draft")
                
                status_str = " | ".join(status_parts) if status_parts else ""
                line = f'  • <{pr_url}|PR #{pr_num}>: "{title}"'
                if status_str:
                    line += f" - {status_str}"
                lines.append(line)
        
        if user_awaiting:
            lines.append("")
            lines.append("*👀 PRs Awaiting Your Review:*")
            for pr_data in user_awaiting:
                pr = pr_data.get("pr", pr_data)
                pr_url = pr.get("html_url", "")
                pr_num = pr.get("number", pr_url.split("/")[-1] if "/" in pr_url else "?")
                title = pr.get("title", "")[:50]
                author = pr.get("user", {}).get("login", "unknown")
                hours = pr_data.get("hours_waiting", 0)
                sla = "🔴" if hours >= inactive_awaiting_review_hours else "🟢"
                time_str = f"{int(hours // 24)}d {int(hours % 24)}h" if hours >= inactive_awaiting_review_hours else f"{int(hours)}h"
                lines.append(f'  • {sla} <{pr_url}|PR #{pr_num}>: "{title}" by {author} - {time_str}')
        
        lines.append("")
        lines.append("---")
        lines.append("Please take a moment to review these PRs. If any are stalled, we would like to understand the blockers so I can help move them forward. Is the inactivity due to:")
        lines.append("a) Pending reviews (stakeholders or area-experts)?")
        lines.append("b) Technical hurdles or shifting priorities?")
        lines.append("Let me know where we can step in to clear the path or nudge the right/concerned folks.")
        
        results[user] = {
            "message": "\n".join(lines),
            "slack_id": slack_id,
            "status": "preview"
        }
    
    if consolidated and len(all_users) > 1:
        all_pr_entries = []
        total_prs = 0
        user_stats = []
        for user in all_users:
            user_prs = prs_by_user.get(user, [])
            user_awaiting = awaiting_review_by_user.get(user, [])
            display_name = user_display_names.get(user, user)
            inactive_count = 0
            approved_count = 0
            stale_draft_count = 0
            awaiting_count = len(user_awaiting)
            
            all_pr_entries.append(f"\n*{display_name}:*")
            
            if user_prs:
                all_pr_entries.append("  _Open PRs:_")
                for pr in user_prs:
                    attention = pr.get("Needs Attention", "")
                    title = pr["Title"][:50] + "..." if len(pr["Title"]) > 50 else pr["Title"]
                    pr_url = pr["PR #"]
                    pr_num = pr_url.split("/")[-1] if "/" in pr_url else pr_url
                    status_parts = []
                    if "inactive" in attention.lower():
                        status_parts.append("⏰")
                        inactive_count += 1
                    if "Approved" in attention:
                        status_parts.append("✅")
                        approved_count += 1
                    if "Stale draft" in attention:
                        status_parts.append("📝")
                        stale_draft_count += 1
                    status_str = " ".join(status_parts)
                    line = f'    • <{pr_url}|PR #{pr_num}>: "{title}"'
                    if status_str:
                        line += f" {status_str}"
                    all_pr_entries.append(line)
                    total_prs += 1
            
            if user_awaiting:
                all_pr_entries.append("  _Awaiting Review:_")
                for pr_data in user_awaiting:
                    pr = pr_data.get("pr", pr_data)
                    pr_url = pr.get("html_url", "")
                    pr_num = pr.get("number", "?")
                    title = pr.get("title", "")[:50]
                    all_pr_entries.append(f'    • <{pr_url}|PR #{pr_num}>: "{title}"')
                    total_prs += 1
            
            user_stats.append({
                "Name": display_name,
                "Open PRs": len(user_prs),
                "Inactive": inactive_count,
                "Approved": approved_count,
                "Drafts": stale_draft_count,
                "To Review": awaiting_count
            })
        
        consolidated_lines = [
            f"*Team PR Summary - {today}*",
            "",
            f"There are *{total_prs}* PR(s) across *{len(all_users)}* team members that need attention:",
        ]
        consolidated_lines.extend(all_pr_entries)
        consolidated_lines.extend([
            "",
            "---",
            "Please follow up with team members on stalled PRs."
        ])
        
        results["__consolidated__"] = {
            "message": "\n".join(consolidated_lines),
            "slack_id": None,
            "status": "preview",
            "user_stats": user_stats
        }
    
    return results

def display_open_prs(prs, exclude_cherrypicks=False, exclude_drafts=False):
    filtered_prs = []
    for pr in prs:
        if exclude_drafts and pr.get("draft", False):
            continue
        if exclude_cherrypicks:
            owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
            details = get_pr_details(owner, repo, pr.get("number")) if owner and repo else None
            base_branch = details.get("base", {}).get("ref", "") if details else ""
            if is_cherrypick_pr(pr.get("title", ""), base_branch):
                continue
        filtered_prs.append(pr)
    prs = filtered_prs
    
    st.session_state.filtered_prs = prs
    
    if not prs:
        st.info("No open PRs found.")
        st.session_state.pr_table_rows = []
        return
    
    rows = []
    progress = st.progress(0, text="Fetching PR details in parallel...")
    now = datetime.utcnow()
    
    pr_keys = []
    for pr in prs:
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        if owner and repo:
            pr_keys.append((owner, repo, pr_number))
    
    all_pr_data = get_multiple_prs_full_details(pr_keys)
    progress.progress(50, text="Processing PR data...")
    
    for i, pr in enumerate(prs):
        owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
        pr_number = pr.get("number")
        
        pr_data = all_pr_data.get((owner, repo, pr_number), {})
        reviews = pr_data.get("reviews", [])
        issue_comments = pr_data.get("comments", [])
        review_comments = pr_data.get("review_comments", [])
        details = pr_data.get("details")
        
        first_approval = get_first_approval_time(reviews)
        last_activity = get_last_activity_time(issue_comments, review_comments, reviews)
        base_branch = details.get("base", {}).get("ref", "—") if details else "—"
        created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
        
        is_draft = pr.get("draft", False)
        
        last_activity_dt = last_activity.replace(tzinfo=None) if last_activity else created_at.replace(tzinfo=None)
        hours_inactive = int((now - last_activity_dt).total_seconds() / 3600)
        
        attention_reasons = []
        if hours_inactive >= inactive_open_prs_hours:
            attention_reasons.append(f"⏰ {hours_inactive}h inactive")
        if first_approval and (now - first_approval.replace(tzinfo=None)).days >= 1:
            attention_reasons.append("✅ Approved, not merged")
        if is_draft and (now - created_at.replace(tzinfo=None)).days >= 7:
            attention_reasons.append("📝 Stale draft")
        
        pr_title = pr.get("title", "")
        pr_url = pr.get("html_url", "")
        rows.append({
            "Author": pr.get("user", {}).get("login", "Unknown"),
            "Repository": f"{owner}/{repo}",
            "PR #": pr_url,
            "Title": pr_title,
            "Base": base_branch,
            "Draft": "📝" if is_draft else "",
            "Submit Time": created_at.strftime("%Y-%m-%d %H:%M"),
            "Last Activity": last_activity_dt.strftime("%Y-%m-%d %H:%M") if last_activity else "—",
            "First Approval": first_approval.strftime("%Y-%m-%d %H:%M") if first_approval else "—",
            "Age (days)": (now - created_at.replace(tzinfo=None)).days,
            "Needs Attention": " | ".join(attention_reasons) if attention_reasons else ""
        })
        progress.progress(50 + int((i + 1) / len(prs) * 50), text=f"Processing {i+1}/{len(prs)} PRs...")
    
    progress.empty()
    df = pd.DataFrame(rows)
    st.session_state.pr_table_rows = rows
    
    df.insert(0, "Status", df["Needs Attention"].apply(lambda x: "⚠️" if x else "✓"))
    
    def highlight_attention(row):
        if row["Needs Attention"]:
            return ["background-color: rgba(251, 146, 60, 0.25)"] * len(row)
        return [""] * len(row)
    
    styled_df = df.style.apply(highlight_attention, axis=1)
    
    needs_attention_count = len(df[df["Needs Attention"] != ""])
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Open PRs", len(df))
    col2.metric("Needs Attention", needs_attention_count)
    col3.metric("Awaiting Approval", len(df[df["First Approval"] == "—"]))
    col4.metric("Avg Age (days)", f"{df['Age (days)'].mean():.1f}" if len(df) > 0 else "—")
    
    st.dataframe(
        styled_df,
        column_config={
            "Status": st.column_config.TextColumn("", width="small"),
            "PR #": st.column_config.LinkColumn("PR #", display_text="/(\\d+)$", width="small"),
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
        
        pr_title = pr.get("title", "")
        pr_url = pr.get("html_url", "")
        rows.append({
            "Author": pr.get("user", {}).get("login", "Unknown"),
            "Repository": f"{owner}/{repo}",
            "PR #": pr_url,
            "Title": pr_title,
            "Base": base_branch,
            "Merged": "✅" if (details and details.get("merged")) else "❌",
            "Created": created_at.strftime("%Y-%m-%d"),
            "Closed": closed_at.strftime("%Y-%m-%d") if closed_at else "—",
            "Lines Added": additions,
            "Lines Deleted": deletions,
            "Total Lines": additions + deletions
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
            "PR #": st.column_config.LinkColumn("PR #", display_text="/(\\d+)$", width="small"),
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
        "Title": "count"
    }).rename(columns={"Title": "PR Count"}).sort_values("Total Lines", ascending=False)
    st.bar_chart(author_stats["Total Lines"])

def display_individual_stats_combined(all_repos, username, days_back, exclude_drafts=False, exclude_cherrypicks=False):
    """Display combined Open PRs + Stats for a single selected user."""
    config = load_config()
    user_display_names = config.get("user_display_names", {})
    display_name = user_display_names.get(username, username)
    
    with st.spinner("Fetching data..."):
        open_prs = search_prs(all_repos, [username], state="open", days_back=days_back)
        merged_prs = search_merged_prs(all_repos, [username], days_back)
        
        if exclude_drafts:
            merged_prs = [pr for pr in merged_prs if not pr.get("draft", False)]
        
        user_merged = [pr for pr in merged_prs if pr.get("user", {}).get("login") == username]
    
    st.subheader(f"📊 Summary for {display_name}")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("🟢 Open PRs", len(open_prs))
    with col2:
        st.metric("🔀 PRs Merged", len(user_merged))
    
    st.divider()
    
    st.subheader("🟢 Open PRs")
    display_open_prs(open_prs, exclude_cherrypicks, exclude_drafts)
    
    st.divider()
    
    st.subheader("👀 Review Responsibilities")
    with st.spinner("Fetching review data..."):
        prs_as_reviewer = search_prs_where_user_is_reviewer(all_repos, [username])
        if exclude_drafts:
            prs_as_reviewer = {u: [pr for pr in prs if not pr.get("draft", False)] for u, prs in prs_as_reviewer.items()}
        user_reviewing = prs_as_reviewer.get(username, [])
    
    if user_reviewing:
        rows = []
        now = datetime.utcnow()
        review_sla_hours = inactive_awaiting_review_hours
        
        pr_list = []
        for pr in user_reviewing:
            owner = pr.get("_owner", "")
            repo = pr.get("_repo", "")
            if not owner or not repo:
                owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
            pr_number = pr.get("number")
            if owner and repo and pr_number:
                pr_list.append((owner, repo, pr_number))
        
        all_pr_data = get_multiple_prs_full_details(pr_list) if pr_list else {}
        
        for pr in user_reviewing:
            owner = pr.get("_owner", "")
            repo = pr.get("_repo", "")
            if not owner or not repo:
                owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
            pr_number = pr.get("number")
            
            pr_data = all_pr_data.get((owner, repo, pr_number), {})
            reviews = pr_data.get("reviews", [])
            issue_comments = pr_data.get("comments", [])
            review_comments = pr_data.get("review_comments", [])
            
            created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            last_activity = get_last_activity_time(issue_comments, review_comments, reviews)
            last_activity_dt = last_activity.replace(tzinfo=None) if last_activity else created_at.replace(tzinfo=None)
            hours_since_activity = int((now - last_activity_dt).total_seconds() / 3600)
            
            user_has_reviewed = any(
                r.get("user", {}).get("login", "").lower() == username.lower() 
                for r in reviews
            )
            
            if user_has_reviewed:
                review_status = "✅ Reviewed"
            elif hours_since_activity > review_sla_hours:
                review_status = "🔴 Needs Review"
            else:
                review_status = "🟡 Pending"
            
            pr_title = pr.get("title", "")
            pr_url = pr.get("html_url", "")
            rows.append({
                "Status": review_status,
                "Repository": f"{owner}/{repo}",
                "PR #": pr_url,
                "Title": pr_title,
                "Author": pr.get("user", {}).get("login", "Unknown"),
                "Last Activity": last_activity_dt.strftime("%Y-%m-%d %H:%M"),
                "Hours Idle": hours_since_activity
            })
        
        df = pd.DataFrame(rows)
        df = df.sort_values(by="Status", key=lambda x: x.map({"🔴 Needs Review": 0, "🟡 Pending": 1, "✅ Reviewed": 2}))
        
        def highlight_status(row):
            if "Needs Review" in row["Status"]:
                return ["background-color: rgba(239, 68, 68, 0.25)"] * len(row)
            elif "Pending" in row["Status"]:
                return ["background-color: rgba(234, 179, 8, 0.15)"] * len(row)
            elif "Reviewed" in row["Status"]:
                return ["background-color: rgba(34, 197, 94, 0.15)"] * len(row)
            return [""] * len(row)
        
        styled_df = df.style.apply(highlight_status, axis=1)
        st.dataframe(
            styled_df,
            column_config={
                "PR #": st.column_config.LinkColumn("PR #", display_text="/(\\d+)$", width="small"),
                "Hours Idle": st.column_config.NumberColumn(format="%d hrs")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info(f"{display_name} is not currently listed as a reviewer on any open PRs.")
    
    st.divider()
    st.subheader("📤 Send Slack Reminder")
    
    token = config.get("slack_bot_token", "")
    token_valid = token and token != "xoxb-YOUR-BOT-TOKEN-HERE"
    my_slack_id = config.get("my_slack_id", "")
    user_slack_mapping = config.get("user_slack_mapping", {})
    slack_id = user_slack_mapping.get(username, "")
    slack_configured = slack_id and slack_id != "NOT MAPPED" and slack_id != "U_SLACK_ID_HERE"
    
    github_slack_users = {k: v for k, v in user_slack_mapping.items() if v}
    additional_contacts = config.get("additional_slack_contacts", {})
    cc_options_map = {}
    for gh_user, sid in github_slack_users.items():
        disp = user_display_names.get(gh_user) or gh_user
        cc_options_map[disp] = {"type": "github", "github": gh_user, "slack_id": sid}
    for name, sid in additional_contacts.items():
        cc_options_map[f"📋 {name}"] = {"type": "additional", "slack_id": sid}
    
    awaiting_review = search_review_requested_prs(all_repos, [username])
    user_awaiting = awaiting_review.get(username, [])
    
    lines = [f"Hi {display_name}! 👋", ""]
    if open_prs:
        lines.append(f"📂 *You have {len(open_prs)} open PR(s):*")
        for pr in open_prs:
            pr_num = pr.get("number", "?")
            pr_url = pr.get("html_url", "")
            title = pr.get("title", "Untitled")
            lines.append(f"  • <{pr_url}|PR #{pr_num}>: \"{title}\"")
        lines.append("")
    if user_awaiting:
        lines.append(f"👀 *{len(user_awaiting)} PR(s) awaiting your review:*")
        for pr in user_awaiting:
            pr_num = pr.get("number", "?")
            pr_url = pr.get("html_url", "")
            title = pr.get("title", "Untitled")
            author = pr.get("user", {}).get("login", "Unknown")
            lines.append(f'  • <{pr_url}|PR #{pr_num}>: "{title}" by {author}')
    lines.extend(["", "---", "Please take a moment to review these PRs. If any are stalled, we would like to understand the blockers so I can help move them forward. Is the inactivity due to:", "a) Pending reviews (stakeholders or area-experts)?", "b) Technical hurdles or shifting priorities?", "Let me know where we can step in to clear the path or nudge the right/concerned folks."])
    default_message = "\n".join(lines)
    
    edited_msg = st.text_area("Message:", value=default_message, height=200, key=f"indiv_msg_{username}")
    
    other_cc_options = [opt for opt in cc_options_map.keys() if cc_options_map.get(opt, {}).get("github") != username]
    col_cc1, col_cc2 = st.columns([1, 3])
    with col_cc1:
        cc_myself = st.checkbox("CC myself", value=True, key=f"indiv_cc_{username}")
    with col_cc2:
        cc_additional = st.multiselect("CC additional", options=other_cc_options, default=[], key=f"indiv_cc_add_{username}")
    
    send_disabled = not token_valid or not slack_configured
    btn_label = "📤 Send Reminder" if slack_configured else "📤 Send (No Slack ID)"
    if st.button(btn_label, key=f"indiv_send_{username}", disabled=send_disabled, use_container_width=True):
        from slack_notifier import send_slack_dm
        success, result = send_slack_dm(token, slack_id, edited_msg)
        if success:
            st.success(f"✅ Sent to {display_name}")
            if cc_myself and my_slack_id:
                cc_msg = f"[CC - sent to {display_name}]\n\n{edited_msg}"
                send_slack_dm(token, my_slack_id, cc_msg)
            for cc_name in cc_additional:
                cc_info = cc_options_map.get(cc_name, {})
                cc_slack_id = cc_info.get("slack_id", "")
                if cc_slack_id:
                    cc_msg = f"[CC - sent to {display_name}]\n\n{edited_msg}"
                    send_slack_dm(token, cc_slack_id, cc_msg)
        else:
            st.error(f"Failed: {result}")

def display_individual_stats(all_repos, username, days_back, exclude_drafts=False):
    """Display stats for a single selected user."""
    config = load_config()
    user_display_names = config.get("user_display_names", {})
    display_name = user_display_names.get(username, username)
    
    st.subheader(f"📊 Summary for {display_name}")
    with st.spinner("Fetching metrics..."):
        merged_prs = search_merged_prs(all_repos, [username], days_back)
        awaiting_review = search_review_requested_prs(all_repos, [username])
        
        if exclude_drafts:
            merged_prs = [pr for pr in merged_prs if not pr.get("draft", False)]
            awaiting_review = {u: [pr for pr in prs if not pr.get("draft", False)] for u, prs in awaiting_review.items()}
        
        user_merged = [pr for pr in merged_prs if pr.get("user", {}).get("login") == username]
        user_awaiting = awaiting_review.get(username, [])
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("🔀 PRs Merged", len(user_merged))
    with col2:
        st.metric("⏳ Awaiting Their Review", len(user_awaiting))
    
    tab1, tab2 = st.tabs(["📋 Awaiting Review", "🔍 Reviewing PRs"])
    
    with tab1:
        if user_awaiting:
            st.subheader(f"PRs Awaiting Review from {display_name}")
            rows = []
            for pr in user_awaiting:
                owner = pr.get("_owner", "")
                repo = pr.get("_repo", "")
                if not owner or not repo:
                    owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
                created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                age_days = (datetime.utcnow() - created_at.replace(tzinfo=None)).days
                pr_title = pr.get("title", "")
                pr_url = pr.get("html_url", "")
                rows.append({
                    "Repository": f"{owner}/{repo}",
                    "PR #": pr_url,
                    "Title": pr_title,
                    "Author": pr.get("user", {}).get("login", "Unknown"),
                    "Age (days)": age_days
                })
            
            df = pd.DataFrame(rows)
            st.dataframe(
                df,
                column_config={
                    "PR #": st.column_config.LinkColumn("PR #", display_text="/(\\d+)$", width="small"),
                    "Age (days)": st.column_config.NumberColumn(format="%d days")
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info(f"No PRs currently awaiting review from {display_name}.")
    
    with tab2:
        with st.spinner("Fetching review data..."):
            prs_as_reviewer = search_prs_where_user_is_reviewer(all_repos, [username])
            if exclude_drafts:
                prs_as_reviewer = {u: [pr for pr in prs if not pr.get("draft", False)] for u, prs in prs_as_reviewer.items()}
            user_reviewing = prs_as_reviewer.get(username, [])
        
        if user_reviewing:
            st.subheader(f"PRs {display_name} is Reviewing")
            rows = []
            now = datetime.utcnow()
            review_sla_hours = inactive_awaiting_review_hours
            
            pr_list = []
            for pr in user_reviewing:
                owner = pr.get("_owner", "")
                repo = pr.get("_repo", "")
                if not owner or not repo:
                    owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
                pr_number = pr.get("number")
                if owner and repo and pr_number:
                    pr_list.append((owner, repo, pr_number))
            
            all_pr_data = get_multiple_prs_full_details(pr_list) if pr_list else {}
            
            for pr in user_reviewing:
                owner = pr.get("_owner", "")
                repo = pr.get("_repo", "")
                if not owner or not repo:
                    owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
                pr_number = pr.get("number")
                
                pr_data = all_pr_data.get((owner, repo, pr_number), {})
                reviews = pr_data.get("reviews", [])
                issue_comments = pr_data.get("comments", [])
                review_comments = pr_data.get("review_comments", [])
                
                created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                last_activity = get_last_activity_time(issue_comments, review_comments, reviews)
                last_activity_dt = last_activity.replace(tzinfo=None) if last_activity else created_at.replace(tzinfo=None)
                hours_since_activity = int((now - last_activity_dt).total_seconds() / 3600)
                
                user_has_reviewed = any(
                    r.get("user", {}).get("login", "").lower() == username.lower() 
                    for r in reviews
                )
                
                if user_has_reviewed:
                    sla_status = "✅ Reviewed"
                elif hours_since_activity > review_sla_hours:
                    sla_status = "🔴 SLA Violation"
                else:
                    sla_status = "🟢 On Track"
                
                pr_title = pr.get("title", "")
                pr_url = pr.get("html_url", "")
                rows.append({
                    "SLA Status": sla_status,
                    "Repository": f"{owner}/{repo}",
                    "PR #": pr_url,
                    "Title": pr_title,
                    "Author": pr.get("user", {}).get("login", "Unknown"),
                    "Last Activity": last_activity_dt.strftime("%Y-%m-%d %H:%M"),
                    "Hours Inactive": hours_since_activity
                })
            
            df = pd.DataFrame(rows)
            
            def highlight_sla(row):
                if "SLA Violation" in row["SLA Status"]:
                    return ["background-color: rgba(239, 68, 68, 0.25)"] * len(row)
                elif "Reviewed" in row["SLA Status"]:
                    return ["background-color: rgba(34, 197, 94, 0.15)"] * len(row)
                return [""] * len(row)
            
            styled_df = df.style.apply(highlight_sla, axis=1)
            st.dataframe(
                styled_df,
                column_config={
                    "PR #": st.column_config.LinkColumn("PR #", display_text="/(\\d+)$", width="small"),
                    "Hours Inactive": st.column_config.NumberColumn(format="%d hrs")
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info(f"{display_name} is not currently listed as a reviewer on any open PRs.")

def display_team_stats(all_repos, selected_users, days_back, exclude_drafts=False):
    config = load_config()
    user_display_names = config.get("user_display_names", {})
    
    st.subheader("Team Summary")
    with st.spinner("Fetching team metrics..."):
        merged_prs = search_merged_prs(all_repos, selected_users, days_back)
        reviewed_prs = search_reviewed_prs(all_repos, selected_users, days_back)
        awaiting_review = search_review_requested_prs(all_repos, selected_users)
        
        if exclude_drafts:
            merged_prs = [pr for pr in merged_prs if not pr.get("draft", False)]
            reviewed_prs = {u: [pr for pr in prs if not pr.get("draft", False)] for u, prs in reviewed_prs.items()}
            awaiting_review = {u: [pr for pr in prs if not pr.get("draft", False)] for u, prs in awaiting_review.items()}
        
        total_merged = len(merged_prs)
        total_reviewed = sum(len(prs) for prs in reviewed_prs.values())
        total_awaiting = sum(len(prs) for prs in awaiting_review.values())
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("🔀 Total PRs Merged", total_merged)
    with col2:
        st.metric("👀 Total PRs Reviewed", total_reviewed)
    with col3:
        st.metric("⏳ PRs Awaiting Review", total_awaiting)
    
    st.subheader("👥 Team PR Activity")

    reviewer_data = []
    for username in selected_users:
        display_name = user_display_names.get(username, username)
        prs_reviewed = reviewed_prs.get(username, [])
        prs_awaiting = awaiting_review.get(username, [])
        prs_merged_by_user = [pr for pr in merged_prs if pr.get("user", {}).get("login") == username]

        reviewer_data.append({
            "Name": display_name,
            "PRs Merged": len(prs_merged_by_user),
            "PRs Reviewed": len(prs_reviewed),
            "Awaiting Their Review": len(prs_awaiting)
        })

    df_reviewer = pd.DataFrame(reviewer_data)

    view_mode = st.radio("View", ["Bar Chart", "Table"], horizontal=True, key="team_pr_view")

    if view_mode == "Bar Chart":
        import altair as alt
        chart_df = df_reviewer.melt(id_vars=["Name"], var_name="Metric", value_name="Count")
        chart = alt.Chart(chart_df).mark_bar().encode(
            y=alt.Y("Name:N", sort=None, title=None, axis=alt.Axis(labelFontSize=14, labelFontWeight="bold")),
            x=alt.X("Count:Q", title="PRs"),
            color=alt.Color("Metric:N", legend=alt.Legend(orient="top")),
            yOffset="Metric:N"
        ).properties(height=max(80 * len(selected_users), 200))
        st.altair_chart(chart, use_container_width=True)
    else:
        html_reviewer = df_reviewer.to_html(index=False, escape=False)
        html_reviewer = html_reviewer.replace('<thead>', '''<thead style="background-color: #4b5563;">''')
        html_reviewer = html_reviewer.replace('<th>', '''<th style="font-weight: 700; font-size: 15px; color: white; padding: 12px 16px; text-align: left;">''')
        html_reviewer = html_reviewer.replace('<table ', '''<table style="width: 100%; border-collapse: collapse;" ''')
        html_reviewer = html_reviewer.replace('<td>', '''<td style="padding: 10px 16px; border-bottom: 1px solid #e5e7eb;">''')
        st.markdown(html_reviewer, unsafe_allow_html=True)

    st.subheader("👤 Per-Member Details & Reminders")

    token = config.get("slack_bot_token", "")
    token_valid = token and token != "xoxb-YOUR-BOT-TOKEN-HERE"
    my_slack_id = config.get("my_slack_id", "")
    user_slack_mapping = config.get("user_slack_mapping", {})

    github_slack_users = {k: v for k, v in user_slack_mapping.items() if v}
    additional_contacts = config.get("additional_slack_contacts", {})
    cc_options_map = {}
    for gh_user, sid in github_slack_users.items():
        disp = user_display_names.get(gh_user) or gh_user
        cc_options_map[disp] = {"type": "github", "github": gh_user, "slack_id": sid}
    for name, sid in additional_contacts.items():
        cc_options_map[f"📋 {name}"] = {"type": "additional", "slack_id": sid}
    all_cc_options = list(cc_options_map.keys())

    open_prs_by_user = {}
    open_prs_attention = {}
    now = datetime.utcnow()
    with st.spinner("Fetching open PRs..."):
        all_open_prs = search_prs(all_repos, selected_users, state="open", days_back=days_back)
        if exclude_drafts:
            all_open_prs = [pr for pr in all_open_prs if not pr.get("draft", False)]
        
        pr_keys = []
        for pr in all_open_prs:
            owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
            pr_number = pr.get("number")
            if owner and repo:
                pr_keys.append((owner, repo, pr_number))
        all_pr_details = get_multiple_prs_full_details(pr_keys) if pr_keys else {}
        
        for pr in all_open_prs:
            author = pr.get("user", {}).get("login", "")
            if author in selected_users:
                if author not in open_prs_by_user:
                    open_prs_by_user[author] = []
                    open_prs_attention[author] = []
                
                owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
                pr_number = pr.get("number")
                pr_data = all_pr_details.get((owner, repo, pr_number), {})
                reviews = pr_data.get("reviews", [])
                issue_comments = pr_data.get("comments", [])
                review_comments = pr_data.get("review_comments", [])
                
                created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                last_activity = get_last_activity_time(issue_comments, review_comments, reviews)
                last_activity_dt = last_activity.replace(tzinfo=None) if last_activity else created_at.replace(tzinfo=None)
                hours_inactive = int((now - last_activity_dt).total_seconds() / 3600)
                
                needs_attention = hours_inactive >= inactive_open_prs_hours
                pr["_needs_attention"] = needs_attention
                pr["_hours_inactive"] = hours_inactive
                
                open_prs_by_user[author].append(pr)
                if needs_attention:
                    open_prs_attention[author].append(pr)

    awaiting_review_attention = {}
    with st.spinner("Fetching awaiting review details..."):
        all_awaiting_prs = []
        for user, prs in awaiting_review.items():
            all_awaiting_prs.extend(prs)
        
        awaiting_pr_keys = []
        for pr in all_awaiting_prs:
            owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
            pr_number = pr.get("number")
            if owner and repo:
                awaiting_pr_keys.append((owner, repo, pr_number))
        awaiting_pr_details = get_multiple_prs_full_details(awaiting_pr_keys) if awaiting_pr_keys else {}
        
        for user, prs in awaiting_review.items():
            awaiting_review_attention[user] = []
            for pr in prs:
                owner, repo = parse_repo_from_url(pr.get("repository_url", ""))
                pr_number = pr.get("number")
                pr_data = awaiting_pr_details.get((owner, repo, pr_number), {})
                reviews = pr_data.get("reviews", [])
                issue_comments = pr_data.get("comments", [])
                review_comments = pr_data.get("review_comments", [])
                
                created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                last_activity = get_last_activity_time(issue_comments, review_comments, reviews)
                last_activity_dt = last_activity.replace(tzinfo=None) if last_activity else created_at.replace(tzinfo=None)
                hours_inactive = int((now - last_activity_dt).total_seconds() / 3600)
                
                needs_attention = hours_inactive >= inactive_open_prs_hours
                pr["_needs_attention"] = needs_attention
                pr["_hours_inactive"] = hours_inactive
                if needs_attention:
                    awaiting_review_attention[user].append(pr)

    for username in selected_users:
        display_name = user_display_names.get(username, username)
        slack_id = user_slack_mapping.get(username, "")
        slack_configured = slack_id and slack_id != "NOT MAPPED" and slack_id != "U_SLACK_ID_HERE"

        user_open_prs = open_prs_by_user.get(username, [])
        user_attention_prs = open_prs_attention.get(username, [])
        user_awaiting = awaiting_review.get(username, [])

        attention_count = len(user_attention_prs)

        with st.expander(f"**{display_name}** — {len(user_open_prs)} Open PRs, {len(user_awaiting)} Awaiting Review"):
            col_open, col_awaiting = st.columns(2)
            with col_open:
                st.markdown(f"**🟢 Open PRs:** ({len(user_open_prs)} total, {attention_count} need attention)")
                if user_open_prs:
                    for pr in user_open_prs:
                        title = pr.get("title", "")[:50] + ("..." if len(pr.get("title", "")) > 50 else "")
                        pr_url = pr.get("html_url", "")
                        needs_attn = pr.get("_needs_attention", False)
                        icon = "⚠️" if needs_attn else "✓"
                        st.markdown(f"{icon} [{pr.get('number')}]({pr_url}) - {title}")
                else:
                    st.caption("No open PRs")
            with col_awaiting:
                awaiting_attn_count = len(awaiting_review_attention.get(username, []))
                st.markdown(f"**⏳ Awaiting Their Review:** ({len(user_awaiting)} total, {awaiting_attn_count} need attention)")
                if user_awaiting:
                    for pr in user_awaiting:
                        title = pr.get("title", "")[:50] + ("..." if len(pr.get("title", "")) > 50 else "")
                        pr_url = pr.get("html_url", "")
                        author = pr.get("user", {}).get("login", "Unknown")
                        needs_attn = pr.get("_needs_attention", False)
                        icon = "⚠️" if needs_attn else "✓"
                        st.markdown(f"{icon} [{pr.get('number')}]({pr_url}) - {title} (by {author})")
                else:
                    st.caption("No PRs awaiting review")

            st.divider()
            st.markdown("**📤 Send Reminder**")

            lines = [f"Hi {display_name}! 👋", ""]
            if user_attention_prs:
                lines.append(f"🔴 *You have {len(user_attention_prs)} PR(s) needing attention:*")
                for pr in user_attention_prs:
                    pr_num = pr.get("number", "?")
                    pr_url = pr.get("html_url", "")
                    title = pr.get("title", "Untitled")
                    hours = pr.get("_hours_inactive", 0)
                    time_str = f"{hours // 24}d {hours % 24}h" if hours >= 24 else f"{hours}h"
                    lines.append(f"  • <{pr_url}|PR #{pr_num}>: \"{title}\" ({time_str} inactive)")
                lines.append("")
            if user_awaiting:
                lines.append(f"👀 *{len(user_awaiting)} PR(s) awaiting your review:*")
                for pr in user_awaiting:
                    pr_num = pr.get("number", "?")
                    pr_url = pr.get("html_url", "")
                    title = pr.get("title", "Untitled")
                    author = pr.get("user", {}).get("login", "Unknown")
                    lines.append(f'  • <{pr_url}|PR #{pr_num}>: "{title}" by {author}')
            if not user_attention_prs and not user_awaiting:
                lines.append("✅ No PRs currently need your attention. Great work!")
            lines.extend(["", "---", "Please take a moment to review these PRs. If any are stalled, we would like to understand the blockers so I can help move them forward. Is the inactivity due to:", "a) Pending reviews (stakeholders or area-experts)?", "b) Technical hurdles or shifting priorities?", "Let me know where we can step in to clear the path or nudge the right/concerned folks."])
            default_message = "\n".join(lines)

            edited_msg = st.text_area("Message:", value=default_message, height=200, key=f"team_msg_{username}")

            other_cc_options = [opt for opt in all_cc_options if cc_options_map.get(opt, {}).get("github") != username]
            col_cc1, col_cc2 = st.columns([1, 3])
            with col_cc1:
                cc_myself = st.checkbox("CC myself", value=True, key=f"team_cc_{username}")
            with col_cc2:
                cc_additional = st.multiselect("CC additional", options=other_cc_options, default=[], key=f"team_cc_add_{username}")

            send_disabled = not token_valid or not slack_configured
            btn_label = "📤 Send" if slack_configured else "📤 Send (No Slack ID)"
            if st.button(btn_label, key=f"team_send_{username}", disabled=send_disabled, use_container_width=True):
                from slack_notifier import send_slack_dm
                success, result = send_slack_dm(token, slack_id, edited_msg)
                if success:
                    st.success(f"✅ Sent to {display_name}")
                    if cc_myself and my_slack_id:
                        cc_msg = f"[CC - sent to {display_name}]\n\n{edited_msg}"
                        send_slack_dm(token, my_slack_id, cc_msg)
                    for cc_name in cc_additional:
                        cc_info = cc_options_map.get(cc_name, {})
                        cc_slack_id = cc_info.get("slack_id", "")
                        if cc_slack_id:
                            cc_msg = f"[CC - sent to {display_name}]\n\n{edited_msg}"
                            send_slack_dm(token, cc_slack_id, cc_msg)
                else:
                    st.error(f"Failed: {result}")

select_all_users = len(selected_users) == len(all_usernames)

if pr_state == "Open":
    if select_all_users:
        tab_prs, tab_stats = st.tabs(["🟢 Open Pull Requests", "📊 Team Stats"])
        with tab_prs:
            with st.spinner("Fetching open PRs..."):
                open_prs = search_prs(all_repos, selected_users, state="open", days_back=days_back)
            display_open_prs(open_prs, exclude_cherrypicks, exclude_drafts)
        with tab_stats:
            display_team_stats(all_repos, selected_users, days_back, exclude_drafts)
    elif len(selected_users) == 1:
        display_individual_stats_combined(all_repos, selected_users[0], days_back, exclude_drafts, exclude_cherrypicks)
    else:
        tab_prs, tab_stats = st.tabs(["🟢 Open Pull Requests", "📊 Selected Members Stats"])
        with tab_prs:
            with st.spinner("Fetching open PRs..."):
                open_prs = search_prs(all_repos, selected_users, state="open", days_back=days_back)
            display_open_prs(open_prs, exclude_cherrypicks, exclude_drafts)
        with tab_stats:
            display_team_stats(all_repos, selected_users, days_back, exclude_drafts)

elif pr_state == "Closed":
    st.subheader("🔴 Closed Pull Requests")
    with st.spinner("Fetching closed PRs..."):
        closed_prs = search_prs(all_repos, selected_users, state="closed", days_back=days_back)
    display_closed_prs(closed_prs)

else:
    if select_all_users:
        tab_open, tab_closed, tab_stats = st.tabs(["🟢 Open PRs", "🔴 Closed PRs", "📊 Team Stats"])
        
        with tab_open:
            with st.spinner("Fetching open PRs..."):
                open_prs = search_prs(all_repos, selected_users, state="open", days_back=days_back)
            display_open_prs(open_prs, exclude_cherrypicks, exclude_drafts)
        
        with tab_stats:
            display_team_stats(all_repos, selected_users, days_back, exclude_drafts)
        
        with tab_closed:
            with st.spinner("Fetching closed PRs..."):
                closed_prs = search_prs(all_repos, selected_users, state="closed", days_back=days_back)
            display_closed_prs(closed_prs)
    
    elif len(selected_users) == 1:
        tab_combined, tab_closed = st.tabs(["📊 PR Dashboard", "🔴 Closed PRs"])
        
        with tab_combined:
            display_individual_stats_combined(all_repos, selected_users[0], days_back, exclude_drafts, exclude_cherrypicks)
        
        with tab_closed:
            with st.spinner("Fetching closed PRs..."):
                closed_prs = search_prs(all_repos, selected_users, state="closed", days_back=days_back)
            display_closed_prs(closed_prs)
    
    else:
        tab_open, tab_closed, tab_stats = st.tabs(["🟢 Open PRs", "🔴 Closed PRs", "📊 Selected Members Stats"])
        
        with tab_open:
            with st.spinner("Fetching open PRs..."):
                open_prs = search_prs(all_repos, selected_users, state="open", days_back=days_back)
            display_open_prs(open_prs, exclude_cherrypicks, exclude_drafts)
        
        with tab_stats:
            display_team_stats(all_repos, selected_users, days_back, exclude_drafts)
        
        with tab_closed:
            with st.spinner("Fetching closed PRs..."):
                closed_prs = search_prs(all_repos, selected_users, state="closed", days_back=days_back)
            display_closed_prs(closed_prs)

st.divider()
st.header("📅 Schedule Manager")

slack_config = load_config()

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
FREQUENCIES = ["Daily", "Weekly", "Monthly", "Custom Interval"]
TIMEZONES = ["America/Los_Angeles", "America/New_York", "America/Chicago", "America/Denver", "UTC", "Europe/London", "Asia/Kolkata"]

schedules_config = slack_config.get("schedules", {
    "team_default": {"enabled": True, "frequency": "weekly", "days_of_week": ["Monday"], "time": "09:00", "timezone": "America/Los_Angeles"},
    "user_overrides": {}
})
team_schedule = schedules_config.get("team_default", {})
user_overrides = schedules_config.get("user_overrides", {})

with st.expander("🏢 Team Default Schedule", expanded=True):
    st.caption("This schedule applies to all team members unless they have a personal override.")
    
    team_enabled = st.checkbox("Enable team schedule", value=team_schedule.get("enabled", True), key="team_sched_enabled")
    
    col1, col2 = st.columns(2)
    with col1:
        team_freq = st.selectbox("Frequency", FREQUENCIES, index=FREQUENCIES.index(team_schedule.get("frequency", "Weekly").capitalize()) if team_schedule.get("frequency", "weekly").capitalize() in FREQUENCIES else 1, key="team_freq")
    with col2:
        team_time = st.time_input("Time", value=datetime.strptime(team_schedule.get("time", "09:00"), "%H:%M").time(), key="team_time")
    
    col3, col4 = st.columns(2)
    with col3:
        team_tz = st.selectbox("Timezone", TIMEZONES, index=TIMEZONES.index(team_schedule.get("timezone", "America/Los_Angeles")) if team_schedule.get("timezone", "America/Los_Angeles") in TIMEZONES else 0, key="team_tz")
    
    if team_freq == "Weekly":
        team_days = st.multiselect("Days of Week", DAYS_OF_WEEK, default=team_schedule.get("days_of_week", ["Monday"]), key="team_days")
    elif team_freq == "Monthly":
        with col4:
            team_day_of_month = st.number_input("Day of Month", min_value=1, max_value=28, value=team_schedule.get("day_of_month", 1), key="team_dom")
    elif team_freq == "Custom Interval":
        with col4:
            team_interval = st.number_input("Every N days", min_value=1, max_value=90, value=team_schedule.get("interval_days", 7), key="team_interval")

with st.expander("👤 User Schedule Overrides", expanded=False):
    st.caption("Set custom schedules for specific team members. These override the team default.")
    
    user_to_override = st.selectbox("Select user to configure", ["-- Select --"] + all_usernames, key="user_override_select")
    
    if user_to_override != "-- Select --":
        existing = user_overrides.get(user_to_override, {})
        display_name = slack_config.get("user_display_names", {}).get(user_to_override, user_to_override)
        
        st.subheader(f"Schedule for {display_name}")
        
        user_has_override = st.checkbox("Enable custom schedule for this user", value=bool(existing), key=f"override_enabled_{user_to_override}")
        
        if user_has_override:
            ucol1, ucol2 = st.columns(2)
            with ucol1:
                user_freq = st.selectbox("Frequency", FREQUENCIES, index=FREQUENCIES.index(existing.get("frequency", "Weekly").capitalize()) if existing.get("frequency", "weekly").capitalize() in FREQUENCIES else 1, key=f"user_freq_{user_to_override}")
            with ucol2:
                user_time = st.time_input("Time", value=datetime.strptime(existing.get("time", "09:00"), "%H:%M").time(), key=f"user_time_{user_to_override}")
            
            ucol3, ucol4 = st.columns(2)
            with ucol3:
                user_tz = st.selectbox("Timezone", TIMEZONES, index=TIMEZONES.index(existing.get("timezone", "America/Los_Angeles")) if existing.get("timezone") in TIMEZONES else 0, key=f"user_tz_{user_to_override}")
            
            if user_freq == "Weekly":
                user_days = st.multiselect("Days of Week", DAYS_OF_WEEK, default=existing.get("days_of_week", ["Monday"]), key=f"user_days_{user_to_override}")
            elif user_freq == "Monthly":
                with ucol4:
                    user_dom = st.number_input("Day of Month", min_value=1, max_value=28, value=existing.get("day_of_month", 1), key=f"user_dom_{user_to_override}")
            elif user_freq == "Custom Interval":
                with ucol4:
                    user_interval = st.number_input("Every N days", min_value=1, max_value=90, value=existing.get("interval_days", 7), key=f"user_interval_{user_to_override}")
    
    if user_overrides:
        st.divider()
        st.write("**Current user overrides:**")
        for usr, sched in user_overrides.items():
            disp = slack_config.get("user_display_names", {}).get(usr, usr)
            freq = sched.get("frequency", "weekly").capitalize()
            time_str = sched.get("time", "09:00")
            if freq == "Weekly":
                days_str = ", ".join(sched.get("days_of_week", []))
                st.write(f"• **{disp}**: {freq} on {days_str} at {time_str}")
            elif freq == "Monthly":
                st.write(f"• **{disp}**: {freq} on day {sched.get('day_of_month', 1)} at {time_str}")
            elif freq == "Custom interval":
                st.write(f"• **{disp}**: Every {sched.get('interval_days', 7)} days at {time_str}")
            else:
                st.write(f"• **{disp}**: {freq} at {time_str}")

if st.button("💾 Save Schedule Configuration", key="save_schedules"):
    new_team = {
        "enabled": team_enabled,
        "frequency": team_freq.lower(),
        "time": team_time.strftime("%H:%M"),
        "timezone": team_tz
    }
    if team_freq == "Weekly":
        new_team["days_of_week"] = team_days
    elif team_freq == "Monthly":
        new_team["day_of_month"] = team_day_of_month
    elif team_freq == "Custom Interval":
        new_team["interval_days"] = team_interval
    
    new_user_overrides = dict(user_overrides)
    if user_to_override != "-- Select --":
        if st.session_state.get(f"override_enabled_{user_to_override}", False):
            user_sched = {
                "frequency": st.session_state.get(f"user_freq_{user_to_override}", "Weekly").lower(),
                "time": st.session_state.get(f"user_time_{user_to_override}", datetime.strptime("09:00", "%H:%M").time()).strftime("%H:%M"),
                "timezone": st.session_state.get(f"user_tz_{user_to_override}", "America/Los_Angeles")
            }
            if user_sched["frequency"] == "weekly":
                user_sched["days_of_week"] = st.session_state.get(f"user_days_{user_to_override}", ["Monday"])
            elif user_sched["frequency"] == "monthly":
                user_sched["day_of_month"] = st.session_state.get(f"user_dom_{user_to_override}", 1)
            elif user_sched["frequency"] == "custom interval":
                user_sched["interval_days"] = st.session_state.get(f"user_interval_{user_to_override}", 7)
            new_user_overrides[user_to_override] = user_sched
        elif user_to_override in new_user_overrides:
            del new_user_overrides[user_to_override]
    
    current_config = load_config()
    current_config["schedules"] = {
        "team_default": new_team,
        "user_overrides": new_user_overrides
    }
    save_config(current_config)
    st.success("Schedule configuration saved!")
    st.rerun()

st.divider()
st.caption("""
**Automated Reminders:** The schedule above controls when reminders are sent. Run `crontab -e` and add:
```
* * * * * cd /Users/ahusain/pr-dashboard && ./venv/bin/python slack_notifier.py --check-schedule
```
Runs every minute but is a **NOP unless a schedule matches** - GitHub API is only called when reminders actually need to be sent.
""")
