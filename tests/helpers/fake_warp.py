#!/usr/bin/env python3
"""A faithful-enough warp-cli stand-in for the test suite.

Models the behaviour that actually broke the original tool:
  * `registration delete` is asynchronous: `registration new` right after it
    fails with "Old registration is still around" until FAKE_WARP_MIN_SETTLE
    seconds have passed.
  * a tunnel can only be "connected" when a registration exists.
State lives in $FAKE_WARP_STATE (JSON), calls are logged to $FAKE_WARP_LOG.
"""
import json, os, sys, time

STATE = os.environ.get("FAKE_WARP_STATE", "/tmp/fake-warp-state.json")
LOG = os.environ.get("FAKE_WARP_LOG", "")
MIN_SETTLE = float(os.environ.get("FAKE_WARP_MIN_SETTLE", "8"))
FORCE_FAIL = int(os.environ.get("FAKE_WARP_FAIL_NEW", "0"))
REG_DELAY_MS = int(os.environ.get("FAKE_WARP_REG_DELAY_MS", "0"))
KEYS_DELAY_MS = int(os.environ.get("FAKE_WARP_KEYS_DELAY_MS", "0"))
# how many rotate-keys calls return the *same* block (simulates Cloudflare
# handing back a /64 that is already burned)
KEYS_REPEAT = int(os.environ.get("FAKE_WARP_KEYS_REPEAT", "0"))
CONNECT_DELAY_MS = int(os.environ.get("FAKE_WARP_CONNECT_DELAY_MS", "0"))


def load():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {"connected": False, "registered": False, "reg_count": 0,
                "deleted_at": 0.0, "fail_new": FORCE_FAIL}


def save(d):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, STATE)


def log(msg):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {' '.join(sys.argv[1:])} -> {msg}\n")


def main():
    raw = sys.argv[1:]
    args = [a for a in raw if not a.startswith("--")]
    st = load()
    cmd = args[0] if args else "status"
    log("called")

    if cmd == "status":
        if st.get("connected"):
            print("Status update: Connected")
            print("Network: healthy")
        elif st.get("registered"):
            print("Status update: Disconnected")
        else:
            print("Status update: Unable")
            print("Reason: Registration Missing due to: Does not exist in API")
        return 0

    if cmd == "connect":
        if not st.get("registered"):
            print('Error: Missing registration. Try running: "warp-cli registration new"')
            log("connect failed: no registration")
            return 1
        if CONNECT_DELAY_MS:
            time.sleep(CONNECT_DELAY_MS / 1000.0)
        st["connected"] = True
        save(st)
        log("connected")
        return 0

    if cmd == "disconnect":
        st["connected"] = False
        save(st)
        log("disconnected")
        return 0

    if cmd == "mode":
        st["mode"] = args[1] if len(args) > 1 else ""
        save(st)
        print("Success")
        log(f"mode -> {st['mode']}")
        return 0

    if cmd == "proxy":
        if len(args) > 2 and args[1] == "port":
            st["proxy_port"] = int(args[2])
            save(st)
            log(f"proxy port -> {args[2]}")
        print("Success")
        return 0

    if cmd == "tunnel":
        sub = args[1] if len(args) > 1 else ""
        if sub == "rotate-keys":
            if not st.get("registered"):
                print('Error: Missing registration. Try running: "warp-cli registration new"')
                log("rotate-keys failed: no registration")
                return 1
            if int(st.get("keys_repeat", KEYS_REPEAT)) > 0:
                st["keys_repeat"] = int(st["keys_repeat"]) - 1
                save(st)
                log("rotate-keys: kept the same block (repeat)")
                print("Success")
                return 0
            if KEYS_DELAY_MS:
                time.sleep(KEYS_DELAY_MS / 1000.0)
            st["reg_count"] = int(st.get("reg_count", 0)) + 1
            st["connected"] = True
            save(st)
            print("Success")
            log(f"rotate-keys -> block #{st['reg_count']}")
            return 0

    if cmd == "registration":
        sub = args[1] if len(args) > 1 else ""
        if sub == "delete":
            st["registered"] = False
            st["connected"] = False
            st["deleted_at"] = time.time()
            save(st)
            log("registration deleted")
            return 0
        if sub == "new":
            if st.get("registered"):
                print('Error: Old registration is still around. '
                      'Try running: "warp-cli registration delete"')
                log("new failed: old registration still around")
                return 1
            waited = time.time() - float(st.get("deleted_at") or 0)
            if st.get("deleted_at") and waited < MIN_SETTLE:
                print('Error: Old registration is still around. '
                      'Try running: "warp-cli registration delete"')
                log(f"new failed: only {waited:.1f}s since delete")
                return 1
            if int(st.get("fail_new", 0)) > 0:
                st["fail_new"] = int(st["fail_new"]) - 1
                save(st)
                print("Error: Failed to communicate with the WARP API")
                log("new failed: forced API failure")
                return 1
            if REG_DELAY_MS:
                time.sleep(REG_DELAY_MS / 1000.0)
            st["registered"] = True
            st["reg_count"] = int(st.get("reg_count", 0)) + 1
            save(st)
            print("Success")
            log(f"registration created #{st['reg_count']}")
            return 0
        if sub in ("show", "list"):
            if st.get("registered"):
                print("Account type: Free")
                return 0
            print('Error: Missing registration. Try running: "warp-cli registration new"')
            return 1

    if cmd in ("mode", "settings", "debug"):
        return 0

    print(f"fake warp-cli: unhandled command {args}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
