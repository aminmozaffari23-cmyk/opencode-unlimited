# Everything, in plain English — Round 2

**Supersedes round 1.** Round 1 was wrong about two providers, and I said so at
the time as "broken". This round proves you right and me wrong: Psiphon and
VPN Gate both work, and the faults were mine. The old report is kept as
`RERUN-STUDY.md` only as a record of that mistake.

**How to read:** Part 1 is a dictionary — new words for this round are marked
**(new)**. Part 2 is the story using only those words. Part 3 is the results.
Part 4 is what I got wrong.

---

# Part 1 — The dictionary

## 1.1 The basics

**IP address** — the return address on every packet you send. Websites see it.
`4.155.45.96` is one.

**IPv4 / IPv6** — the old and new address formats. IPv4: `192.42.116.104`. IPv6:
`2a09:bac1:76c0:830::1c:31c`. IPv6 addresses arrive in enormous blocks.

**Prefix / subnet / block** — a *block* of addresses given to one organisation,
written `/64` for IPv6 or `/32` for a single IPv4 address. **The key idea of
this whole study:** a company with one `/64` can give you thousands of
addresses that are all in the *same building*. Moving rooms is not moving
buildings.

**Network identity** — my phrase for "which building", not "which room". Two
IPv6 addresses in one `/64` count as **one** identity.

**Egress IP** — the address the outside world sees. The exit door.

## 1.2 The plumbing

**Proxy** — a middleman that carries your traffic so the website sees the
middleman's address.

**SOCKS5** — a bare, flexible proxy language. **Tor, Psiphon** and my VPN Gate
setup all speak it.

**HTTP proxy** — the proxy language web browsers use. **opencode** (your AI tool)
speaks only this, and *refuses* SOCKS5 outright.

**The bridge** — your project's translator, at `ouc.py` line 1241. It accepts
HTTP-proxy traffic from opencode and re-sends it as SOCKS5 to a provider. Without
this bridge, opencode and Tor literally cannot talk. **Round 1's biggest failure
was forgetting to use it.**

**Port** — a numbered door on a machine. `:9050` is Tor's door.

**Tor** — free network routing you through three volunteer computers; the last
one is the **exit node**, and its address is your egress IP.

**WARP** — Cloudflare's free VPN-ish client. Fast, and speaks HTTP proxy
natively, so it needs no bridge.

**Psiphon** — free anti-censorship VPN. Speaks SOCKS5, so it needs your bridge.

**VPN Gate** — a public list of volunteer-run OpenVPN servers, mostly in Japan.

**OpenVPN** — the program that dials those servers using a `.ovpn` settings file.

**(new) Bootstrap credentials** — Psiphon's software ships with an identity
(`SponsorId`, `PropagationChannelId`) and — crucially — a **remote server list
URL** plus a **signature public key**. Round 1 used placeholder values (`FFFF…`)
and no server list, so Psiphon could never download a list of servers. It had
nothing to connect *to*. That is why it looked broken. This round used the real
public values, and it worked immediately.

## 1.3 The product

**opencode** — the AI assistant program you type into. Version 1.19.5.

**opencode-unlimited** — your project. When opencode hits its free limit, your
project rotates the IP so the service sees a fresh user, then continues the same
conversation.

**`ouc.py`** — the main program, ~5,150 lines. Where all of this lives.

**Rotation** — changing the egress IP. The core action.

**Session** — a saved opencode conversation, with an id like
`ses_e26166e36ffewOLk1uBBZfTzOa`.

**Continuity** — whether the conversation survives a rotation. Tested by asking
the AI to memorise a token, rotating, then asking for the token back **without
repeating it**. If it answers, memory survived.

**`ouc hop`** — your project's own rotation command. Used this round for WARP,
so even the rotation itself came from your code.

**`ouc verify`** — your project's "is the free tier usable right now?" command.
One real request, structured answer.

**`ouc resume`** — your project's "continue this conversation" command. Two real
requests: a preflight check, then the actual continuation.

