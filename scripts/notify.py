"""Sends the daily phone notification through ntfy (free, no account).
Anyone who subscribes to the channel in the ntfy app gets it.

Usage:
  python scripts/notify.py          -> today's update (sent at most once per day)
  python scripts/notify.py failed   -> "refresh failed" alert
"""
import datetime as dt
import json
import os
import sys
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "docs", "data")
STATE = os.path.join(DATA, "notify_state.json")
TOPIC = os.environ.get("NTFY_TOPIC", "gcc-jobs-bc8urt")
PAGE = "https://sreesree8523-blip.github.io/gcc-jobs/"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NAMES = [("product", "Product"), ("dev", "Development"), ("lnd", "L&D"),
         ("eng_lead", "Eng leadership"), ("ops_lead", "Ops leadership")]


def send(title, message, click=PAGE, tags="briefcase", priority=3):
    body = json.dumps({"topic": TOPIC, "title": title, "message": message,
                       "click": click, "tags": [tags], "priority": priority}).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("ntfy:", r.status)


def line(counts):
    return " · ".join(f"{n} {counts.get(k, 0)}" for k, n in NAMES)


def main():
    today = dt.datetime.now(IST).date().isoformat()
    if len(sys.argv) > 1 and sys.argv[1] == "failed":
        send("GCC jobs: today's refresh failed",
             "The page is still showing the previous list. It will try again at the next scheduled run.",
             tags="warning", priority=4)
        return

    state = {}
    if os.path.exists(STATE):
        state = json.load(open(STATE))
    if state.get("last_sent") == today:
        print("already notified today")
        return

    s = json.load(open(os.path.join(DATA, "summary.json")))
    new = s.get("new_total", 0)
    date_label = dt.date.fromisoformat(today).strftime("%a %d %b")
    if new:
        title = f"GCC jobs {date_label}: {new} new"
        msg = (f"Hyderabad: {line(s.get('new_hyd_by_group', {}))}\n"
               f"All India: {line(s.get('new_by_group', {}))}")
    else:
        title = f"GCC jobs {date_label}: no new jobs today"
        msg = f"{s.get('total', 0):,} open jobs on the page."
    if s.get("companies_failed"):
        msg += "\nCouldn't reach today: " + ", ".join(s["companies_failed"][:6])
    send(title, msg, click=PAGE + "?when=new")

    json.dump({"last_sent": today}, open(STATE, "w"))


if __name__ == "__main__":
    main()
