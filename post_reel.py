"""Post the next due Reel from queue.csv to Instagram via the Graph API.

Env vars (set as GitHub Actions secrets / env):
  IG_USER_ID       Instagram Business/Creator account ID
  IG_ACCESS_TOKEN  Long-lived access token with instagram_content_publish
  GITHUB_REPOSITORY, GITHUB_REF_NAME  (provided automatically by Actions)
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

# Instagram-Login tokens use graph.instagram.com; Facebook-Login tokens use graph.facebook.com
GRAPH = os.environ.get("IG_GRAPH_BASE") or "https://graph.facebook.com/v21.0"
QUEUE = "queue.csv"
FIELDS = ["filename", "caption", "post_at", "status", "media_id"]


def load_queue():
    with open(QUEUE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in FIELDS:
            r.setdefault(k, "")
    return rows


def save_queue(rows):
    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def parse_time(s):
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def api(method, path, **params):
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    r = requests.request(method, f"{GRAPH}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Graph API error: {data}")
    return data


def post_reel(video_url, caption):
    uid = os.environ["IG_USER_ID"]
    container = api("POST", f"{uid}/media", media_type="REELS",
                    video_url=video_url, caption=caption)["id"]
    print(f"Container created: {container}")

    for _ in range(60):  # up to ~10 minutes
        status = api("GET", container, fields="status_code,status")
        code = status.get("status_code")
        print(f"Processing status: {code}")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Video processing failed: {status}")
        time.sleep(10)
    else:
        raise RuntimeError("Timed out waiting for video processing")

    return api("POST", f"{uid}/media_publish", creation_id=container)["id"]


def main():
    rows = load_queue()
    now = datetime.now(timezone.utc)
    due = next((r for r in rows if not r["status"].strip()
                and parse_time(r["post_at"]) <= now), None)
    if not due:
        print("Nothing due.")
        return 0

    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/videos/{quote(due['filename'])}"
    print(f"Posting {due['filename']} from {url}")

    # Mark first so a crash after publishing can never cause a double post.
    due["status"] = "posting"
    save_queue(rows)
    try:
        due["media_id"] = post_reel(url, due["caption"])
        due["status"] = "posted"
        # Posted OK: remove the video from the repo to keep storage small.
        try:
            os.remove(os.path.join("videos", os.path.basename(due["filename"])))
            print("Deleted video from repo.")
        except OSError as e:
            print(f"Could not delete video: {e}", file=sys.stderr)
    except Exception as e:
        due["status"] = f"failed: {e}"[:200].replace("\n", " ")
        save_queue(rows)
        print(due["status"], file=sys.stderr)
        return 1
    save_queue(rows)
    print(f"Published! media id {due['media_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
