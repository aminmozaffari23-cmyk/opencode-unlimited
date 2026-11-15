# lab3 — live verification harness

| file | what it is |
| --- | --- |
| `live_driver.py` | the phases: `pdiag gap1 gap2 c1..c6 t4`, run as root on a disposable host |
| `audit.py` | 18 core before/after checks + 6 chaos checks, read from pulled evidence only |
| `status_doc.py` | generates `PHASE4-STATUS.md` from an evidence directory — no hand-typed numbers |
| `warp_revert_guard.sh` | dead-man's switch for the TUN window |
| `summarise.py` | evidence directory → Markdown table for the job summary |
| `evidence/<run-id>/` | the JSONs each live run produced |

Reproduce:

```bash
gh workflow run live-verify.yml -f "phases=gap1 c1 c2 c3 c4 c5 c6" -f "tunnel_window=true"
gh run download <run-id> -D /tmp/ev
OUC_EVIDENCE=/tmp/ev/live-evidence-<run-id> python3 tests/lab3/audit.py
python3 tests/lab3/status_doc.py /tmp/ev/live-evidence-<run-id> > PHASE4-STATUS.md
```
