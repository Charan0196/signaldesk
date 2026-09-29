# SignalDesk

**Churn early-warning and save-play memory for B2B customer success teams — built on [Hindsight](https://hindsight.vectorize.io).**

You paste a raw customer signal. SignalDesk answers two questions a CSM cannot answer from memory:
**are they leaving, why, and what do I do today?**

```bash
export HINDSIGHT_API_KEY=hsk_...        # https://ui.hindsight.vectorize.io
signaldesk serve                        # → http://127.0.0.1:8420
```

---
### 🎥 Demo
<img width="1440" height="900" alt="Screenshot 2026-09-29 at 9 35 17 PM" src="https://github.com/user-attachments/assets/1e6d1cbe-64c5-4bab-815a-07c35a5fd264" />
#### Demo video:
https://github.com/user-attachments/assets/YOUR-VIDEO-ASSET-ID


## The problem

A CSM carrying 40 accounts cannot remember why Account A is churning. The signals are scattered
across call transcripts, emails, support tickets and QBR decks. By the time the champion goes
quiet, the real reason is six weeks old and buried in a transcript nobody will reread.

Every generic AI assistant fails here in the same way: it has no history, so it recommends
generic plays. It will happily tell you to "offer a discount" — which is exactly the move that
bought us three months on Cobalt Ridge and still lost the account.

## What SignalDesk does instead

The save play is **not hardcoded**. It is reasoned from the org's own history:

- Every raw signal is written to memory (`retain`), tagged by account and channel.
- Facts consolidate into **observations** — evolving beliefs like *"risk is rising on this account"*
  rather than a pile of disconnected notes.
- A **mental model** keeps a self-refreshing brief per account.
- `reflect` then diagnoses the account and prescribes a play, **citing the comparable accounts
  that prove it works** — and the ones where it didn't.

Interaction 1 is generic advice. By interaction 50, the agent knows that exec-sponsor alignment
rescued Halcyon Logistics while discounting a non-economic-buyer champion lost us Cobalt Ridge.
**That knowledge is the product, and it is the memory bank.**

---

## Quickstart

```bash
git clone https://github.com/Charan0196/signaldesk.git
cd signaldesk
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install "uvicorn[standard]" fastapi      # only needed for `serve`

cp .env.example .env                          # add your Hindsight key
pytest -q                                     # 73 tests, no network needed

signaldesk demo --reset                       # the full story, end to end
signaldesk serve                              # the dashboard
```

| Command | What it does |
|---|---|
| `signaldesk serve` | Web dashboard — the same agent in a browser |
| `signaldesk assess "Acme"` | Risk verdict + cited evidence + save plan |
| `signaldesk assess "Acme" --md` | Same, as markdown for a CRM note |
| `signaldesk brief "Acme"` | The self-refreshing account brief (mental model) |
| `signaldesk board` | Portfolio triage, highest risk first |
| `signaldesk ingest -a "Acme" -f call.txt` | Store one raw signal (file, `-f -` for stdin, or `--text`) |
| `signaldesk seed --reset` | Load the demo book of business |
| `signaldesk demo` | Seed, analyze, and run the memory ablation |
| `signaldesk setup` | Create/configure the bank, mission and guardrail directives |
| `signaldesk accounts` / `stats` | What's in memory |

**Use your own data:** any text works. `signaldesk ingest` accepts call transcripts, emails, or
ticket dumps — the extraction is Hindsight's job, not a regex.

---

## How memory is used (the part judges ask about)

Four memory operations. **Not `recall`** — retrieval is `reflect`'s job, so the agent *reasons
over* memory instead of pattern-matching against it.

| Op | Where | Why it matters |
|---|---|---|
| `retain` | `memory.py: ingest` | Every raw signal becomes tagged, attributable memory units |
| `reflect` | `memory.py: assess / prescribe` | Diagnosis + prescription under a JSON schema, with citations |
| mental models | `memory.py: refresh_brief` | A per-account brief that re-synthesizes as new signals land |
| directives | `memory.py: _install_directives` | Guardrails that bind *every* reflect |

Plus **observation consolidation**, enabled on the bank: facts merge into deduplicated,
evidence-tracked beliefs that get *updated* — not overwritten — when new evidence contradicts them.

Three guardrails ship installed on the bank:

| Directive | Effect |
|---|---|
| `no-invented-evidence` | Every claim must trace to memory, else say "not in memory" |
| `prefer-precedent-over-playbook` | Name the comparable accounts explicitly, not generic advice |
| `date-every-signal` | Flag stale accounts rather than assume they're healthy |

### The learning curve

`signaldesk demo` proves memory is load-bearing with an **ablation**, not a claim:

1. Loads 27 historical signals across 8 accounts — a real book of business with recorded
   **outcomes** (3 saved, 3 lost, with post-mortems).
2. Assesses the live target account from full memory.
3. Assesses **the same account again** against a control bank containing only its own signals.
4. Diffs them.

Same model, same prompts — the only variable is memory. Live on Cobalt Ridge Energy:

```
with memory   : critical 100, confidence medium, precedent 2
                 - Halcyon Logistics: saved via exec sponsor alignment + dated commitments
                 - Cobalt Ridge Energy (failure): saved by discounting the champion only
without memory: critical 100, confidence low,    precedent 0
```

Neither answer is wrong. The one with precedent is *defensible in a room where the CFO is sitting
there* — which is the entire job.

---

## Architecture

```
signaldesk/
├── config.py     Settings from env — no hardcoded secrets
├── schemas.py    Pydantic contracts + coercion for real-world model drift
├── memory.py     The only module that talks to Hindsight (retain/reflect/models/directives)
├── agent.py      Pipeline: ingest → brief → diagnose → prescribe
├── llm.py        Optional OpenRouter layer for fast side-tasks
├── web.py        FastAPI, 10 endpoints, dedicated IO thread
├── render.py     Terminal + markdown output, citations always visible
├── seed.py       The demo book of business, with outcomes
├── demo.py       The learning-curve narrative + memory ablation
├── cli.py        Typer CLI
└── static/       Dashboard (HTML/CSS/JS, no build step)
```

### Design decisions worth knowing

**`reflect` is the reasoning engine, not an external LLM.** Every judgment comes from Hindsight
reasoning over the bank. No second LLM key, no prompt plumbing — and it makes memory the star
rather than a retrieval step bolted onto a model.

**A bank per org, not per account.** Accounts are separated by tags (`acct:`, `chan:`) and by
`reflect`'s tag filtering. Keeping one bank is what lets the agent reason *across* accounts —
which is where the save plays come from. Isolation without silos.

**Structured output is validated, then coerced.** Models drift: evidence arrives as strings
instead of objects, scores as `"82"` or `0.82`, tiers as `"SEVERE"`, owners packed into the
action text, `silent_days` as `"300 days"`. `schemas.py` normalizes these rather than failing
the run — a citation that came back as a sentence is still worth showing a CSM. Covered by
`tests/test_schemas.py`.

**Missing data stays visible.** When the model doesn't supply an owner or a success signal, the
plan shows "unassigned" rather than a confident-looking default. A plan reading "CSM, day 7" for
every step looks complete but tells the rep nothing.

**Failures are visible.** If an account can't be analyzed, the board shows `n/a` rather than
dropping it. A silent gap in a risk board is worse than an error — "no risk found" and "couldn't
reach memory" must never look the same.

**The LLM layer is optional by design.** OpenRouter free models are frequently rate-limited, so
`llm.py` walks a fallback list, trips a circuit breaker after repeated 429s, and degrades to a
rule-based classifier. It accelerates ingest; it never gates it.

---

## Testing

```bash
pytest -q                                  # 73 tests, offline
python tests/test_frontend.py              # optional: needs the server running
```

They cover the coercion layer with the exact payload shapes that broke in real runs against the
live API — the `"300 days"` string, the string-typed evidence array, the `{"items": [...]}` list
nesting that silently renders empty, and the client's async/sync event-loop conflict. End-to-end
behavior is verified by `signaldesk demo`.

---

## Limitations

- Reflect quality is only as good as the signals you ingest; thin history means thin verdicts
  (the agent is told to say so rather than guess).
- Scores vary between runs — `reflect` is non-deterministic. The *direction* is stable: accounts
  we lost rank critical, accounts we saved rank low.
- The portfolio board takes ~2 min for 6 accounts (one `reflect` each). That's the API, not the code.
- Risk scores are a triage aid, not a forecast. The save plan is the deliverable.
- Ingest falls back to rule-based classification when the OpenRouter free tier is saturated; the
  LLM path engages automatically once a slot frees.
- Requires Hindsight Cloud credits (promo `MEMHACK99` gives $50) or a self-hosted instance.

---

*Hackathon submission.*
