# SmartPark - Step 1: Fake Sensor Pipeline

This proves the data pipeline works end-to-end, using a FAKE sensor,
before we touch any real hardware.

```
simulator.py  --(MQTT)-->  broker  --(MQTT)-->  subscriber.py  -->  parking.db
 (fake sensor)                                    (backend)          (SQLite)
```

- **simulator.py** pretends to be a parking sensor. Every 2 seconds it
  flips one of 10 spots (A01-A10) between `free` and `occupied` and
  publishes an MQTT message; every ~30 seconds it also sends a heartbeat.
- **subscriber.py** listens for those messages and saves them into
  `parking.db` (a local SQLite file, created automatically). It also
  watches for nodes that stop sending heartbeats and marks their spots
  `unknown` if they go quiet for too long.
- **show_db.py** prints what's currently in the database. Add `--watch`
  to have it auto-refresh in your terminal once a second.
- **dashboard.py** + **dashboard.html** - a tiny local web dashboard.
  Run `python3 dashboard.py` and open http://localhost:8000 to see all
  10 spots as a live color-coded grid (green=free, red=occupied,
  gray=unknown) that updates once a second. No new dependencies - it
  uses Python's built-in `http.server` and plain HTML/JS.
- **message-contract.md** is the full, standalone spec for the MQTT
  topics and JSON payloads - the source of truth every part follows.

## Message contract

Full details live in [`message-contract.md`](message-contract.md). Summary:

- Status topic: `parking/{garage}/{floor}/{spot}/status`
  (example: `parking/union/3/A12/status`)
- Status payload (JSON):
  ```json
  {"garage":"union","floor":3,"spot":"A12","status":"occupied","node":"esp32-u3a","ts":1735689600}
  ```
- `status` is one of: `free`, `occupied`, `unknown`
- A status message is only sent when a spot's status actually changes,
  using QoS 1 (at-least-once) and retained (a new subscriber gets the
  latest state immediately).
- Heartbeat topic: `parking/{garage}/{floor}/{node}/heartbeat`, sent
  every ~30s so the backend knows a node is still alive. If a node
  goes quiet for 90s, the backend marks its spots `status = "unknown"`
  in the database (never trust stale data).

## How to run it

1. **Install dependencies** (do this once):
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the backend** in one terminal:
   ```bash
   python3 subscriber.py
   ```
   It will connect, subscribe, and print `saved <- ...` for every
   message it stores.

3. **Start the fake sensor** in another terminal:
   ```bash
   python3 simulator.py
   ```
   It will print `sent -> ...` for every message it publishes.

   Watch the two terminals - each `sent` line in simulator.py should
   be followed almost immediately by a matching `saved` line in
   subscriber.py.

4. **Watch it live**, in a third terminal - pick one:
   ```bash
   python3 show_db.py --watch        # terminal grid, refreshes every 1s
   ```
   ```bash
   python3 dashboard.py              # then open http://localhost:8000
   ```
   Or just run `python3 show_db.py` (no flag) any time for a one-off peek.

## Switching to a local broker

By default all three scripts use `test.mosquitto.org`, a free public
MQTT broker - fine for testing, but not private and not guaranteed to
always be up.

To run entirely locally instead:

1. Install Mosquitto:
   - macOS: `brew install mosquitto`
   - Linux (Debian/Ubuntu): `sudo apt install mosquitto`
2. Start the broker: `mosquitto -v` (or `brew services start mosquitto`)
3. In **simulator.py** and **subscriber.py**, change:
   ```python
   BROKER = "test.mosquitto.org"
   ```
   to:
   ```python
   BROKER = "localhost"
   ```
4. Run subscriber.py and simulator.py as before.

## Notes

- `parking.db` is created automatically the first time subscriber.py
  runs. Delete it if you want to start fresh.
- Since `test.mosquitto.org` is a shared public broker, in theory
  other people's test traffic could publish to the same topics. For
  this learning exercise that's not a concern, but it's a good reason
  to move to a local or private broker for anything real.
