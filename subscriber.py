"""
subscriber.py - the backend.

This script is the "brain" of the pipeline. It listens for MQTT
messages published by sensors (real or fake, like simulator.py) and
saves them into a local SQLite database file, parking.db.

What it does, in plain English:
  1. Connects to the same MQTT broker the sensors publish to.
  2. Subscribes to parking/# - the '#' is an MQTT wildcard meaning
     "everything under the parking/ topic tree", so we catch both
     status updates and heartbeats from any garage/floor/spot/node
     without hardcoding them here.
  3. Whenever a STATUS message arrives:
       a. Parse the JSON payload.
       b. UPSERT it into spot_status - a table that holds only the
          CURRENT state of each spot (one row per spot, overwritten
          each time that spot changes).
       c. INSERT it into occupancy_events - an append-only log of
          every single change that has ever happened, useful later
          for analytics like "how often is this spot used".
  4. Whenever a HEARTBEAT message arrives, it just notes "this node
     is alive as of now" in memory (see the Node Health section below).
  5. Commits the database after every message, so no data is lost if
     the script is stopped.

Run this BEFORE simulator.py so it's ready to catch the first message.
"""

import json
import importlib
import sqlite3
import threading
import time

# Load paho dynamically so static analyzers do not fail when the optional
# MQTT dependency is not installed in their configured environment.
try:
    mqtt = importlib.import_module("paho.mqtt.client")
except ImportError as exc:
    mqtt = None
    _mqtt_import_error = exc

# --- Configuration -----------------------------------------------------
# Must match the broker simulator.py (and real sensors) publish to.
# Change this to "localhost" once you have a local Mosquitto broker
# running (see README.md).
BROKER = "localhost"
PORT = 1883

DB_FILE = "parking.db"

# Subscribe to every spot, floor, garage, and node - status AND heartbeat.
SUBSCRIBE_TOPIC = "parking/#"

# A node should heartbeat every ~30s (see simulator.py). If we haven't
# heard from it in this long, we assume it's offline and mark its
# spots "unknown" rather than trusting stale data. 3x the heartbeat
# interval gives some slack for one or two missed beats.
HEARTBEAT_TIMEOUT_SECONDS = 90

# How often the background thread checks for stale (offline) nodes.
HEALTH_CHECK_INTERVAL_SECONDS = 10


# --- Node health tracking (in-memory only) ------------------------------
# We deliberately do NOT add a "nodes" table for this - a nodes/health table is
# planned for later, not now.
# Instead we just remember, in memory, the last time each node was seen
# and which spots it reported. If the process restarts, this resets -
# that's fine for Step 1's fake sensor; a persistent version can come
# later if the team decides it's needed.
#
# node_last_seen: node id -> unix time we last heard from it (status OR heartbeat)
# node_spots:     node id -> set of spot ids that node has reported
# node_marked_unknown: node id -> True if we've already marked it offline,
#                       so we don't re-mark (and re-log events) every check
#
# A lock protects these dicts since two threads touch them: the MQTT
# network thread (via on_message) and the health-check thread below.
health_lock = threading.Lock()
node_last_seen = {}
node_spots = {}
node_marked_unknown = {}

# A second lock protects the SQLite connection itself, since it's now
# used from two threads too (message handling + the health checker).
# SQLite doesn't handle concurrent writes from multiple threads safely
# on its own, so we make sure only one thread touches it at a time.
db_lock = threading.Lock()


def create_tables(conn: sqlite3.Connection):
    """Create the two tables we need, if they don't already exist."""

    # spot_status: ONE row per spot, holding its most recent known
    # state. "spot" is the primary key so re-saving the same spot
    # overwrites its old row instead of creating a duplicate.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spot_status (
            spot TEXT PRIMARY KEY,
            garage TEXT NOT NULL,
            floor INTEGER NOT NULL,
            status TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )

    # occupancy_events: append-only history log. Every message we
    # receive adds a new row here, so nothing is ever overwritten -
    # this is our full audit trail / history of spot changes.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS occupancy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spot TEXT NOT NULL,
            status TEXT NOT NULL,
            ts INTEGER NOT NULL
        )
        """
    )

    conn.commit()


def count_free_spots(conn: sqlite3.Connection) -> int:
    """Return how many spots are currently marked 'free'."""
    cursor = conn.execute(
        "SELECT COUNT(*) FROM spot_status WHERE status = 'free'"
    )
    (count,) = cursor.fetchone()
    return count


def save_status(conn: sqlite3.Connection, garage: str, floor: int, spot: str, status: str, ts: int):
    """Upsert spot_status and append to occupancy_events for one status update."""

    # (a) Upsert into spot_status - "INSERT ... ON CONFLICT" is SQLite's
    # way of saying "insert a new row, but if the primary key already
    # exists, update that row instead."
    conn.execute(
        """
        INSERT INTO spot_status (spot, garage, floor, status, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(spot) DO UPDATE SET
            garage = excluded.garage,
            floor = excluded.floor,
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (spot, garage, floor, status, ts),
    )

    # (b) Insert into occupancy_events - always a new row, never overwritten.
    conn.execute(
        "INSERT INTO occupancy_events (spot, status, ts) VALUES (?, ?, ?)",
        (spot, status, ts),
    )

    conn.commit()


