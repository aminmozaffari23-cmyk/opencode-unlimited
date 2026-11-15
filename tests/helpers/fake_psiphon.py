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
# The controller spawns the real binary with -formatNotices, which switches
# stdout from JSON notices to the console dialect:
#   2026-11-14T14:35:58.395Z Tunnels {"count":1}
# The stand-in must speak the same dialect, or it will happily pass while the
# controller matches on a shape the real binary never emits.
FORMAT_NOTICES = "-formatNotices" in sys.argv
FAIL = os.environ.get("FAKE_PSIPHON_FAIL", "")
NO_NOTICE = os.environ.get("FAKE_PSIPHON_NO_NOTICE", "")
SLOW_FIRST = int(os.environ.get("FAKE_PSIPHON_SLOW_FIRST", "0") or 0)
START_COUNTER = os.environ.get("FAKE_PSIPHON_START_COUNTER", "")
NOTICE_DELAY = float(os.environ.get("FAKE_PSIPHON_NOTICE_DELAY", "0.2"))


def _hold(conn) -> None:
    """Keep a CONNECTed socket open; the bridge pipes bytes through it."""
    try:
        while conn.recv(4096):
            pass
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


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
        if FORMAT_NOTICES:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
            print(f"{stamp} Error {json.dumps({'error': FAIL})}", flush=True)
        else:
            print(json.dumps({"noticeType": "Error",
                              "data": {"message": FAIL}}), flush=True)
        record({"event": "failed", "error": FAIL})
        return 4
    if NO_NOTICE:
        # Stay alive but never claim a tunnel: the controller must time out.
        while True:
            time.sleep(0.2)
    if SLOW_FIRST and START_COUNTER:
        # A cold bootstrap: the first N launches never get a tunnel. Measured
        # on a real runner, where the tunnel core spent >45 s fetching its
        # server list before the first successful tunnel.
        seen = 0
        try:
            with open(START_COUNTER) as fh:
                seen = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            seen = 0
        seen += 1
        try:
            with open(START_COUNTER, "w") as fh:
                fh.write(str(seen))
        except OSError:
            pass
        if seen <= SLOW_FIRST:
            record({"event": "bootstrap", "attempt": seen})
            while True:
                time.sleep(0.2)
    time.sleep(NOTICE_DELAY)
    if FORMAT_NOTICES:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        print(f'{stamp} Tunnels {{"count":1,"region":'
              f'"{cfg.get("EgressRegion", "")}"}}', flush=True)
    else:
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
                """Minimal SOCKS5 CONNECT, like the real tunnel's local proxy.

                Accepting and closing (the old behaviour) made every bridge
                CONNECT fail with 502, so no test could prove that the
                resurrected tunnel actually *carries* traffic.
                """
                while True:
                    try:
                        conn, _ = listener.accept()
                    except OSError:
                        return
                    try:
                        greeting = conn.recv(2)
                        if len(greeting) < 2:
                            conn.close()
                            continue
                        conn.recv(greeting[1])            # methods
                        conn.sendall(b"\x05\x00")
                        head = conn.recv(4)
                        if len(head) < 4 or head[0] != 5:
                            conn.close()
                            continue
                        atyp = head[3]
                        if atyp == 1:
                            conn.recv(4 + 2)
                        elif atyp == 4:
                            conn.recv(16 + 2)
                        else:
                            conn.recv(1 + 2)
                        conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                        record({"event": "socks-connect"})
                        threading.Thread(target=_hold, args=(conn,), daemon=True).start()
                    except OSError:
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