**(new) Free-tier wall / quota wall** — the polite refusal opencode gives when
the free allowance for your IP is used up:
*"Free usage limit reached. Free models work in OpenCode; subscribe to OpenCode
Go to keep going"*. The **product's entire reason to exist** is getting past this
wall by rotating.

**(new) `flock` / lease** — an operating-system lock so only one process owns
recovery at a time. Your project uses one, which is good design — and it blocked
my study until I stopped the background supervisor, because that supervisor
legitimately held the lock.

## 1.4 The workshop

**Codespace** — a disposable Linux computer in the cloud. Created, used, deleted.
**Both this round's machine and round 1's are deleted** (the API returns **404**).

**Repository / commit / `49cee20`** — your project's folder, its saved snapshot
id, and the exact version everything ran against.

**pytest** — the project's automated self-checks.

**Repository path** — `/workspaces/opencode-unlimited` (note the **plural**
`workspaces`; a plural/singular slip cost me a debugging detour).

**Snapshot** — copying files and keeping them locally so evidence survives
machine deletion. Done before deleting.

## 1.5 Measuring honestly

**Round** — one attempt: rotate, then act.

**Latency / median** — how long something takes; the median is the middle value,
used because with few samples an average is distorted by one slow run.

**Probe** — asking a website "what address do you see me as?"
(`https://api64.ipify.org`).

**curl / exit code 0** — the tool that does the asking, and the code meaning
"success". A failed probe is recorded as *unknown*, never as "probably fine".

**Artifact / evidence / CSV / JSON / SVG** — the raw files a study produces, and
three file formats (spreadsheet, structured data, picture). Every number below
is computed by script from the evidence files — none typed by hand.

**Chain of custody** — the rule that conclusions trace back to measurements.

## 1.6 The safety checks, and this round's new ones

**(new) Network namespace** — a sealed-off copy of the machine's networking. Your
real machine keeps its own routes while a test VPN lives in its own box.

**(new) veth pair** — a "virtual cable" with one plug in the real machine and one
in the sealed box. It's how the sealed box gets a *real* road to the internet.
**Round 1's VPN Gate failure was that box having no road at all.**

**(new) NAT / MASQUERADE** — the trick that lets the sealed box share the real
machine's single public address. Without it, the box can reach the cable but not
the internet.

**(new) `iptables` / `ip6tables`** — the traffic rulebook (firewall). I added
three rules for the virtual cable and removed them at the end.

**(new) Packet counters** — `iptables` prints live counts like `[118407:627050544]`
on every line. Two identical rulebooks therefore never *look* identical. Round
1's audit would have failed on that noise; this round's audit normalises the
counters away and compares the actual rules.

**(new) Route / default route** — the machine's map of which road traffic takes.
A dead VPN leaves the map pointing at a road that no longer exists, so every
later connection mysteriously times out. **That exact fault wasted 20 minutes
this round** until I made the setup repair the route before every attempt.

**Audit / assertion** — automatic before/after comparisons. **An assertion can be
wrong**, and two of mine were (again — see Part 4).

---

# Part 2 — What actually happened

> **The short version.** I rebuilt the study so that every request goes through
> your product's own bridge, gave VPN Gate a working network cable instead of a
> sealed box with no exit, and gave Psiphon its real credentials. All four
> providers then rotated perfectly — including the two I had declared broken.
> But only **WARP** and **Psiphon** actually restored usable quota; **Tor** and
> **VPN Gate** rotate perfectly and are still refused by opencode's free tier.
> WARP and Psiphon also kept the conversation alive across every rotation; Tor
> and VPN Gate never got that far. All 18 safety checks passed this time,
> including the two I wrote wrongly before. Machine deleted.

## Act 1 — Fixing the thing I broke

Round 1 failed Tor's continuity test with `UnsupportedProxyProtocol`. That was my
error: I handed opencode a SOCKS5 address, which it refuses by design, instead of
routing through your bridge.

This round, nothing talks to a provider directly. Each provider exposes a SOCKS5
door, and then **your own code** is the only way in:

```
python3 ouc.py bridge --listen 127.0.0.1:<port> --socks <provider door>
```

