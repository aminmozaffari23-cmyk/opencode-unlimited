# Phase 3 — the backup route now actually works
## Plain English report

---

## What you asked for

You wanted the fallback built properly: when WARP (Cloudflare's free VPN-ish
tunnel) runs out of usable addresses, the program should switch to Psiphon by
itself — start it, prove the new address works, blacklist the dead one, and carry
on with the same conversation. Then you wanted it proven on a throwaway machine,
with before/after checks, and the machine destroyed afterwards.

## The short version

I built it, tested it on a throwaway machine, and it **failed**. Not the idea —
the wiring. Then I found five separate bugs, fixed them, and the second run
worked:

| What | Result |
| --- | --- |
| The five broken WARP attempts → Psiphon by itself | ✅ exactly on attempt 5, no human |
| A quota wall fixed with nobody touching anything | ✅ 7.37 seconds |
| Three different countries, three different addresses, same conversation | ✅ Germany → Netherlands → Canada, "remember this token" recalled 3 times out of 3 |
| Before/after check of the machine | ✅ 18 out of 18 |
| The machine itself | ✅ destroyed, confirmed gone |

---

## Why the first attempt failed — the interesting part

The program was passing all its own tests. **64 tests, zero failures.** And it
still could not switch to Psiphon on a real machine. That is the most useful
lesson in this whole project, so here it is in plain terms.

The program talks to Psiphon and waits for it to print a specific line meaning
"my tunnel is up":

```
{"noticeType": "Tunnels", "data": {"count": 1}}
```

The program starts Psiphon with a switch called `-formatNotices`. That switch
makes Psiphon print the *same information in a different layout*:

```
2026-11-14T17:55:43.156Z Tunnels {"count":1}
```

The program was looking for the first layout. Psiphon was printing the second.
So the tunnel came up perfectly in about 12 seconds, and the program said
"no tunnel within 45 seconds" and gave up. It then quietly went back to WARP,
which is why the status showed "still on WARP" after seven failed attempts.

**Why the tests missed it:** my fake Psiphon for the test suite printed the
first layout, because I wrote the fake by reading the code instead of by reading
Psiphon's output. The fake agreed with the bug. A test can only catch what it is
told to look at, and mine was told to look at my own assumption.

The fix was to accept both layouts, and — more importantly — to make the fake
behave like the real thing: if you start it with `-formatNotices`, it now prints
the console layout, so this bug class cannot come back silently.

---

## The other four bugs, in one line each

2. **It claimed a fallback that never happened.** The program stamped "fallback
   started at 14:20" *before* trying Psiphon. When Psiphon failed, the stamp
   stayed, so the status screen described a rescue that did not exist.

3. **It ignored a quota wall that arrived while it was busy.** Every 2 seconds
   the supervisor checks the AI tool's log for the "you have used up the free
   tier" message. But first it checks the tunnel, and repairing a dead tunnel can
   take a minute. A wall that arrived during that minute got read on the very
   first pass, and that pass says "anything already in the log is old news, skip
   it". So the wall was thrown away, and stayed invisible forever. Now the log is
   read *first*, and the "old news" line is drawn once when the supervisor
   starts, not whenever it happens to get round to looking.

4. **The supervisor's children were handed a broken stdin.** When the program
   runs in the background, it points its three standard streams at its own log
   file. All three — including stdin, and stdin was opened write-only. The AI
   tool (`opencode`) reads from stdin when it starts. Reading a write-only file
   gives an immediate error (`EBADF: bad file descriptor`), so it died before
   saying a word. Every rotation done by the background supervisor therefore
   failed with "could not be confirmed", while the identical rotation typed at a
   prompt worked in 8 seconds. Very confusing until I looked at the file
   descriptors of the running process and saw `0 -> supervisor.log` with a `w`
   (write) flag.

