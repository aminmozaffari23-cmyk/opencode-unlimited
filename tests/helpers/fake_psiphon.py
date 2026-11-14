#!/usr/bin/env python3
"""psiphon-tunnel-core stand-in for the offline suite.

Models what the controller actually depends on:
  * it reads the generated config and refuses to run without real bootstrap
    values (a placeholder build cannot fetch a server list),
  * it reports the Tunnels notice once, on stdout, in tunnel-core's shape,
  * it stays alive until SIGTERM so the controller owns its lifecycle,
  * every launch is logged with the values a test needs to assert on
    (EgressRegion, DisableReplay, LocalSocksProxyPort, DataRootDirectory).

Nothing here opens a socket: the offline tests read the log, and the live test
uses the real binary.
"""
import json
import os
import signal
import socket
import sys
import threading
import time

from sandbox_scope import fixture_path

LOG = fixture_path("FAKE_PSIPHON_LOG", optional=True)
FAIL = os.environ.get("FAKE_PSIPHON_FAIL", "")
NO_NOTICE = os.environ.get("FAKE_PSIPHON_NO_NOTICE", "")
NOTICE_DELAY = float(os.environ.get("FAKE_PSIPHON_NOTICE_DELAY", "0.2"))


def record(entry: dict) -> None:
    if not LOG:
        return
    with open(LOG, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def main() -> int:
    argv = sys.argv[1:]
    config_path = ""
    for i, a in enumerate(argv):
        if a == "-config" and i + 1 < len(argv):
            config_path = argv[i + 1]
    cfg = {}
    if config_path:
        try:
            with open(config_path) as fh:
                cfg = json.load(fh)
        except Exception as exc:
            record({"event": "config-unreadable", "error": str(exc)})
            print(f"cannot read config: {exc}", flush=True)
            return 2
    else:
        record({"event": "no-config"})
        return 2

    # A placeholder build (all-F sponsor id, no server list) cannot start.
    sponsor = str(cfg.get("SponsorId") or "")
    broken = (not sponsor or set(sponsor) == {"F"}
              or not cfg.get("RemoteServerListURLs"))
    record({"event": "start", "region": cfg.get("EgressRegion", ""),
            "disable_replay": cfg.get("DisableReplay"),
            "socks_port": cfg.get("LocalSocksProxyPort"),
            "data_root": cfg.get("DataRootDirectory") or cfg.get("DataStoreDirectory"),
            "sponsor": sponsor, "bootstrap_ok": not broken,
            "pid": os.getpid()})
    if broken:
        print(json.dumps({"noticeType": "Error", "data": {
            "message": "no usable bootstrap values"}}), flush=True)
        return 3
    if FAIL:
        print(json.dumps({"noticeType": "Error",
                          "data": {"message": FAIL}}), flush=True)
        record({"event": "failed", "error": FAIL})
        return 4
    if NO_NOTICE:
        # Stay alive but never claim a tunnel: the controller must time out.
        while True:
            time.sleep(0.2)
    time.sleep(NOTICE_DELAY)
    print(json.dumps({"noticeType": "Tunnels", "data": {
        "count": 1, "region": cfg.get("EgressRegion", "")}}), flush=True)
    record({"event": "tunnels", "region": cfg.get("EgressRegion", "")})

    # A real tunnel opens its SOCKS port as soon as the Tunnels notice fires;
    # the controller treats a listening port as the proof it can carry traffic.
    socks_port = int(cfg.get("LocalSocksProxyPort") or 0)
    listener = None
    if socks_port:
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", socks_port))
            listener.listen(16)
            record({"event": "socks-listening", "port": socks_port})

            def serve():
                while True:
                    try:
                        conn, _ = listener.accept()
                    except OSError:
                        return
                    try:
                        conn.close()
                    except OSError:
                        pass
            threading.Thread(target=serve, daemon=True).start()
        except OSError as exc:
            record({"event": "socks-failed", "error": str(exc)})

    stop = {"done": False}

    def bye(*_):
        stop["done"] = True
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    while not stop["done"]:
        time.sleep(0.2)
    record({"event": "stopped", "region": cfg.get("EgressRegion", "")})
    if listener is not None:
        try:
            listener.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
