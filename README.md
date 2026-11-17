```bash
python3 terminjetzt.py --interval 30 --ntfy terminryan --open
```

# terminjetzt

Watches the Stadt Heilbronn Ausländerbehörde booking page ([eTermin](https://www.etermin.net/qtermin-stadtheilbronn-abh?lang=en)) and notifies you when an appointment for the **residence permit extension** (*Verlängerung Aufenthaltserlaubnis*) opens up within the next 2 months.

Leave it running on a Mac or Windows PC. It stays quiet until a bookable slot appears.

## Requirements

- Python 3.8+ (standard library only, nothing to `pip install`)
  - macOS: `python3` is usually already installed
  - Windows: install from [python.org](https://www.python.org/downloads/) and tick **Add Python to PATH**

## Starting it

| Platform | How |
| --- | --- |
| Terminal (any OS) | `python3 terminjetzt.py` (use `python` on Windows) |
| macOS | double-click or run `./start-mac.command` |
| Windows | double-click `start-windows.bat` |

Extra arguments are passed through, e.g. `./start-mac.command --interval 30`.

Keep the window open (minimising is fine). Press `Ctrl+C` to stop.

First run `python3 terminjetzt.py --test` to check that the pop-up, sound and colours work.

## Tiers

| Colour | Appointment is... |
| --- | --- |
| 🔴 Red | within 7 days |
| 🟡 Yellow | within 30 days |
| 🔵 Blue | within 2 months |

The terminal output is coloured. Desktop and phone notifications use the matching emoji, and the title shows the soonest tier found.

## Options

| Option | Meaning |
| --- | --- |
| `--interval N` | seconds between checks (default 60, minimum 20) |
| `--ntfy TOPIC` | also push to your phone through [ntfy.sh](https://ntfy.sh) |
| `--open` | open the booking page in your browser when a slot appears |
| `--months N` | look-ahead window in months (default 2) |
| `--service ID` | watch a different service (default `144512`) |
| `--list-services` | print all bookable services and their ids |
| `--once` | check once and exit |
| `--test` | send a test notification and exit |
| `--no-sleep-lock` | don't prevent the computer from sleeping |

## How it works

1. **Reads the booking site's own data.** The page is a JavaScript app, so the script calls the same JSON API the page uses (`/api/timeslots`) instead of scraping HTML. It sends the header `webid: qtermin-stadtheilbronn-abh`.
2. **Checks each month in the window.** For "next 2 months" it requests the current month and the next two months, as needed.
3. **Keeps only bookable slots.** A slot counts only if `available > 0`. Slots that are full (`available: 0`), already in the past, or more than 2 months away are ignored.
4. **Assigns a tier** from the number of days until the slot: 7 or fewer is red, 30 or fewer is yellow, otherwise blue.
5. **Notifies only on news.** You are alerted when a new slot appears. Slots you've already been told about don't repeat every check. If a slot disappears and comes back, you're alerted again.
6. **Stays alive unattended.**
   - Waits with a little random jitter between checks.
   - Backs off when the network fails, and warns you after 5 failed checks in a row.
   - Prevents sleep while running (`caffeinate` on macOS, a power request on Windows).

## Notifications

- **Desktop:** a system notification (macOS Notification Center / Windows toast) plus three chimes.
- **Phone (optional):** `--ntfy TOPIC` sends a push through ntfy. Install the ntfy app, subscribe to the same topic, and you'll be alerted even when away from the computer. Anyone who knows the topic name can read the alerts, so pick a non-obvious one.
- **Browser (optional):** `--open` opens the booking page so you can book straight away.

### If you don't see a notification

- **macOS:** allow notifications for **Script Editor** in System Settings → Notifications. The pop-ups are sent under that name.
- Turn off **Do Not Disturb / Focus** (macOS) or **Focus Assist** (Windows), which hide pop-ups.
- Keep the volume up.
- Don't close the window, close the laptop lid, or log out. These stop the script. Minimising is fine.

## Changing what it watches

Run `python3 terminjetzt.py --list-services` and pass an id with `--service`, for example `--service 144511` for a first-time residence permit.

## Notes

- The script only reads public availability. It doesn't log in or book anything. You still have to book on the site yourself.
- If eTermin changes its API, the script may stop finding slots. The booking page itself is the source of truth.
