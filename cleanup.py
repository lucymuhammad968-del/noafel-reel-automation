"""Delete a video from videos/ once every platform it was queued for has posted it."""
import csv
import os

with open("queue.csv", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

for r in rows:
    ig_done = (r.get("status") or "").strip() == "posted"
    wants_tiktok = (r.get("tiktok") or "").strip().lower() == "yes"
    tt_done = (r.get("tiktok_status") or "").strip() == "posted"
    if ig_done and (tt_done or not wants_tiktok):
        path = os.path.join("videos", os.path.basename(r["filename"]))
        if os.path.exists(path):
            os.remove(path)
            print(f"Deleted {path}")
