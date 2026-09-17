"""
show_db.py - a peek script.

A tiny read-only tool to check what's currently sitting in parking.db,
without needing a separate SQLite GUI. Run it any time while (or after)
subscriber.py is running to see the live state.

Prints:
  1. Every spot's current status (from spot_status).
  2. A summary like "6 of 10 free".
  3. The total number of rows ever logged in occupancy_events.

Usage:
  python3 show_db.py            <- print once and exit
  python3 show_db.py --watch    <- clear the screen and reprint every
                                    second, so you can watch spots
                                    change live without a browser
"""

import os
import sqlite3
import sys
import time

DB_FILE = "parking.db"

# How often --watch mode refreshes, in seconds.
WATCH_INTERVAL_SECONDS = 1


def print_snapshot():
    """Print the current spot table + summary once. Returns True if there
    was data to show (used so --watch mode can say 'waiting...' otherwise).
    """
    conn = sqlite3.connect(DB_FILE)

    # Order by spot name so the output is stable/readable (A01, A02, ...).
    rows = conn.execute(
        "SELECT spot, garage, floor, status, updated_at "
        "FROM spot_status ORDER BY spot"
    ).fetchall()

    if not rows:
        print("No spots recorded yet. Is subscriber.py running and "
              "has simulator.py sent any messages?")
        conn.close()
        return False

    print("Current spot status:")
    print(f"{'SPOT':<8}{'GARAGE':<10}{'FLOOR':<8}{'STATUS':<12}{'UPDATED_AT'}")
    free_count = 0
    for spot, garage, floor, status, updated_at in rows:
        print(f"{spot:<8}{garage:<10}{floor:<8}{status:<12}{updated_at}")
        if status == "free":
            free_count += 1

    total_spots = len(rows)
    print(f"\n{free_count} of {total_spots} free")

    (event_count,) = conn.execute(
        "SELECT COUNT(*) FROM occupancy_events"
    ).fetchone()
    print(f"Total occupancy events logged: {event_count}")

    conn.close()
    return True


def clear_screen():
    """Clear the terminal so --watch mode redraws in place instead of
    scrolling forever. 'cls' on Windows, 'clear' everywhere else.
    """
    os.system("cls" if os.name == "nt" else "clear")


def main():
    watch_mode = "--watch" in sys.argv

    if not watch_mode:
        print_snapshot()
        return

    print(f"Watching parking.db every {WATCH_INTERVAL_SECONDS}s. Press Ctrl+C to stop.")
    try:
        while True:
            clear_screen()
            print(f"SmartPark - live view (refreshing every {WATCH_INTERVAL_SECONDS}s, Ctrl+C to stop)\n")
            print_snapshot()
            time.sleep(WATCH_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped watching.")


if __name__ == "__main__":
    main()
