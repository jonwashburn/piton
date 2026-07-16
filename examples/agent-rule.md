# Program Memory (agent rule template)

Drop this into your agent harness's always-loaded rules (Cursor
`.cursor/rules/`, `CLAUDE.md`, `AGENTS.md`, or a system prompt) and adapt the
paths. It is the protocol that makes Piton memories stay honest and current.

---

The repo keeps a program registry at `memory/` and a per-program memory at
`<folder>/memory/` (bounded `ORIENTATION.txt`, append-only `ledger.jsonl`,
generated `claims.txt`). Tool: `piton`.

## Read (start of session)

When a task touches a program: grep `memory/claims.txt` (the program tree;
programs nest as a DAG, one project may sit under several parents), then read
that program's `memory/ORIENTATION.txt` and grep its `claims.txt`. The ledger
always wins over prose. If a program has no memory yet, orient from its folder
as before.

## Write (end of any session that changed program state)

Append what changed (one fact, one record), refresh the orientation if the
story changed, then gate it:

```bash
echo '{"kind":"claim","id":"C-x","title":"...","tier":"MEASURED","status":"live","statement":"...","evidence":["path"],"gate":"...","falsifier":"..."}' \
  | piton append <folder>/memory --session <name>
piton close <folder>/memory     # project + sync-head + validate
```

Kinds: claim / event / decision / lesson / note / program. Tiers: THEOREM,
MEASURED, EXTERNAL-MEASURED, DERIVED-UNFORMALIZED, HYPOTHESIS, MODEL, OPEN.
The weakest link sets the tier; external gates (proof-assistant builds, frozen
experiment gates, deterministic test suites) are the only judges of the strong
tiers. Updating an id = append a new record with that id (the CLI chains
`prev`; stdin and `--file` may carry multiple JSONL lines). Commit memory
changes with the work.

## Register new programs (agent-created, threshold-gated)

When work crosses the threshold (its own folder or named campaign AND expected
to persist beyond one session, or the owner names it a project): append a
`program` record to `memory/` (id `P-x`, name, status
active/dormant/closed/absorbed, summary, folder, parents=[P-...];
multi-parent allowed) in that same session. Init `<folder>/memory`
(`piton init <folder>/memory --program NAME`) once it has claims worth
tracking. Do not register one-shot errands.

## Improve the protocol as you use it

When you hit friction (a command that fights you, a missing check, a record
shape that does not fit) or see a better structure, record it in the registry
ledger as a `decision` or `note` in that same session. Small compatible
improvements should be implemented immediately; breaking changes (record
schema, tier/status vocabulary, file layout) are a decision for the owner.
