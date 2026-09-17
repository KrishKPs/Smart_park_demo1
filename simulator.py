"""
simulator.py - the FAKE sensor.

This script pretends to be a parking sensor node. Real sensors will
eventually run on ESP32 microcontrollers and publish the same kind of
MQTT messages this script does - so getting the message format right
here means the backend (subscriber.py) never has to change later.

What it does, in plain English:
  1. Connects to an MQTT broker (a message relay server).
  2. Keeps track of 10 parking spots (A01 .. A10) and whether each is
     currently "free" or "occupied".
  3. Every 2 seconds, picks one random spot and FLIPS its state
     (free -> occupied, or occupied -> free).
  4. Publishes a JSON message describing that change to the broker,
     using QoS 1 (the broker guarantees at-least-once delivery) and
     "retained" (the broker keeps the last message so a backend that
     connects later immediately knows the current state).
  5. Every ~30 seconds, also publishes a "heartbeat" message so the
     backend knows this sensor node is still alive.

A status message is only ever sent when a spot's state actually
changes - that mirrors how a real sensor works (it doesn't spam
updates every second, only when a car arrives or leaves).
"""

import json
import random
import time

import paho.mqtt.client as mqtt

# --- Configuration -----------------------------------------------------
# Change this to "localhost" once you have a local Mosquitto broker
# running (see README.md). test.mosquitto.org is a free public broker
# meant for testing - don't rely on it for anything real/production.
BROKER = "localhost"
PORT = 1883

GARAGE = "union"
FLOOR = 3
NODE_ID = "esp32-u3a"  # pretend hardware ID for this sensor node

SPOT_IDS = [f"A{n:02d}" for n in range(1, 11)]  # A01, A02, ... A10

# How long to wait between simulated state changes, in seconds.
PUBLISH_INTERVAL_SECONDS = 2

# How often to send a "still alive" heartbeat, in seconds. Real ESP32
# nodes will do this too, so the backend can detect a dead/offline node.
HEARTBEAT_INTERVAL_SECONDS = 30


def build_status_topic(garage: str, floor: int, spot: str) -> str:
    """Build the MQTT topic string for a spot status update, per the contract:
    parking/{garage}/{floor}/{spot}/status
    """
    return f"parking/{garage}/{floor}/{spot}/status"


def build_status_payload(garage: str, floor: int, spot: str, status: str, node: str) -> dict:
    """Build the JSON payload dict for a status message, per the contract."""
    return {
        "garage": garage,
        "floor": floor,
        "spot": spot,
        "status": status,
        "node": node,
        "ts": int(time.time()),  # unix seconds
    }


def build_heartbeat_topic(garage: str, floor: int, node: str) -> str:
    """Build the MQTT topic string for a node heartbeat, per the contract:
    parking/{garage}/{floor}/{node}/heartbeat
    """
    return f"parking/{garage}/{floor}/{node}/heartbeat"


def build_heartbeat_payload(node: str) -> dict:
    """Build the JSON payload dict for a heartbeat message.

    Just says "I'm alive" - the node id and the time it sent this.
    """
    return {
        "node": node,
        "ts": int(time.time()),
    }


def main():
    # spot_state remembers what each spot is currently doing, so we can
    # detect real changes instead of just publishing random noise.
    # We start every spot as "free".
    spot_state = {spot: "free" for spot in SPOT_IDS}

    client = mqtt.Client()
    print(f"Connecting to broker {BROKER}:{PORT} ...")
    client.connect(BROKER, PORT, keepalive=60)

    # The network loop needs to run in the background so the client can
    # send/receive MQTT packets (pings, acks, etc.) while our code sleeps.
    client.loop_start()

    print(f"Simulating sensor node '{NODE_ID}' for {GARAGE} floor {FLOOR}, "
          f"spots {SPOT_IDS[0]}..{SPOT_IDS[-1]}")
    print(f"Publishing a changed spot every {PUBLISH_INTERVAL_SECONDS}s and a "
          f"heartbeat every {HEARTBEAT_INTERVAL_SECONDS}s. Press Ctrl+C to stop.\n")

    # Send one heartbeat immediately on startup, then track when the next
    # one is due so we don't need a second thread just for heartbeats.
    last_heartbeat_time = 0.0

    try:
        while True:
            # Pick a random spot and flip its state (free <-> occupied).
            spot = random.choice(SPOT_IDS)
            new_status = "occupied" if spot_state[spot] == "free" else "free"
            spot_state[spot] = new_status

            topic = build_status_topic(GARAGE, FLOOR, spot)
            payload = build_status_payload(GARAGE, FLOOR, spot, new_status, NODE_ID)
            payload_json = json.dumps(payload)

            # QoS 1 = "at least once" delivery. retain=True means the
            # broker keeps this as the "last known message" on this
            # topic, so a backend that connects later gets it right away.
            client.publish(topic, payload_json, qos=1, retain=True)

            print(f"sent -> topic={topic} payload={payload_json}")

            # If it's been long enough, also send a heartbeat so the
            # backend knows this node is still alive.
            now = time.time()
            if now - last_heartbeat_time >= HEARTBEAT_INTERVAL_SECONDS:
                hb_topic = build_heartbeat_topic(GARAGE, FLOOR, NODE_ID)
                hb_payload = build_heartbeat_payload(NODE_ID)
                hb_payload_json = json.dumps(hb_payload)
                client.publish(hb_topic, hb_payload_json, qos=1)
                print(f"sent -> topic={hb_topic} payload={hb_payload_json} (heartbeat)")
                last_heartbeat_time = now

            time.sleep(PUBLISH_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping simulator...")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
