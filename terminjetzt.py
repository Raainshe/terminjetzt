#!/usr/bin/env python3
"""
terminjetzt - watch the Heilbronn Ausländerbehörde booking page (eTermin) and
notify you as soon as an appointment opens up in the next 4 months.

Default service: "Verlängerung Aufenthaltserlaubnis" (residence permit extension).

Tiers (by how soon the appointment is):
    RED     within 7 days
    YELLOW  within 30 days
    BLUE    within 4 months

Only Python 3.8+ and the standard library are needed (macOS and Windows).

Usage:
    python3 terminjetzt.py                  # start watching (Ctrl+C to stop)
    python3 terminjetzt.py --test           # send a test notification + sound
    python3 terminjetzt.py --once           # check one time and exit
    python3 terminjetzt.py --list-services  # show all bookable services
    python3 terminjetzt.py --interval 45 --open --ntfy my-secret-topic
"""

import argparse
import calendar
import ctypes
import datetime as dt
import json
import os
import platform
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

WEBID = "qtermin-stadtheilbronn-abh"
BASE = "https://www.etermin.net"
BOOKING_URL = BASE + "/" + WEBID + "?lang=en"
DEFAULT_SERVICE_ID = 144512  # Verlängerung Aufenthaltserlaubnis
DEFAULT_MONTHS = 4
MIN_INTERVAL = 20  # seconds; be polite to the server

SYSTEM = platform.system()  # "Darwin", "Windows", "Linux"

try:
    from zoneinfo import ZoneInfo

    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # zoneinfo missing / no tzdata on Windows: fall back to local time
    BERLIN = None


# --------------------------------------------------------------------------- #
# Terminal colours
# --------------------------------------------------------------------------- #
if SYSTEM == "Windows":
    os.system("")  # enables ANSI escape processing in recent Windows consoles

USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ

TIERS = {
    # name: (emoji for notifications, ANSI colour code, label)
    "red": ("\U0001F534", "1;91", "within 7 days"),
    "yellow": ("\U0001F7E1", "1;93", "within 30 days"),
    "blue": ("\U0001F535", "1;94", "within 4 months"),
}