opencode is then pointed at that bridge, and every request is issued by your
product's own commands. The proof came immediately: through the bridge, Tor
worked, and opencode's refusal vanished.

## Act 2 — Psiphon: you were right, I was wrong

Round 1 reported Psiphon as a hard negative: the proxy door opened, but nothing
ever came through. I blamed placeholder credentials and said don't retry.

The real cause was bigger: modern Psiphon needs **four** things to start — the
sponsor identity, the propagation channel, a **remote server list URL**, and the
**public key that verifies that list**. I had supplied placeholders and no
server list, so Psiphon never had a single server to dial. It wasn't broken; it
was blindfolded.

With the real public bootstrap values, Psiphon connected on the first attempt and
then produced **three distinct exit IPs in three rotations** — `68.183.239.137`,
`172.105.246.108`, `172.238.125.192` — and **kept the conversation alive all
three times** (9–11 seconds each).

Psiphon is the second-best backend in this study and the most surprising result
in it.

## Act 3 — VPN Gate: a real cable instead of a sealed room

Round 1 put VPN Gate inside a sealed box and gave it an exit that never opened,
so every server dialled into "no road". This round the box gets a **real cable**
(veth pair) plus **NAT**, so it has an ordinary route to the internet while your
machine's own routing stays untouched.

It worked: four rotations, four genuine Japanese relay exits
(`219.100.37.240`, `.234`, `.243`, `.233`), about 32 seconds each.

Two things then had to be fixed before those numbers were trustworthy:

1. **The leak.** Early rounds measured `4.155.45.96` — *your machine's own
   address* — because the tunnel wasn't up and my proxy was quietly forwarding
   straight out of the box. Those rounds were invalid and I threw them away. The
   setup now refuses to report a rotation unless the box's exit differs from the
   machine's own.
2. **The dead road.** After one tunnel died, the box's route still pointed at the
   dead tunnel, so every later attempt timed out even though the servers were
   healthy. Now the road is repaired before every attempt.

## Act 4 — The result that matters most

Here is the finding I did not expect, and the one I'd act on.

**All four providers rotate perfectly.** Every round changed the IP. Tor: 4 for
4. VPN Gate: 4 for 4. WARP: 4 for 4. Psiphon: 3 for 3. If the study had stopped
there — as round 1 largely did — it would have ranked all four as working.

But rotation is not the point. The point is whether the free tier **lets you
in afterwards**. So every rotated IP got a real request. The product's own
verdicts:

| Provider | Fresh IP | Product's verdict |
|---|---|---|
| WARP | `2a09:bac5:cbbd:1541::21e:e7` | **answered** |
| Psiphon | `172.238.125.192` | **answered** |
| Tor | `185.243.218.231` | *free usage limit reached* |
| VPN Gate | `219.100.37.246` | *free usage limit reached* |

Same minute, same machine, same model, same session. Only the road differed.

**Interpretation, labelled as interpretation:** Tor exits and VPN Gate relays are
*public, published lists*. Millions of people use them, so those addresses are
already exhausted — or deliberately blocked — for this free tier. WARP and
Psiphon hand out addresses that are not on those lists. So the kind of IP matters
as much as whether it's new.

That reverses round 1's headline. Round 1 said "Tor is the best rotator" on the
strength of speed and novelty alone. Tor *is* fast and *is* honest — but as a
backend for your product it is useless, because it cannot get past the wall. I
was measuring the wrong thing.

## Act 5 — Continuity, finally measured properly

One conversation was seeded once (`ses_e26166e36ffewOLk1uBBZfTzOa`) on WARP, with
a token the model had to memorise. Then every rotation on every provider was asked
to return that token **without it being repeated**.

| Provider | Rotations | Token recalled |
|---|---|---|
| WARP | 4 | **4 / 4** |
| Psiphon | 3 | **3 / 3** |
| Tor | 4 | 0 / 4 |
| VPN Gate | 4 | 0 / 4 |

WARP and Psiphon: memory survived every single swap — the product's core promise,
demonstrated end to end, through your own `ouc resume` path. Round 1 could not
show this for anything but WARP.

