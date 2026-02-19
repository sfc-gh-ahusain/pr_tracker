import os
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import streamlit as st

GITHUB_API_BASE = "https://api.github.com"
MAX_WORKERS = 10

def get_headers():
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        try:
            token = st.secrets.get("GITHUB_TOKEN", "")
        except Exception:
            token = ""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers

@st.cache_data(ttl=3600)
def search_prs(repos: list[str], usernames: list[str], state: str = "open", days_back: int = 90) -> list[dict]:
    prs = []
    since_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    repos_lower = [r.lower() for r in repos]
    
    def fetch_user_prs(username):
        user_prs = []
        query = f"is:pr author:{username} state:{state} created:>={since_date}"
        url = f"{GITHUB_API_BASE}/search/issues"
        params = {"q": query, "per_page": 100, "sort": "created", "order": "desc"}
        try:
            resp = requests.get(url, headers=get_headers(), params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            total_found = data.get("total_count", 0)
            for item in data.get("items", []):
                repo_url = item.get("repository_url", "")
                repo_path = "/".join(repo_url.split("/")[-2:]).lower() if "/repos/" in repo_url else ""
                if repo_path in repos_lower:
                    user_prs.append(item)
            return (username, user_prs, None, total_found, len(data.get("items", [])))
        except Exception as e:
            return (username, [], str(e), 0, 0)
        return (username, user_prs, None, 0, 0)
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_user_prs, u): u for u in usernames}
        for future in as_completed(futures):
            result = future.result()
            username, user_prs, error = result[0], result[1], result[2]
            if len(result) > 3:
                total_found, items_returned = result[3], result[4]
                if total_found > 0 and len(user_prs) == 0:
                    st.info(f"Debug: {username} has {total_found} PRs total, {items_returned} returned, {len(user_prs)} matched repos {repos_lower}")
            if error:
                st.warning(f"Error fetching PRs for {username}: {error}")
            prs.extend(user_prs)
    
    return prs

@st.cache_data(ttl=300)
def get_pr_details(owner: str, repo: str, pr_number: int) -> Optional[dict]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None

@st.cache_data(ttl=300)
def get_pr_reviews(owner: str, repo: str, pr_number: int) -> list[dict]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []

@st.cache_data(ttl=300)
def get_pr_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []

@st.cache_data(ttl=300)
def get_pr_review_comments(owner: str, repo: str, pr_number: int) -> list[dict]:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []

def parse_repo_from_url(url: str) -> tuple[str, str]:
    parts = url.replace("https://github.com/", "").replace("https://api.github.com/repos/", "").split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""

def get_first_approval_time(reviews: list[dict]) -> Optional[datetime]:
    approvals = [r for r in reviews if r.get("state") == "APPROVED"]
    if approvals:
        approvals.sort(key=lambda x: x.get("submitted_at", ""))
        return datetime.fromisoformat(approvals[0]["submitted_at"].replace("Z", "+00:00"))
    return None

def get_last_comment_time(comments: list[dict]) -> Optional[datetime]:
    if comments:
        comments.sort(key=lambda x: x.get("updated_at", x.get("created_at", "")), reverse=True)
        return datetime.fromisoformat(comments[0].get("updated_at", comments[0]["created_at"]).replace("Z", "+00:00"))
    return None

def get_last_activity_time(issue_comments: list[dict], review_comments: list[dict], reviews: list[dict]) -> Optional[datetime]:
    all_times = []
    for c in issue_comments:
        ts = c.get("updated_at") or c.get("created_at")
        if ts:
            all_times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
    for c in review_comments:
        ts = c.get("updated_at") or c.get("created_at")
        if ts:
            all_times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
    for r in reviews:
        ts = r.get("submitted_at")
        if ts:
            all_times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
    return max(all_times) if all_times else None

@st.cache_data(ttl=300)
def get_pr_full_details(owner: str, repo: str, pr_number: int) -> dict:
    """Fetch PR details, reviews, and comments in parallel."""
    results = {"details": None, "reviews": [], "comments": [], "review_comments": []}
    
    def fetch_details():
        return get_pr_details(owner, repo, pr_number)
    
    def fetch_reviews():
        return get_pr_reviews(owner, repo, pr_number)
    
    def fetch_comments():
        return get_pr_comments(owner, repo, pr_number)
    
    def fetch_review_comments():
        return get_pr_review_comments(owner, repo, pr_number)
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_details = executor.submit(fetch_details)
        future_reviews = executor.submit(fetch_reviews)
        future_comments = executor.submit(fetch_comments)
        future_review_comments = executor.submit(fetch_review_comments)
        
        results["details"] = future_details.result()
        results["reviews"] = future_reviews.result()
        results["comments"] = future_comments.result()
        results["review_comments"] = future_review_comments.result()
    
    return results

def get_multiple_prs_full_details(pr_list: list[tuple[str, str, int]]) -> dict:
    """Fetch full details for multiple PRs in parallel.
    
    Args:
        pr_list: List of (owner, repo, pr_number) tuples
    
    Returns:
        Dict mapping (owner, repo, pr_number) to full details
    """
    results = {}
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_pr = {
            executor.submit(get_pr_full_details, owner, repo, pr_num): (owner, repo, pr_num)
            for owner, repo, pr_num in pr_list
        }
        for future in as_completed(future_to_pr):
            pr_key = future_to_pr[future]
            try:
                results[pr_key] = future.result()
            except Exception:
                results[pr_key] = {"details": None, "reviews": [], "comments": [], "review_comments": []}
    
    return results
