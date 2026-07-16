# Piton

**Durable, honest program memory for AI agent sessions.**

A piton is the spike a lead climber leaves in the rock so the next climber can
clip in instead of free-climbing the same face. AI agent sessions are one-shot:
each new session starts with no memory of the last. Piton is how a long-running
project remembers itself between sessions.

No server, no database, no embeddings. One stdlib-only Python file, plain text
on disk, git as the only coordination.

Piton also handles a whole portfolio of projects. Each project keeps its own
small, focused memory, while a root registry maps how the projects relate.
Projects can be nested to any depth, and the structure is a graph rather than
a rigid folder tree: one project can belong under several parent programs at
once. This lets an agent start from the full portfolio, follow the relevant
branch into the right project, and load only the context needed for the task.
`piton overview` then checks every registered project at once and shows which
memories are current, stale, missing, active, dormant, closed, or absorbed.

## The problem

Teams running many AI agent sessions against one long-lived project usually end
up with a single "living document" the agents read for orientation and append
to when they finish. That pattern fails in a predictable way:

- **Unbounded growth.** The append-only history swallows the file until it is
  bigger than an agent can read (agent file-read tools typically cap around
  100 KB). The canonical orientation document becomes unreadable by the agents
  it exists for.
- **Stale current-state.** The changelog stays honest, but the "where things
  stand" prose silently drifts, and nothing catches it.
- **Triple writing.** Every event gets written two or three times: headline,
  status section, changelog.
- **No enforcement.** The update protocol is a comment at the top of the file.

## The design

Split the jobs into surfaces with different mutation rules, and make staleness
a machine-checkable error. Per program, a `memory/` directory:

| File | Mutation rule | Job |
|------|---------------|-----|
| `ORIENTATION.txt` | Rewritten in place, hard cap 12 KB | Plain-English one-pager: north star, current chapter, next steps, pointers. Carries `LEDGER_HEAD: <hash>` of the last ledger record. |
| `ledger.jsonl` | Append-only, via the CLI | The record of truth. One flat JSON record per line: claims, events, decisions, lessons, notes. |
| `claims.txt` | Generated, never hand-edited | One greppable line per current claim, plus open decisions and banked lessons. |
| `archive/` | Rotated segments, never rewritten | Full history survives rotation. |

The validator ties it together:

- The orientation page is **hash-pinned to the ledger tip**. If someone appends
  to the ledger without refreshing the story, `piton validate` fails. Stale
  docs stop being a vibe and become a build error.
- Claims carry an **epistemic tier** (`THEOREM`, `MEASURED`,
  `EXTERNAL-MEASURED`, `DERIVED-UNFORMALIZED`, `HYPOTHESIS`, `MODEL`, `OPEN`)
  and a status (`live`, `closed_positive`, `closed_negative`, `superseded`,
  `parked`). A closed claim with **no evidence and no gate is refused**. The
  validator cannot check truth (external gates such as proof-assistant builds
  and frozen pre-registered experiments are the only judges of the strong
  tiers), but it can refuse a closure without a receipt.
- A claim's current state is the **tip of its per-id hash chain**. Updating a
  claim means appending a new record with the same id; the CLI fills `prev`
  with the hash of the record it replaces. Two concurrent sessions racing on
  the same claim produce a **detectable fork**, resolved with one reconciling
  append. Appends merge cleanly under git; no locks, no server.

## Quickstart

```bash
pip install .          # installs the `piton` CLI (stdlib only)

piton init myproject/memory --program "My Project"

echo '{"kind":"claim","id":"C-perf","title":"Cache halves p95 latency",
  "tier":"MEASURED","status":"live",
  "statement":"Benchmark shows p95 340ms -> 165ms with the LRU cache.",
  "evidence":["bench/results_2026_07_16.json"],
  "gate":"bench/run.sh under frozen params",
  "falsifier":"regression above 200ms on the frozen benchmark"}' \
  | piton append myproject/memory --session sprint-42

# ... edit ORIENTATION.txt's CURRENT CHAPTER when the story changes ...

piton close myproject/memory     # project + sync-head + validate, one shot
```

Read path for a fresh agent session:

1. Read `ORIENTATION.txt` (small by construction).
2. Grep `claims.txt` for current claims, open decisions, lessons.
3. For any claim's history: `grep '"id":"C-perf"' myproject/memory/ledger.jsonl`.
4. Never trust prose over the ledger. The ledger wins.

## Record kinds

```jsonc
// claim: a proposition the program cares about
{"kind":"claim","id":"C-slug","title":"...","tier":"MEASURED","status":"live",
 "statement":"...","evidence":["path"],"gate":"what judges this","falsifier":"..."}

// decision: an open fork someone must resolve   // lesson: do-not-relearn
{"kind":"decision","id":"D-slug","status":"open","text":"..."}
{"kind":"lesson","id":"L-1","text":"..."}

// event: something that happened               // note: anything else
{"kind":"event","summary":"...","receipts":["path"]}
{"kind":"note","text":"..."}
```

The CLI stamps `ts`, `session`, `prev`, and the record hash `h` itself.
Stdin and `--file` accept multiple JSONL lines for batch appends.

## Multi-project registries

A root registry (default `./memory`) is a normal program memory whose ledger
also carries `program` records:

```json
{"kind":"program","id":"P-solver","name":"Constraint Solver","status":"active",
 "summary":"...","folder":"solver","memory":"solver/memory",
 "parents":["P-engine","P-infra"]}
```

`parents` is a DAG, not a tree: one project may nest under several parents,
and the generated `claims.txt` renders the portfolio as an indented forest
with multi-parent nodes marked. `piton overview` prints a freshness table for
every registered memory:

```
id               status   memory                fresh   claims  last_write
P-engine         active   engine/memory         FRESH   12/16   2026-07-16T16:26:55Z
P-solver         active   solver/memory         FRESH   4/15    2026-07-16T16:28:45Z
```

## Commands

```
piton init <dir> --program NAME   create the memory skeleton
piton append <dir> [--json S | --file F | stdin]   append record(s)
piton close <dir>                 project + sync-head + validate (end of session)
piton validate <dir>              all checks; exit 1 on any error
piton status <dir>                one-screen freshness + counts
piton overview                    freshness table across the whole registry
piton project | head | sync-head | rotate <dir>
```

## Agent integration

Put the protocol in your agent harness's always-loaded rules (Cursor rules,
CLAUDE.md, AGENTS.md, system prompt). A template is in
[`examples/agent-rule.md`](examples/agent-rule.md). The core of it:

- **Read** at session start: registry tree, then the program's orientation,
  then grep its claims.
- **Write** at session end: append what changed (one fact, one record),
  refresh the current chapter if the story changed, run `piton close`, commit
  the memory with the work.
- **Register** new programs when work crosses a threshold (its own folder or
  named campaign, expected to persist beyond one session), never for one-shot
  errands.
- **Never inflate a tier.** The weakest link sets it.

## What Piton is not

Not semantic memory, not vector search, not chat-history recall, and not a
knowledge base. It is the minimal honest handoff between sessions: what we
claim, how strongly, on what evidence, what is open, and what we already
learned the hard way.

## Example

A worked example lives in [`examples/demo`](examples/demo): a small fictional
research program with a seeded ledger, including a claim that was later
downgraded (the tier history is visible in the ledger chain).

## License

MIT
