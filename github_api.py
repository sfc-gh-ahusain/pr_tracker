import os
from datetime import datetime, timedelta
from typing import Optional
import requests
import streamlit as st

GITHUB_API_BASE = "https://api.github.com"

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
def search_prs(orgs: list[str], usernames: list[str], state: str = "open", days_back: int = 90) -> list[dict]:
    prs = []
    since_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    orgs_lower = [o.lower() for o in orgs]
    
    for username in usernames:
        query = f"is:pr author:{username} state:{state} created:>={since_date}"
        url = f"{GITHUB_API_BASE}/search/issues"
        params = {"q": query, "per_page": 100, "sort": "created", "order": "desc"}
        
        try:
            resp = requests.get(url, headers=get_headers(), params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("items", []):
                repo_url = item.get("repository_url", "")
                org = repo_url.split("/")[-2].lower() if "/repos/" in repo_url else ""
                if org in orgs_lower:
                    prs.append(item)
        except Exception as e:
            st.warning(f"Error fetching PRs for {username}: {e}")
    
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
        comments.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return datetime.fromisoformat(comments[0]["created_at"].replace("Z", "+00:00"))
    return None
