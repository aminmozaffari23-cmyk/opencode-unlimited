#!/usr/bin/env python3
"""iptables/ip6tables stand-in: records state so tests can assert on it."""
import json, os, sys

from sandbox_scope import checked_path, fixture_path

STATE = fixture_path("FAKE_IPT_STATE")
LOG = fixture_path("FAKE_IPT_LOG", optional=True)
CHAIN = "OUC_LEAKGUARD"


def load():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {"jumped": False, "rules": [], "chain": False}


def save(d):
    with open(STATE + ".tmp", "w") as fh:
        json.dump(d, fh)
    os.replace(STATE + ".tmp", STATE)


def log(msg):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(f"{' '.join(sys.argv[1:])} -> {msg}\n")


def main():
    a = sys.argv[1:]
    st = load()
    op = a[0] if a else ""
    joined = " ".join(a)

    if op == "-C":
        ok = st["jumped"] if (CHAIN in a and "OUTPUT" in a) else st["chain"]
        log(f"check => {ok}")
        return 0 if ok else 1
    if op == "-N":
        st["chain"] = True
        save(st); log("created"); return 0
    if op == "-F":
        st["rules"] = []
        save(st); log("flushed"); return 0
    if op == "-A":
        st["rules"].append(joined)
        save(st); log("appended"); return 0
    if op == "-I":
        if "OUTPUT" in a and CHAIN in a:
            st["jumped"] = True
        save(st); log("inserted"); return 0
    if op == "-D":
        if "OUTPUT" in a and CHAIN in a:
            was = st["jumped"]
            st["jumped"] = False
            save(st); log("deleted jump" if was else "no jump to delete")
            return 0 if was else 1
        save(st); log("deleted rule"); return 0
    if op == "-X":
        st["chain"] = False; st["jumped"] = False
        save(st); log("chain removed"); return 0
    if op == "-nL":
        if not st["chain"]:
            print("iptables: No chain/target/match by that name.")
            return 1
        print(f"Chain {CHAIN} (1 references)")
        for r in st["rules"]:
            print(r)
        return 0
    if op == "-S":
        print(f"-A OUTPUT -j {CHAIN}" if st["jumped"] else "")
        return 0
    log("other")
    return 0


if __name__ == "__main__":
    sys.exit(main())