Tor and VPN Gate: the request never succeeded, so continuity was never reached.
That is a wall result, not a memory failure — there was no answer to check.

## Act 6 — Proving I didn't break the machine

Your machine's own address before and after: `4.155.45.96` → **`4.155.45.96`**.
The config files for name resolution and hostnames: byte-identical. The routing
rules and priorities: identical. The firewall: identical once I removed my own
three virtual-cable rules, and **nothing unexplained was added**.

**18 of 18 checks pass.** Including the two that failed last time — because this
time I wrote them correctly:

- the hop counter must equal **control rotations + the one manual rotation** I ran
  while wiring the harness → `5 = 4 + 1` ✓
- the blacklist must contain **exactly one retired block per hop** → 5 hops,
  5 blocks ✓

I did have to fix two *other* checks of mine this round (Part 4), and I normalise
packet counters — stated openly rather than hidden.

## Act 7 — Cleaning up

Evidence pulled off the machine first, then the machine deleted (API: **404**).
Your two own codespaces remain untouched. Results, CSVs and a chart were then
generated by script from the pulled evidence. Your repo is clean at `49cee20`.

---

# Part 3 — The results

| Provider | Rounds | New IPs | New *buildings* | Rotation speed (median) | Free tier verdict | Continuity |
|---|---:|---:|---:|---:|---|---:|
| WARP | 4 | 4 | 4 | 7.9 s | **usable** | **4/4** |
| Psiphon | 3 | 3 | 3 | 11.1 s | **usable** | **3/3** |
| VPN Gate | 4 | 4 | 4 | 31.8 s | walled | 0/4 |
| Tor | 4 | 4 | 4 | 86.5 s | walled | 0/4 |

Read across: **rotation success is 15/15 and tells you almost nothing.** The
column that matters is "free tier verdict".

## Act 8 — Building Psiphon in, and the bug the tests couldn't see

You said: stop studying Psiphon, make it part of the product. So the ladder is
now WARP first, Psiphon automatically after five failed WARP rotations, back to
WARP after four hours. I built it, the offline suite passed 64 tests with zero
failures — and then it did **not work on a real machine**.

The reason is the best lesson in this project. The program starts Psiphon with a
switch that changes the layout of its output, then waits for a line in the
*other* layout. The tunnel came up perfectly in 12 seconds every time; the
program said "no tunnel within 45 seconds" and fell back to WARP. My fake
Psiphon for the tests printed the layout the code expected, because I wrote the
fake by reading my own code instead of Psiphon's output. **The fake agreed with
the bug**, so 64 green tests proved nothing about this path.

Four more defects came out the same way, all of them invisible offline:

2. It stamped "fallback started" *before* trying Psiphon, so a failed rescue
   still showed as a rescue in progress.
3. A quota wall that arrived while the supervisor was repairing a dead tunnel was
   read on the first pass — the pass that says "everything already in this log is
   old news" — and thrown away forever.
4. Running as a background service, it handed the AI tool a **write-only stdin**.
   The tool reads stdin, got an immediate `EBADF`, and died before speaking. Every
   background rotation reported "could not be confirmed" while the same rotation
   typed at a prompt worked in 8 seconds.
5. The tunnel was given a pipe owned by the command that started it. The command
   exited, the pipe closed, the tunnel died — about 50 seconds after reporting
   success, and the bridge started answering 502.

All five are fixed, each with a test. Then the three live tests:

| Test | Result |
| --- | --- |
| Break WARP, ask five times, watch | escalated to Psiphon **on attempt 5**, exit `138.197.197.180`, not the host's own IP |
| A quota wall, nobody touching anything | detected and rotated in **7.37 s**, verified with a real request |
| Three countries, one conversation | **Germany → Netherlands → Canada**, three distinct `/32` addresses, "remember this token" recalled **3 out of 3**, ~15 s per hop |
| Before/after machine check | **18 / 18** — same IP, same DNS, same firewalls, nothing left running, tested file == pushed commit |

Final state: your repo at `ccc0c7e`, 65 offline tests green, machine deleted
(GitHub: 404), your own two codespaces untouched. Full detail in
`lab3/PHASE3-PLAIN-ENGLISH.md` (narrative) and `lab3/PHASE3-TECHNICAL.md`
(measurements, code, failure tables).