def paint(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if USE_COLOR else text


def log(msg=""):
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print("[%s] %s" % (stamp, msg), flush=True)


# --------------------------------------------------------------------------- #
# Dates / tiers
# --------------------------------------------------------------------------- #
def berlin_now():
    if BERLIN:
        return dt.datetime.now(BERLIN).replace(tzinfo=None)
    return dt.datetime.now()


def add_months(d, months):
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def tier_for(days_away):
    if days_away <= 7:
        return "red"
    if days_away <= 30:
        return "yellow"
    return "blue"


# --------------------------------------------------------------------------- #
# eTermin API
# --------------------------------------------------------------------------- #
def api_get(path, params=None, timeout=20):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "webid": WEBID,
            "Accept": "application/json, text/plain",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "User-Agent": "Mozilla/5.0 (terminjetzt appointment watcher)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_services():
    return api_get("/api/servicegroupservice")


def load_settings():
    data = api_get("/api/settingbs", {"t": ""})
    return data[0] if isinstance(data, list) else data


def _timeslots_params(service, settings, date_str, rangesearch):
    tz = settings.get("timezone") or "W. Europe Standard Time"
    params = {"date": date_str, "serviceid": service["sid"]}
    if rangesearch:
        params["rangesearch"] = 1
    params.update({
        "capacity": 1,
        "caching": "false",
        "duration": service.get("duration", 20),
        "cluster": "false",
        "slottype": 0,
        "fillcalendarstrategy": settings.get("fillcalendarstrategy", 0),
        "showavcap": "false",
        "appfuture": settings.get("appfuture", 270),
        "appdeadline": settings.get("appdeadline", 480),
        "appdeadlinewm": settings.get("appdeadlinewm", 1),
        "oneoff": "null",
        "msdcm": settings.get("msdcm", 0),
    })
    if not rangesearch:
        params["tz"] = tz
        params["tzaccount"] = tz
    params["calendarid"] = ""
    return params


def fetch_days(service, settings, date_str):
    """Days with free capacity, starting at `date_str`.

    This is the same `rangesearch=1` call the booking page makes first. The server
    jumps to the first month (on/after `date_str`) that has any availability and
    returns one entry per bookable day in it. An empty list means nothing is free.
    """
    return api_get("/api/timeslots", _timeslots_params(service, settings, date_str, True))


def fetch_day_times(service, settings, day):
    """Exact start times for one bookable day (the call the page makes when you click a day)."""
    data = api_get("/api/timeslots", _timeslots_params(service, settings, day.isoformat(), False))
    times = set()
    for slot in data:
        start = dt.datetime.fromisoformat(slot["start"])
        if start.date() == day:
            times.add(start.strftime("%H:%M"))
    return sorted(times)


def scan(service, settings, horizon_months):
    """Find bookable days.

    Returns (days_in_window, earliest_day). `days_in_window` are bookable days within
    `horizon_months` (sorted list of dicts); `earliest_day` is the first bookable day
    the site offers at all (may be beyond the window), or None.
    """
    today = berlin_now().date()
    limit = add_months(today, horizon_months)
    horizon = today + dt.timedelta(days=int(settings.get("appfuture", 270) or 270))

    found = {}
    earliest = None
    cursor = today
    for _ in range(14):  # safety cap; each pass jumps to the next month with availability
        rows = fetch_days(service, settings, cursor.isoformat())
        days = sorted({dt.datetime.fromisoformat(r["start"]).date()
                       for r in rows if int(r.get("available", 0) or 0) > 0})
        days = [d for d in days if d >= today]
        if not days:
            break
        if earliest is None:
            earliest = days[0]
        for d in days:
            if d <= limit:
                found[d] = {"key": d.isoformat(), "date": d, "days": (d - today).days, "times": []}
        last = days[-1]
        nxt = add_months(last.replace(day=1), 1)
        if days[0] > limit or nxt > limit or nxt > horizon:
            break
        cursor = nxt
    return sorted(found.values(), key=lambda s: s["date"]), earliest


def add_times(service, settings, slots, max_days=15):
    """Fill in exact times for the first few days (used when notifying)."""
    for s in slots[:max_days]:
        try:
            s["times"] = fetch_day_times(service, settings, s["date"])
        except Exception:
            pass


def describe_earliest(earliest):
    if earliest is None:
        return "no bookable day offered at all right now"
    return "earliest bookable day: %s (in %d days)" % (
        earliest.strftime("%a %d.%m.%Y"), (earliest - berlin_now().date()).days)


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
def _ps_quote(s):
    return s.replace("'", "''")


def play_sound(times=3):
    try:
        if SYSTEM == "Darwin":
            snd = "/System/Library/Sounds/Glass.aiff"
            subprocess.Popen(
                ["sh", "-c", "for i in $(seq %d); do afplay '%s'; done" % (times, snd)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif SYSTEM == "Windows":
            import winsound

            for _ in range(times):
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                time.sleep(0.6)
        else:
            print("\a" * times, end="", flush=True)
    except Exception:
        print("\a" * times, end="", flush=True)


def desktop_notify(title, message):
    try:
        if SYSTEM == "Darwin":
            script = 'display notification "%s" with title "%s" sound name "Glass"' % (
                message.replace("\\", "\\\\").replace('"', '\\"'),
                title.replace("\\", "\\\\").replace('"', '\\"'),
            )
            subprocess.run(["osascript", "-e", script], check=False, timeout=15)
        elif SYSTEM == "Windows":
            ps = r"""
$ErrorActionPreference = 'Stop'
try {
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
  $t = [Security.SecurityElement]::Escape('%s'); $m = [Security.SecurityElement]::Escape('%s')
  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
  $xml.LoadXml("<toast scenario='reminder'><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
  $id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($toast)
} catch {
  Add-Type -AssemblyName System.Windows.Forms
  $n = New-Object System.Windows.Forms.NotifyIcon
  $n.Icon = [System.Drawing.SystemIcons]::Information
  $n.Visible = $true
  $n.ShowBalloonTip(15000, '%s', '%s', 'Info')
  Start-Sleep -Seconds 16
  $n.Dispose()
}
""" % (_ps_quote(title), _ps_quote(message), _ps_quote(title), _ps_quote(message))
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.run(["notify-send", title, message], check=False, timeout=15)
    except Exception as exc:
        log("Desktop notification failed: %s" % exc)


def ntfy_notify(topic, title, message, priority="urgent"):
    """Optional phone push via https://ntfy.sh (install the ntfy app, subscribe to topic)."""
    try:
        req = urllib.request.Request(
            "https://ntfy.sh/" + urllib.parse.quote(topic),
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8").decode("latin-1", "ignore") or "Termin",
                "Priority": priority,
                "Tags": "bell",
                "Click": BOOKING_URL,
            },
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:
        log("ntfy notification failed: %s" % exc)


def notify(new_slots, all_slots, args):
    best = min(new_slots, key=lambda s: s["date"])
    tier = tier_for(best["days"])
    emoji = TIERS[tier][0]
    counts = {"red": 0, "yellow": 0, "blue": 0}
    for s in all_slots:
        counts[tier_for(s["days"])] += 1
    summary = "  ".join("%s %d" % (TIERS[t][0], counts[t]) for t in ("red", "yellow", "blue") if counts[t])
    title = "%s Termin frei! (%s)" % (emoji, TIERS[tier][2])
    when = best["date"].strftime("%a %d.%m.%Y")
    if best.get("times"):
        when += " " + ", ".join(best["times"][:4])
    msg = "Earliest: %s (in %d days). %s. Book now!" % (when, best["days"], summary)
    desktop_notify(title, msg)
    play_sound()
    if args.ntfy:
        ntfy_notify(args.ntfy, "Termin frei (%s)" % TIERS[tier][2], msg)
    if args.open:
        try:
            import webbrowser

            webbrowser.open(BOOKING_URL)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Keep the computer awake while running
# --------------------------------------------------------------------------- #
def prevent_sleep():
    try:
        if SYSTEM == "Darwin":
            subprocess.Popen(
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        if SYSTEM == "Windows":
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            return True
    except Exception:
        pass
    return False


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def print_slots(slots):
    for sl in slots:
        tier = tier_for(sl["days"])
        times = ", ".join(sl["times"][:12])
        if len(sl["times"]) > 12:
            times += ", ... (+%d)" % (len(sl["times"]) - 12)
        line = "%s %s  in %3d days  %s" % (TIERS[tier][0], sl["date"].strftime("%a %d.%m.%Y"), sl["days"], times)
        print("    " + paint(line, TIERS[tier][1]), flush=True)


def pick_service(services, wanted):
    for s in services:
        if int(s["sid"]) == int(wanted):
            return s
    raise SystemExit("Service id %s not found. Use --list-services." % wanted)


def main():
    p = argparse.ArgumentParser(description="Watch for Heilbronn residence-permit appointments.")
    p.add_argument("--service", type=int, default=DEFAULT_SERVICE_ID,
                   help="service id (default %d = Verlängerung Aufenthaltserlaubnis)" % DEFAULT_SERVICE_ID)
    p.add_argument("--interval", type=int, default=60, help="seconds between checks (default 60, min %d)" % MIN_INTERVAL)
    p.add_argument("--months", type=int, default=DEFAULT_MONTHS,
                   help="look-ahead window in months (default %d)" % DEFAULT_MONTHS)
    p.add_argument("--open", action="store_true", help="also open the booking page in your browser on a hit")
    p.add_argument("--ntfy", metavar="TOPIC", help="also push to your phone via ntfy.sh topic")
    p.add_argument("-v", "--verbose", action="store_true", help="print a line for every check")
    p.add_argument("--once", action="store_true", help="check once and exit")
    p.add_argument("--test", action="store_true", help="send a test notification and exit")
    p.add_argument("--list-services", action="store_true", help="list services and exit")
    p.add_argument("--no-sleep-lock", action="store_true", help="do not prevent the computer from sleeping")
    args = p.parse_args()
    args.interval = max(args.interval, MIN_INTERVAL)
    TIERS["blue"] = (TIERS["blue"][0], TIERS["blue"][1], "within %d months" % args.months)

    if args.test:
        fake = [{"date": berlin_now().date() + dt.timedelta(days=5), "days": 5, "key": "t", "times": ["09:50"]}]
        print("Sending test notification...")
        notify(fake, fake, args)
        time.sleep(3)
        print("Tier colours:")
        for t in ("red", "yellow", "blue"):
            print("   " + paint("%s %s" % (TIERS[t][0], TIERS[t][2]), TIERS[t][1]))
        return

    services = load_services()
    if args.list_services:
        for s in services:
            print("%7d  %-26s %s (%d min)" % (s["sid"], s["sg"], s["s"], s.get("duration", 0)))
        return

    service = pick_service(services, args.service)
    settings = load_settings()

    log("Watching: %s - %s" % (service["sg"], service["s"]))
    log("Window: next %d months | every ~%ds | %s" % (args.months, args.interval, BOOKING_URL))
    log("Tiers: %s  %s  %s" % (
        paint("%s <=7 days" % TIERS["red"][0], TIERS["red"][1]),
        paint("%s <=30 days" % TIERS["yellow"][0], TIERS["yellow"][1]),
        paint("%s <=%d months" % (TIERS["blue"][0], args.months), TIERS["blue"][1]),
    ))
    if not args.once and not args.no_sleep_lock and prevent_sleep():
        log("Sleep prevention on (computer stays awake while this runs).")
    log("Leave this window open. Press Ctrl+C to stop.")

    notified = set()
    failures = 0
    checks = 0
    while True:
        try:
            slots, earliest = scan(service, settings, args.months)
            failures = 0
            checks += 1
            current = {s["key"] for s in slots}
            notified &= current  # forget vanished slots so a re-opened one alerts again
            new = [s for s in slots if s["key"] not in notified]

            if new:
                add_times(service, settings, slots)
                log(paint("APPOINTMENT AVAILABLE! %d day(s):" % len(slots), "1;97"))
                print_slots(slots)
                notify(new, slots, args)
                notified |= current
            elif slots:
                log("Still available (already notified): %d day(s)" % len(slots))
            elif args.verbose or checks == 1 or checks % 30 == 0:
                log("Check #%d: nothing in the next %d months | %s" % (
                    checks, args.months, describe_earliest(earliest)))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError) as exc:
            failures += 1
            log("Check failed (%s). Retrying... [%d]" % (exc, failures))
            if failures == 5:
                desktop_notify("terminjetzt: connection problems", "5 failed checks in a row. Is the internet up?")
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # never die while unattended
            failures += 1
            log("Unexpected error: %r" % exc)

        if args.once:
            return
        wait = min(args.interval * (1 + 0.2 * min(failures, 5)), 600)
        time.sleep(wait * random.uniform(0.85, 1.15))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
