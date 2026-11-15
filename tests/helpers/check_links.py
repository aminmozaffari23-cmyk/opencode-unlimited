#!/usr/bin/env python3
"""Verify every relative markdown link in the flagship docs resolves.

A flagship repo must not ship dead links: a user clicking from README into a
guide that 404s loses trust in the whole project. This checks the six docs a
user is most likely to follow and fails on any relative link whose target does
not exist in the tree.

Usage: check_links.py [repo-root]   (default: the repo containing this file)
Exit 0 = all links resolve, 1 = at least one dead link.
"""
import os
import re
import sys

DOCS = ["README.md", "BEGINNER_GUIDE.md", "INSTALL.md", "UNINSTALL.md",
        "SECURITY.md", "CHANGELOG.md"]

# [text](target) and [text](target "title"); also reference-style [text][ref]
# is rare in this repo, so inline links are what we verify.
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def is_external(target: str) -> bool:
    return (target.startswith(("http://", "https://", "mailto:", "#", "/"))
            or target.startswith("data:"))


def main() -> int:
    if len(sys.argv) > 1:
        root = os.path.abspath(sys.argv[1])
    else:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    dead = []
    checked = 0
    for doc in DOCS:
        path = os.path.join(root, doc)
        if not os.path.isfile(path):
            dead.append(f"{doc}: file itself is missing")
            continue
        base = os.path.dirname(path)
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            if not target or is_external(target):
                continue
            # strip anchor and query
            target = target.split("#", 1)[0].split("?", 1)[0]
            if not target:
                continue
            checked += 1
            resolved = os.path.normpath(os.path.join(base, target))
            if not os.path.exists(resolved):
                dead.append(f"{doc}: dead link -> {target}")

    if dead:
        print(f"LINK_CHECK FAIL: {len(dead)} dead link(s) of {checked} checked")
        for d in dead:
            print(f"  {d}")
        return 1
    print(f"LINK_CHECK OK: {checked} relative links resolve across {len(DOCS)} docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