5. **The tunnel died when the command that started it exited.** The program gave
   Psiphon a pipe to talk into. That pipe belonged to the command that started
   it. The command finished, the pipe closed, Psiphon got "broken pipe" and
   died — about 50 seconds after reporting a successful rotation. The HTTP bridge
   then answered "502 Bad Gateway" to everything. The tunnel now writes to its
   own log file, so it belongs to the machine rather than to the shell that asked
   for it.

---

## The three tests you asked for

### Test 1 — break WARP, step back, watch

I deliberately destroyed the WARP registration and then just asked for a
rotation, over and over, with the threshold set to 5:

```
attempt 1 → warp     (fail 1)
attempt 2 → warp     (fail 2)
attempt 3 → warp     (fail 3)
attempt 4 → warp     (fail 4)
attempt 5 → psiphon  ✅  address 138.197.197.180
```

Nothing was typed to Psiphon. It escalated on the fifth failure, exactly where
the setting says, and the address it came out on was not the machine's own
address (51.143.4.80).

### Test 2 — three countries, one conversation

I started a real conversation asking the AI to remember a token
(`TOKEN-FALLBACK-78851`), then rotated through Psiphon and asked the same
session to repeat it:

| Round | Country | Exit address | Proof it worked | Did it remember? |
| --- | --- | --- | --- | --- |
| 1 | Germany | 212.227.185.145 | real request succeeded | ✅ |
| 2 | Netherlands | 206.189.15.19 | real request succeeded | ✅ |
| 3 | Canada | 159.203.56.122 | real request succeeded | ✅ |

Three countries, three different addresses, **the same conversation each time**,
about 15 seconds per hop. The AI tool's proxy setting never changed — the bridge
in the middle noticed the switch and followed it.

### Test 3 — did I break the machine?

I photographed the machine before and after — network settings, DNS, firewall
rules, routing, the direct public IP — and compared. **18 checks, 18 passes.**
Same IP, same DNS file byte for byte, same firewalls, and after the shutdown:
no leftover Psiphon process, ports closed, and the exact file that ran the tests
is the exact file that is pushed to your repository.

---

## One thing worth knowing about the free tier

The free models are metered **per address**, and the allowance is small. Early on
I was making three or four AI calls per round, which burned each fresh address
mid-round — one round ended with "quota walled" 30 seconds after a successful
rotation. At one point all four free models were walled from the same WARP
address.

That is not a bug in the product: the product correctly *refused* the walled
addresses and kept trying others. It was my test being wasteful. Once I seeded
the conversation on a fresh address and made exactly one call per round, the run
went 3 for 3.

Worth saying plainly: **rotating the address gets you a new allowance, but you
consume it fast.** A test harness is not a normal user — but a real user who
hammers the free tier will hit walls more often than this report suggests.

---

## What is still not proven

Honesty section:

1. **A wall that arrives while Psiphon is the one carrying traffic.** I proved
   the wall path on WARP (7.37 s, nobody involved) and I proved the Psiphon
   rotation path on demand. The combination — wall detected *while* a Psiphon
   block is live — runs through the same code, but I did not sit and watch that
   exact trigger happen. It needs a deliberate test.
2. **A long run.** The longest continuous session here was about four hours, 20
   rotations, 17 of them on Psiphon. The "does it hold up for a whole working
   day" question is not answered.
3. **The kill-switch with Psiphon.** Everything was tested in proxy mode, where
   no firewall is managed. Turning the strict firewall on while a Psiphon address
   is live is untested — that is the next piece of work if you want the full
   lockdown mode to be trustworthy.

---

## Where things stand

* Your repository `main` is at `ccc0c7e`, pushed, working tree clean.
* 65 offline tests pass (3 new ones: the two notice layouts, the broken stdin,
  and the tunnel outliving its command), 0 failures.
* The evidence is in `/home/user/lab3/evidence/` — raw snapshots, per-test JSON,
  the supervisor log, the 18-check audit, and a file proving the tested binary is
  the pushed one.
* The throwaway machine is deleted. GitHub returns "404 Not Found" for it. Your
  own two codespaces were not touched.
