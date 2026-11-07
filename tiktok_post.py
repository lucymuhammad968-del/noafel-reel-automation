"""Post the next due video from queue.csv to TikTok (Content Posting API, Direct Post).

Runs only for rows where the `tiktok` column is "yes" and `tiktok_status` is empty.

Env vars (GitHub Actions secrets):
  TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN
  TIKTOK_PRIVACY (optional) - default SELF_ONLY, which is the only level TikTok
                              allows until the app passes TikTok's audit.
If the secrets are missing the script does nothing and exits 0.
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests

API = "https://open.tiktokapis.com"
QUEUE = "queue.csv"
FIELDS = ["filename", "caption", "post_at", "status", "media_id",
          "tiktok", "tiktok_status", "tiktok_id"]
MAX_SINGLE_CHUNK = 64 * 1024 * 1024  # TikTok allows one chunk up to 64 MB


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


def check(resp):
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    err = data.get("error")
    if resp.status_code >= 400 or (isinstance(err, dict) and err.get("code") not in (None, "ok")) \
            or (isinstance(err, str) and err):
        raise RuntimeError(f"TikTok error: {data}")
    return data


def get_access_token():
    r = requests.post(
        f"{API}/v2/oauth/token/",
        data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"Token refresh failed: {data.get('error_description') or data.get('error') or data}")
    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != os.environ["TIKTOK_REFRESH_TOKEN"]:
        print("NOTE: TikTok issued a new refresh token. The old one may stop working "
              "later; re-run the connect flow to update the TIKTOK_REFRESH_TOKEN secret.")
    return data["access_token"]


def post_video(token, path, caption):
    size = os.path.getsize(path)
    if size > MAX_SINGLE_CHUNK:
        raise RuntimeError("Video is larger than 64 MB; please shrink it.")
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json; charset=UTF-8"}
    body = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": os.environ.get("TIKTOK_PRIVACY") or "SELF_ONLY",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        },
    }
    init = check(requests.post(f"{API}/v2/post/publish/video/init/",
                               headers=headers, json=body, timeout=60))["data"]
    publish_id, upload_url = init["publish_id"], init["upload_url"]
    print(f"Init OK, publish_id={publish_id}")

    with open(path, "rb") as f:
        up = requests.put(
            upload_url, data=f,
            headers={"Content-Type": "video/mp4", "Content-Length": str(size),
                     "Content-Range": f"bytes 0-{size - 1}/{size}"},
            timeout=600,
        )
    if up.status_code not in (200, 201, 206):
        raise RuntimeError(f"Upload failed: HTTP {up.status_code} {up.text[:200]}")
    print("Upload OK")

    for _ in range(40):  # up to ~7 minutes
        st = check(requests.post(f"{API}/v2/post/publish/status/fetch/",
                                 headers=headers, json={"publish_id": publish_id},
                                 timeout=60))["data"]
        status = st.get("status")
        print(f"Status: {status}")
        if status == "PUBLISH_COMPLETE":
            return publish_id
        if status == "FAILED":
            raise RuntimeError(f"TikTok processing failed: {st.get('fail_reason')}")
        time.sleep(10)
    raise RuntimeError("Timed out waiting for TikTok to finish")


def main():
    if not all(os.environ.get(k) for k in
               ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN")):
        print("TikTok secrets not set; skipping.")
        return 0

    rows = load_queue()
    now = datetime.now(timezone.utc)
    due = next((r for r in rows
                if r["tiktok"].strip().lower() == "yes" and not r["tiktok_status"].strip()
                and parse_time(r["post_at"]) <= now), None)
    if not due:
        print("Nothing due for TikTok.")
        return 0

    path = os.path.join("videos", os.path.basename(due["filename"]))
    print(f"TikTok: posting {due['filename']}")
    due["tiktok_status"] = "posting"  # mark first: never double-post
    save_queue(rows)
    try:
        if not os.path.exists(path):
            raise RuntimeError(f"Video file not found: {path}")
        token = get_access_token()
        due["tiktok_id"] = post_video(token, path, due["caption"])
        due["tiktok_status"] = "posted"
    except Exception as e:
        due["tiktok_status"] = f"failed: {e}"[:200].replace("\n", " ")
        save_queue(rows)
        print(due["tiktok_status"], file=sys.stderr)
        return 1
    save_queue(rows)
    print(f"TikTok published. id {due['tiktok_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
