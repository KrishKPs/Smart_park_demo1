# SmartPark Message Contract

This is the single source of truth for how sensors (real or fake) talk to
the backend. Every part of the system - simulator, ESP32 firmware, and
the backend subscriber - must follow this exactly. If it ever needs to
change, update this file first and call it out, don't change it silently.

This is a standalone copy of section 6 of `claude.md`, kept in sync with it.

---

## Topics

Topics are hierarchical and lowercase, no spaces.

**Spot status** - a sensor publishes this only when a spot's state CHANGES:
```
parking/{garage}/{floor}/{spot}/status
```
Example: `parking/union/3/A12/status`

**Node heartbeat** - each sensor node publishes this every ~30s so the
backend knows it's still alive:
```
parking/{garage}/{floor}/{node}/heartbeat
```
Example: `parking/union/3/esp32-u3a/heartbeat`

---

## Payloads

**Spot status (JSON):**
```json
{
  "garage": "union",
  "floor": 3,
  "spot": "A12",
  "status": "occupied",
  "node": "esp32-u3a",
  "ts": 1735689600
}
```

| Field  | Type    | Meaning                                          |
|--------|---------|---------------------------------------------------|
| garage | string  | short lowercase garage id (union, north, ...)    |
| floor  | integer | floor number                                     |
| spot   | string  | row letter + 2 digits (A12)                      |
| status | string  | one of exactly: `free`, `occupied`, `unknown`    |
| node   | string  | which node sent it (helps locate a broken node)  |
| ts     | integer | unix timestamp, seconds                          |

**Node heartbeat (JSON):**
```json
{
  "node": "esp32-u3a",
  "ts": 1735689600
}
```

| Field | Type    | Meaning                          |
|-------|---------|-----------------------------------|
| node  | string  | which node sent this heartbeat   |
| ts    | integer | unix timestamp, seconds          |

---

## Rules

- Publish a status message **only on change** (free <-> occupied), not every reading.
- Use **QoS 1** on all messages (status and heartbeat).
- Status messages are **retained** by the broker, so a new subscriber gets
  the last known state immediately without waiting for the next change.
- Each node sends a **heartbeat every ~30s**. If a backend hasn't heard
  from a node (status OR heartbeat) in **90s** (3x the interval, to allow
  for a missed beat or two), it marks that node's known spots `unknown` -
  never leave stale data on screen.
- **Sensors never report `reserved`.** A sensor only knows physical
  presence (`free`/`occupied`). "Reserved" is decided by the BACKEND by
  combining sensor status with reservation records (a later phase).
  Keep the sensor dumb.
- The device sets `ts` on the message it sends.

---

## Our specifics

- Garages: `union` (Union Deck), `north` (North Deck), `east` (East Deck), `west` (West Lot).
- Floors: `1, 2, 3, 4, ...`
- Spots: row letter (A-D) + two digits (01-10), e.g. `B04`.
- Node ids: `esp32-{garage-initial}{floor}{hub-letter}`, e.g. `esp32-u3a`.