**Still not proven:** a wall that arrives *while Psiphon itself is carrying the
traffic* (proven on WARP, and the Psiphon path proven on demand — not the two
together); a 10-hour soak (longest was ~4 h, 20 hops); and the strict kill-switch
firewall while a Psiphon exit is live (everything ran in proxy mode).

---

## What this means for your project

1. **Psiphon should be a first-class backend.** It rotates quickly, gives clean
   addresses, restores quota, and preserves the conversation. It is the closest
   thing to a drop-in answer for the free-tier wall, and it needs your bridge.
2. **Tor should not be sold as a quota backend.** It rotates fast and honestly,
   but the free tier refuses it. Good for anonymity, wrong tool for this job.
3. **VPN Gate is technically impressive and practically unusable here**: 32 s per
   rotation plus a walled result. Its public relays are simply too well known.
4. **Keep the bridge.** It is the single component that makes any SOCKS5 provider
   usable by opencode. Everything in this round flows through it.
5. **WARP remains the reliable default** — fastest to rotate, always usable, no
   bridge needed.
6. **Speed ranking** (if you care): WARP 7.9 s < Psiphon 11.1 s < VPN Gate
   31.8 s < Tor 86.5 s. Note Tor's number is inflated because its rotation is a
   full restart in my harness, not because Tor is inherently slow.

# Part 4 — What I got wrong, ranked

1. **Round 1: I bypassed your bridge.** Cost: a false "Tor is broken" for
   continuity. Fixed by routing everything through `ouc bridge`.
2. **Round 1: I declared Psiphon dead and told you not to retry it.** Cost: the
   best backend in the study, missed. Cause: missing server-list credentials.
   Your instruction to retry it was correct.
3. **Round 1: I sealed VPN Gate in a box with no exit** and reported the
   resulting failure as a provider limitation.
4. **This round: I measured your machine's own IP as a VPN Gate rotation.** Caught
   because the number looked too familiar. Now impossible: the setup refuses to
   report unless the VPN's exit differs from the machine's.
5. **This round: a dead tunnel poisoned the routing** so every later connection
   timed out. Fixed by repairing the road before each attempt.
6. **This round: two audit assertions were self-contradictory** — one demanded a
   zero firewall delta while the other correctly demanded my three rules be gone.
   Replaced with the right pair.
7. **This round: I bounded retries so tightly** that the per-round record lost the
   product's typed reason for failure. Fixed by adding a diagnosis pass that asks
   the product directly on a fresh IP.

# Part 5 — Limits, stated plainly

- **Small samples.** 3–4 rotations per provider. Medians, not percentiles.
- **One machine, one region, one day.** No day-to-day variance measured.
- **One model** (`opencode/deepseek-v4.1-flash-free`). Round 1 showed models
  differ; flint was walled even on WARP.
- **Tor's rotation time is a harness artifact** (full restart), so don't read it
  as Tor being slow at rotating circuits.
- **The wall explanation is inference.** I measured *that* Tor and VPN Gate are
  refused and WARP and Psiphon are not; "public address lists are exhausted or
  blocked" is my explanation, not a measurement.
- **Untouched by this study:** automatic wall detection triggering rotation with
  no human in the loop, the 10-hour soak test, and tunnel-mode kill-switch.

## Files

- `/home/user/RERUN-STUDY.md` — round 1, kept only as a record of the error.
- `/home/user/rerun2-evidence/` — 8 raw files from this round's machine.
- `/home/user/lab-rebuild/artifacts2/` — `RESULTS.json`,
  `PER_IP_REQUESTS.csv` (every rotated IP and its real request),
  `PROVIDER_SUMMARY.csv`, `ROTATION_VS_VANTAGE.svg`.
- `/home/user/lab-rebuild/v2/` — the harness: `study_v2.py`, `diag_v2.py`,
  `audit_v2.py`, `analyze_v2.py`, `psiphon_start.sh`, `vpngate_ns.sh`.
