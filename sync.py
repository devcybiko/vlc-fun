#!/usr/bin/env python3
import argparse
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# -----------------------------
# CONFIGURATION
# -----------------------------

VLC_PASSWORD = "secretpw"       # <-- set your VLC http-password
DEFAULT_PORT = 8080
HTTP_TIMEOUT = 2.0              # seconds


# -----------------------------
# HELPERS
# -----------------------------

def parse_host_port(host_str):
    if ":" in host_str:
        host, port_str = host_str.split(":", 1)
        return host, int(port_str)
    return host_str, DEFAULT_PORT


def get_vlc_status(host, port, password):
    url = f"http://{host}:{port}/requests/status.json"
    try:
        r = requests.get(url, auth=("", password), timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ERROR] status {host}:{port} -> {e}")
        return None


def timed_status_request(host, port, password):
    """
    Wrapper measuring round-trip time.
    """
    t0 = time.perf_counter()
    status = get_vlc_status(host, port, password)
    t1 = time.perf_counter()
    return status, (t1 - t0) * 1000.0   # ms


def extract_precise_time(status):
    """
    Returns playback time as float seconds: position * length
    (falls back to integer 'time')
    """
    if status is None:
        return None

    pos = status.get("position")
    length = status.get("length")

    if pos is not None and length:
        try:
            return float(pos) * float(length)
        except:
            pass

    t = status.get("time")
    if t is not None:
        try:
            return float(t)
        except:
            pass

    return None


def format_time(seconds):
    if seconds is None:
        return "unknown"
    s = float(seconds)
    m = int(s // 60)
    sec = int(s % 60)
    return f"{m:02d}:{sec:02d}"


def seek_vlc_to_time(host, port, password, time_seconds, delta=0.0):
    """
    VLC seek command; VLC truncates to integer seconds.
    """
    delta = 0.0 ### for now, no adjustment
    target = int(max(time_seconds + delta, 0.0))
    params = {"command": "seek", "val": str(target)}
    url = f"http://{host}:{port}/requests/status.json"

    try:
        requests.get(url, params=params, auth=("", password),
                     timeout=HTTP_TIMEOUT).raise_for_status()
        return True, target
    except Exception as e:
        print(f"[ERROR] seek {host}:{port} -> {e}")
        return False, target


# -----------------------------
# DELAY DETECTION
# -----------------------------

def detect_delays(host_strs):
    """
    Poll all hosts in parallel, measure RTT, compute drift vs master.
    Returns:
        delays[] – per-host drift (seconds)
        rtt_ms[] – per-host RTT in ms
    """
    if not host_strs:
        return None, None

    parsed = [parse_host_port(h) for h in host_strs]
    statuses = [None] * len(parsed)
    rtt_ms = [0.0] * len(parsed)

    print("[INFO] Detecting delays...")

    with ThreadPoolExecutor(max_workers=len(parsed)) as executor:
        fut_to_idx = {}
        for i, (h, p) in enumerate(parsed):
            fut = executor.submit(timed_status_request, h, p, VLC_PASSWORD)
            fut_to_idx[fut] = i

        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            statuses[idx], rtt_ms[idx] = fut.result()

    # Master time
    master_time = extract_precise_time(statuses[0])
    if master_time is None:
        print("[WARN] Could not read master time.")
        return None, None

    print(f"[MASTER] time={master_time:.3f}s   RTT={rtt_ms[0]:.2f}ms")

    delays = [0.0] * len(parsed)

    # Slaves
    for i in range(1, len(parsed)):
        host, port = parsed[i]
        slave_time = extract_precise_time(statuses[i])

        print(f"[RTT]   {host}:{port} -> {rtt_ms[i]:.2f}ms")

        if slave_time is None:
            print(f"[DELAY] {host}:{port}: no playback; delay=0.0")
            continue

        drift = master_time - slave_time
        delays[i] = drift

        print(f"[DELAY] {host}:{port}  master={master_time:.3f}  "
              f"slave={slave_time:.3f}  drift={drift:+.3f}s")

    return delays, rtt_ms


# -----------------------------
# SYNC LOGIC
# -----------------------------

def sync_once(host_strs, delays, drift_threshold):
    """
    One sync cycle:
      - read master time
      - seek slaves only if |drift| > drift_threshold
      - re-measure
    """
    parsed = [parse_host_port(h) for h in host_strs]

    master_host, master_port = parsed[0]
    status = get_vlc_status(master_host, master_port, VLC_PASSWORD)
    master_time = extract_precise_time(status)

    if master_time is None:
        print("[WARN] Could not get master time in sync_once.")
        return delays

    print(f"[MASTER] playback={master_time:.3f}s")

    # First run always synchronizes all slaves
    first_run = all(abs(d) < 1e-6 for d in delays)

    # Apply seeks where needed
    for i in range(1, len(parsed)):
        host, port = parsed[i]
        drift = delays[i]

        if not first_run and abs(drift) < drift_threshold:
            print(f"[SLAVE {host}:{port}] |drift|={drift:.3f}s < "
                  f"{drift_threshold:.3f}s -> skipping seek")
            continue

        ok, target = seek_vlc_to_time(
            host, port, VLC_PASSWORD, master_time, drift
        )

        if ok:
            print(f"[SLAVE {host}:{port}] seek -> {target}s "
                  f"(drift {drift:+.3f}s)")
        else:
            print(f"[SLAVE {host}:{port}] seek FAILED")

    # After adjustments, re-detect drift
    new_delays, rtt_ms = detect_delays(host_strs)
    if new_delays is not None:
        delays = new_delays

    return delays


# -----------------------------
# MAIN
# -----------------------------

def main():
    parser = argparse.ArgumentParser(
        description="VLC multi-host sync using high-precision timing."
    )
    parser.add_argument("--interval", "-i", type=float, default=None,
                        help="Sync interval in seconds.")
    parser.add_argument("--drift-threshold", "-d", type=float,
                        default=1.0,
                        help="Drift threshold in seconds before applying a seek.")

    parser.add_argument("hosts", nargs="+",
                        help="VLC hostnames (first is master).")

    args = parser.parse_args()

    hosts = args.hosts
    drift_threshold = args.drift_threshold

    if len(hosts) < 1:
        parser.error("At least one host (the master) is required.")

    delays = [0.0] * len(hosts)  # initial state

    if args.interval is None:
        sync_once(hosts, delays, drift_threshold)
    else:
        try:
            while True:
                delays = sync_once(hosts, delays, drift_threshold)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[EXIT] Stopped.")


if __name__ == "__main__":
    main()