def on_connect(client, userdata, flags, rc):
    """Called automatically by paho-mqtt once the connection succeeds -
    including automatically after a reconnect, so this also tells us
    when we've recovered from a dropped connection.
    """
    if rc == 0:
        print(f"Connected to broker. Subscribing to '{SUBSCRIBE_TOPIC}' ...")
        client.subscribe(SUBSCRIBE_TOPIC, qos=1)
    else:
        print(f"Connection failed with return code {rc}")


def on_disconnect(client, userdata, rc):
    """Called automatically by paho-mqtt when the connection drops.

    test.mosquitto.org is a free public broker meant for testing - it
    can silently drop long-running connections. loop_forever() retries
    the connection for us automatically in the background, but without
    this callback we'd have no way to see that happening, and it would
    just look like messages stopped arriving for no reason.
    """
    if rc != 0:
        print(f"Disconnected from broker unexpectedly (code {rc}). "
              "Reconnecting...")
    else:
        print("Disconnected from broker.")


def handle_status_message(conn: sqlite3.Connection, data: dict):
    """Handle a parsed payload from a .../status topic."""
    garage = data["garage"]
    floor = data["floor"]
    spot = data["spot"]
    status = data["status"]
    node = data["node"]
    ts = data["ts"]

    with db_lock:
        save_status(conn, garage, floor, spot, status, ts)
        free_count = count_free_spots(conn)

    # Remember that this node is alive and which spot it owns, so the
    # health checker knows what to mark "unknown" if this node goes quiet.
    with health_lock:
        node_last_seen[node] = time.time()
        node_spots.setdefault(node, set()).add(spot)
        node_marked_unknown[node] = False

    print(f"saved <- spot={spot} status={status} | free spots now: {free_count}")


def handle_heartbeat_message(data: dict):
    """Handle a parsed payload from a .../heartbeat topic.

    Heartbeats don't touch the database - they just prove a node is
    still alive, so we only need to update our in-memory tracking.
    """
    node = data["node"]

    with health_lock:
        node_last_seen[node] = time.time()
        node_marked_unknown[node] = False

    print(f"heartbeat <- node={node}")


def on_message(client, userdata, msg):
    """Called automatically by paho-mqtt whenever a subscribed message arrives.

    'userdata' is how we pass our open database connection into this
    callback (paho-mqtt calls this function for us, so we can't just
    pass extra arguments directly).
    """
    conn: sqlite3.Connection = userdata

    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Skipping message on {msg.topic}: could not parse JSON ({exc})")
        return

    # Topics look like parking/{garage}/{floor}/{spot}/status
    # or          parking/{garage}/{floor}/{node}/heartbeat
    # so the last segment tells us which kind of message this is.
    topic_kind = msg.topic.rsplit("/", 1)[-1]

    if topic_kind == "status":
        handle_status_message(conn, data)
    elif topic_kind == "heartbeat":
        handle_heartbeat_message(data)
    else:
        print(f"Skipping message on unrecognized topic: {msg.topic}")


def mark_node_offline(conn: sqlite3.Connection, node: str, spots: set):
    """Mark every known spot for a gone-quiet node as 'unknown'.

    We never leave stale data: if we can't hear from a node, we can't
    trust the last status it gave us anymore.
    """
    now = int(time.time())
    with db_lock:
        for spot in spots:
            # We don't know this spot's garage/floor here (only its id), so
            # look up its last known garage/floor from spot_status and just
            # flip the status - garage/floor stay whatever they already were.
            row = conn.execute(
                "SELECT garage, floor FROM spot_status WHERE spot = ?", (spot,)
            ).fetchone()
            if row is None:
                continue
            garage, floor = row
            save_status(conn, garage, floor, spot, "unknown", now)

    print(f"node offline -> node={node} marked spots unknown: {sorted(spots)}")


def health_check_loop(conn: sqlite3.Connection):
    """Background thread: periodically checks for nodes that have gone quiet.

    Runs forever until the main program exits (it's a daemon thread).
    """
    while True:
        time.sleep(HEALTH_CHECK_INTERVAL_SECONDS)

        now = time.time()
        stale_nodes = []

        with health_lock:
            for node, last_seen in node_last_seen.items():
                already_marked = node_marked_unknown.get(node, False)
                if not already_marked and (now - last_seen) > HEARTBEAT_TIMEOUT_SECONDS:
                    stale_nodes.append((node, set(node_spots.get(node, set()))))
                    node_marked_unknown[node] = True

        for node, spots in stale_nodes:
            mark_node_offline(conn, node, spots)


def main():
    if mqtt is None:
        raise RuntimeError(
            "paho-mqtt is required to run subscriber.py; install it with "
            "'python -m pip install paho-mqtt'"
        ) from _mqtt_import_error

    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    create_tables(conn)

    client = mqtt.Client(userdata=conn)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    print(f"Connecting to broker {BROKER}:{PORT} ...")
    client.connect(BROKER, PORT, keepalive=60)

    # Start the health-check thread. It's a daemon thread so it won't
    # stop the program from exiting when we hit Ctrl+C.
    health_thread = threading.Thread(target=health_check_loop, args=(conn,), daemon=True)
    health_thread.start()

    print("Waiting for messages... Press Ctrl+C to stop.\n")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nStopping subscriber...")
    finally:
        client.disconnect()
        conn.close()


if __name__ == "__main__":
    main()
