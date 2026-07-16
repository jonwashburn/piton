#!/usr/bin/env python3
"""piton -- durable, honest program memory for AI agent sessions.

A piton is the spike a lead climber leaves in the rock so the next climber
can clip in instead of free-climbing the same face. AI agent sessions are
one-shot: each new session starts with no memory of the last. Piton is how
a long-running program remembers itself between sessions, without the
failure modes of a single ever-growing "living document" (unbounded growth
past agent read limits, current-state prose silently drifting stale, every
event written three times).

Piton splits the three jobs of a living document into three surfaces with
different mutation rules, and makes staleness a machine-checkable error:

  ORIENTATION.txt   bounded (default <= 12 KB), rewritten in place. Plain
                    English one-pager: north star, current chapter, next
                    steps, pointers. Must carry a "LEDGER_HEAD: <hash>" line
                    equal to the hash of the last ledger record, so a stale
                    orientation FAILS VALIDATION instead of quietly lying.
  ledger.jsonl      append-only record of truth. One flat JSON record per
                    line. Kinds: claim / event / decision / lesson / note /
                    program. A claim's current state is the tip of its
                    per-id chain: each update carries prev = hash of the
                    record it replaces, so concurrent sessions that race on
                    the same claim produce a detectable fork, not a silent
                    overwrite.
  claims.txt        GENERATED projection: one greppable line per live claim,
                    plus open decisions and banked lessons. Never hand-edit.
  archive/          rotated ledger segments. Full history, never rewritten.

Epistemic honesty is enforced mechanically where possible: claims carry a
tier (THEOREM / MEASURED / EXTERNAL-MEASURED / DERIVED-UNFORMALIZED /
HYPOTHESIS / MODEL / OPEN), the weakest link sets the tier, and the
validator refuses closed claims that carry neither evidence nor a gate.
External truth gates (proof-assistant builds, frozen pre-registered
experiment checks) are the only judges of the strong tiers; a validator
cannot check truth, so the protocol is: never inflate.

A root registry (a normal memory directory, default ./memory) may also carry
"program" records (id P-*, name, status active/dormant/closed/absorbed,
summary, folder, memory, parents=[P-...]). Parents form a DAG: one program
may nest under several parents. The registry's claims.txt renders the
portfolio as an indented tree, and `piton overview` reports every program
memory's freshness in one table.

Commands:
  init <dir> --program NAME     create the memory/ skeleton
  append <dir> [--json S | --file F | stdin]   append record(s) (auto ts/h/
                                prev), then reproject claims.txt; --file or
                                stdin may carry MULTIPLE jsonl lines (batch)
  project <dir>                 regenerate claims.txt from the ledger
  validate <dir>                all checks; exit 1 on any error
  close <dir>                   project + sync-head + validate in one step
                                (the end-of-session command)
  head <dir>                    print current ledger head hash
  sync-head <dir>               rewrite the LEDGER_HEAD line in ORIENTATION.txt
  status <dir>                  one-screen summary (head freshness + counts)
  overview                      walk the root registry and report every
                                program memory's freshness and counts
  rotate <dir>                  move non-tip history to archive/ (keeps the tip
                                record of every id + the last 20 events)

Coordination is git-only: appends merge cleanly; forks are caught by
validate after merge and resolved with one reconciling append.

Stdlib only. No server, no database, no embeddings.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys

__version__ = "0.1.0"

TIERS = {
    "THEOREM", "MEASURED", "EXTERNAL-MEASURED", "DERIVED-UNFORMALIZED",
    "HYPOTHESIS", "MODEL", "OPEN",
}
STATUSES = {"live", "closed_positive", "closed_negative", "superseded", "parked"}
DECISION_STATUSES = {"open", "decided"}
PROGRAM_STATUSES = {"active", "dormant", "closed", "absorbed"}
KINDS = {"claim", "event", "decision", "lesson", "note", "program"}
ID_PREFIX = {"claim": "C-", "event": "E-", "decision": "D-", "lesson": "L-",
             "note": "N-", "program": "P-"}
REQUIRED = {
    "claim": ["id", "title", "tier", "status", "statement"],
    "event": ["id", "summary"],
    "decision": ["id", "status", "text"],
    "lesson": ["id", "text"],
    "note": ["text"],
    # program records live in the root registry. Optional fields: folder
    # (project dir), memory (its memory/ dir), parents (list of program
    # ids; DAG, multi-parent allowed).
    "program": ["id", "name", "status", "summary"],
}
REGISTRY_DIR = "memory"
ORIENTATION_BUDGET = 12 * 1024
ROTATE_THRESHOLD = 200 * 1024
ROTATE_KEEP_EVENTS = 20
GENERATED_BANNER = "# GENERATED FILE - do not edit. Regenerate: piton project <dir>"


def rec_hash(rec):
    body = {k: v for k, v in rec.items() if k != "h"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def ledger_path(d):
    return os.path.join(d, "ledger.jsonl")


def read_ledger(d):
    path = ledger_path(d)
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            out.append((n, json.loads(line)))
    return out


def tips(records):
    """Last record per id, in first-seen order. Records without id excluded."""
    tip = {}
    order = []
    for _, rec in records:
        rid = rec.get("id")
        if rid is None:
            continue
        if rid not in tip:
            order.append(rid)
        tip[rid] = rec
    return [(rid, tip[rid]) for rid in order]


def head_hash(records):
    return records[-1][1]["h"] if records else "EMPTY"


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- projection

def render_program_tree(records):
    """Render the program DAG as an indented forest (multi-parent nodes appear
    under each parent, marked as such)."""
    prog = {rid: t for rid, t in tips(records) if t["kind"] == "program"}
    children = {}
    roots = []
    for rid, t in prog.items():
        parents = t.get("parents") or []
        parents = [p for p in parents if p in prog]
        if not parents:
            roots.append(rid)
        for p in parents:
            children.setdefault(p, []).append(rid)
    lines = []

    def emit(rid, depth, seen):
        t = prog[rid]
        multi = len([p for p in (t.get("parents") or []) if p in prog]) > 1
        mark = " (also under other parents)" if multi and depth > 0 else ""
        info = f"{t['status']}"
        if t.get("folder"):
            info += f" | {t['folder']}"
        if t.get("memory"):
            info += f" | memory={t['memory']}"
        lines.append(f"{'  ' * depth}{rid} [{info}] {t['name']}: {t['summary']}{mark}")
        if rid in seen:
            lines.append(f"{'  ' * (depth + 1)}(cycle guard: already expanded)")
            return
        for c in sorted(children.get(rid, [])):
            emit(c, depth + 1, seen | {rid})

    for r in sorted(roots):
        emit(r, 0, frozenset())
    return lines


def render_claims(records):
    lines = [GENERATED_BANNER, ""]
    prog_lines = render_program_tree(records)
    if prog_lines:
        lines.append("# PROGRAMS (registry DAG; indentation = nesting, multi-parent allowed)")
        lines.extend(prog_lines)
        lines.append("")
    claim_tips = [(r, t) for r, t in tips(records) if t["kind"] == "claim"]
    lines.append("# CLAIMS (tip per id; full history in ledger.jsonl, grep the id)")
    for rid, t in sorted(claim_tips):
        lines.append(
            f"{rid} | {t['status']} | {t['tier']} | {t['title']}"
            f" | gate={t.get('gate', '-') or '-'}"
            f" | ev={len(t.get('evidence', []))} | h={t['h']}"
        )
    lines.append("")
    lines.append("# OPEN DECISIONS")
    for rid, t in sorted(t2 for t2 in tips(records) if t2[1]["kind"] == "decision"):
        if t["status"] == "open":
            lines.append(f"{rid} | open | {t['text']}")
    lines.append("")
    lines.append("# LESSONS (do not relearn)")
    for rid, t in sorted(t2 for t2 in tips(records) if t2[1]["kind"] == "lesson"):
        lines.append(f"{rid} | {t['text']}")
    lines.append("")
    return "\n".join(lines) + "\n"


def cmd_project(d, quiet=False):
    records = read_ledger(d)
    out = os.path.join(d, "claims.txt")
    with open(out, "w") as f:
        f.write(render_claims(records))
    if not quiet:
        print(f"wrote {out}")


# ------------------------------------------------------------------ validate

def validate(d, repo_root):
    errors, warnings = [], []
    records = read_ledger(d)
    tip = {}
    for n, rec in records:
        kind = rec.get("kind")
        if kind not in KINDS:
            errors.append(f"line {n}: bad kind {kind!r}")
            continue
        for field in REQUIRED[kind] + ["ts", "session", "h"]:
            if field not in rec:
                errors.append(f"line {n}: {kind} missing field {field!r}")
        if rec.get("h") and rec_hash(rec) != rec["h"]:
            errors.append(f"line {n}: hash mismatch (got {rec['h']}, want {rec_hash(rec)})")
        rid = rec.get("id")
        if rid is not None and not rid.startswith(ID_PREFIX[kind]):
            errors.append(f"line {n}: id {rid!r} lacks prefix {ID_PREFIX[kind]!r}")
        if kind in ("claim", "program"):
            prev = rec.get("prev")
            if rid in tip:
                if prev != tip[rid]["h"]:
                    errors.append(
                        f"line {n}: {kind} {rid} fork/gap: prev={prev!r} but tip is "
                        f"{tip[rid]['h']} (concurrent sessions? merge and re-chain)")
            elif prev:
                if os.path.isdir(os.path.join(d, "archive")) and os.listdir(os.path.join(d, "archive")):
                    warnings.append(f"line {n}: {kind} {rid} prev={prev} not in live ledger (assumed archived)")
                else:
                    errors.append(f"line {n}: {kind} {rid} has prev={prev} but no prior record and no archive")
        if kind == "claim":
            if rec.get("tier") not in TIERS:
                errors.append(f"line {n}: bad tier {rec.get('tier')!r}")
            if rec.get("status") not in STATUSES:
                errors.append(f"line {n}: bad status {rec.get('status')!r}")
            if rec.get("status") in {"closed_positive", "closed_negative", "superseded"}:
                if not rec.get("evidence") and not rec.get("gate"):
                    errors.append(f"line {n}: claim {rid} is {rec['status']} with no evidence and no gate")
            for ev in rec.get("evidence", []):
                if not os.path.exists(os.path.join(repo_root, ev)):
                    warnings.append(f"line {n}: claim {rid} evidence path missing: {ev}")
        if kind == "decision" and rec.get("status") not in DECISION_STATUSES:
            errors.append(f"line {n}: bad decision status {rec.get('status')!r}")
        if kind == "program":
            if rec.get("status") not in PROGRAM_STATUSES:
                errors.append(f"line {n}: bad program status {rec.get('status')!r}")
            for field in ("folder", "memory"):
                if rec.get(field) and not os.path.exists(os.path.join(repo_root, rec[field])):
                    warnings.append(f"line {n}: program {rid} {field} path missing: {rec[field]}")
        if rid is not None:
            tip[rid] = rec

    # program parent links must resolve within the registry (checked at tips)
    prog_ids = {r for r, t in tip.items() if t["kind"] == "program"}
    for rid in sorted(prog_ids):
        for p in tip[rid].get("parents") or []:
            if p not in prog_ids:
                errors.append(f"program {rid}: unknown parent {p!r}")

    orientation = os.path.join(d, "ORIENTATION.txt")
    if not os.path.exists(orientation):
        errors.append("ORIENTATION.txt missing")
    else:
        size = os.path.getsize(orientation)
        if size > ORIENTATION_BUDGET:
            errors.append(f"ORIENTATION.txt is {size} bytes (> {ORIENTATION_BUDGET} budget); tighten it")
        text = open(orientation).read()
        head_lines = [l for l in text.splitlines() if l.startswith("LEDGER_HEAD:")]
        if not head_lines:
            errors.append("ORIENTATION.txt has no LEDGER_HEAD: line")
        else:
            declared = head_lines[0].split(":", 1)[1].strip()
            actual = head_hash(records)
            if declared != actual:
                errors.append(
                    f"ORIENTATION.txt is STALE: LEDGER_HEAD {declared} != ledger head {actual}. "
                    "Review the new ledger records, refresh the orientation, then run sync-head.")

    claims_file = os.path.join(d, "claims.txt")
    if not os.path.exists(claims_file):
        errors.append("claims.txt missing (run project)")
    elif open(claims_file).read() != render_claims(records):
        errors.append("claims.txt does not match the ledger (run project)")

    lsize = os.path.getsize(ledger_path(d)) if os.path.exists(ledger_path(d)) else 0
    if lsize > ROTATE_THRESHOLD:
        warnings.append(f"ledger.jsonl is {lsize} bytes (> {ROTATE_THRESHOLD}); consider rotate")

    return errors, warnings


def cmd_validate(d, repo_root):
    errors, warnings = validate(d, repo_root)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        print(f"FAIL: {len(errors)} error(s)")
        return 1
    print(f"OK ({len(warnings)} warning(s))")
    return 0


# -------------------------------------------------------------------- append

def cmd_append(d, raw, session):
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        sys.exit("append: no records supplied")
    last_hash = None
    for line in lines:
        rec = json.loads(line)
        if rec.get("kind") not in KINDS:
            sys.exit(f"append: bad kind {rec.get('kind')!r}")
        rec.setdefault("ts", now_iso())
        rec.setdefault("session", session or os.environ.get("PITON_SESSION", "unknown"))
        records = read_ledger(d)
        if rec["kind"] == "event" and "id" not in rec:
            stamp = rec["ts"][:10].replace("-", "")
            n = sum(1 for _, r in records if r.get("id", "").startswith(f"E-{stamp}")) + 1
            rec["id"] = f"E-{stamp}-{n}"
        if rec["kind"] in ("claim", "program"):
            cur = {rid: t for rid, t in tips(records)}
            if rec["id"] in cur:
                rec["prev"] = cur[rec["id"]]["h"]
            else:
                rec.setdefault("prev", None)
        rec["h"] = rec_hash(rec)
        with open(ledger_path(d), "a") as f:
            f.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
        last_hash = rec["h"]
    cmd_project(d, quiet=True)
    print(last_hash)


# ------------------------------------------------------------------- init &c

ORIENTATION_TEMPLATE = """PROGRAM: {program}
LEDGER_HEAD: EMPTY

NORTH STAR
  (one paragraph: what this program is trying to achieve, and the honest bar)

CURRENT CHAPTER
  (3-8 plain sentences: where things stand and what is next; rewrite freely,
   this section is BOUNDED and must stay current -- validate fails if the
   LEDGER_HEAD above does not match the ledger tip)

NEXT STEPS
  1. ...

POINTERS
  claims.txt        current claims/decisions/lessons (GENERATED; grep this)
  ledger.jsonl      full append-only record; grep an id for its history
  (list the 3-6 paths that matter most for this program)

PROTOCOL (enforced by `piton validate`)
  read: this file, then grep claims.txt; never trust prose over the ledger.
  write: append records (`piton append`), refresh CURRENT CHAPTER, then run
  `piton close`. Tiers obey external truth gates; the weakest link sets the
  tier; never inflate.
"""


def cmd_init(d, program):
    os.makedirs(os.path.join(d, "archive"), exist_ok=True)
    o = os.path.join(d, "ORIENTATION.txt")
    if os.path.exists(o):
        sys.exit(f"init: {o} already exists")
    open(o, "w").write(ORIENTATION_TEMPLATE.format(program=program))
    open(ledger_path(d), "a").close()
    cmd_project(d, quiet=True)
    print(f"initialized {d}")


def cmd_sync_head(d):
    records = read_ledger(d)
    actual = head_hash(records)
    o = os.path.join(d, "ORIENTATION.txt")
    lines = open(o).read().splitlines()
    out, done = [], False
    for l in lines:
        if l.startswith("LEDGER_HEAD:"):
            out.append(f"LEDGER_HEAD: {actual}")
            done = True
        else:
            out.append(l)
    if not done:
        out.insert(1, f"LEDGER_HEAD: {actual}")
    open(o, "w").write("\n".join(out) + "\n")
    print(actual)


def cmd_head(d):
    print(head_hash(read_ledger(d)))


def cmd_status(d):
    records = read_ledger(d)
    actual = head_hash(records)
    o = os.path.join(d, "ORIENTATION.txt")
    declared = "?"
    if os.path.exists(o):
        for l in open(o):
            if l.startswith("LEDGER_HEAD:"):
                declared = l.split(":", 1)[1].strip()
                break
    fresh = "FRESH" if declared == actual else f"STALE (orientation {declared})"
    t = tips(records)
    claims = [x for _, x in t if x["kind"] == "claim"]
    by_status = {}
    for c in claims:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
    open_dec = sum(1 for _, x in t if x["kind"] == "decision" and x["status"] == "open")
    print(f"head={actual} orientation={fresh}")
    print(f"records={len(records)} claims={len(claims)} {by_status} open_decisions={open_dec}")


def cmd_close(d, repo_root):
    """End-of-session one-shot: project, sync-head, validate."""
    cmd_project(d, quiet=True)
    cmd_sync_head(d)
    return cmd_validate(d, repo_root)


def cmd_overview(repo_root):
    reg = os.path.join(repo_root, REGISTRY_DIR)
    if not os.path.exists(ledger_path(reg)):
        sys.exit(f"overview: no registry ledger at {reg}")
    records = read_ledger(reg)
    progs = [(rid, t) for rid, t in tips(records) if t["kind"] == "program"]
    print(f"{'id':<16} {'status':<8} {'memory':<34} {'fresh':<28} claims  last_write")
    for rid, t in sorted(progs, key=lambda x: (x[1]["status"] != "active", x[0])):
        mem = t.get("memory")
        if not mem:
            print(f"{rid:<16} {t['status']:<8} {'-':<34} {'no memory yet':<28} -       -")
            continue
        md = os.path.join(repo_root, mem)
        if not os.path.exists(ledger_path(md)):
            print(f"{rid:<16} {t['status']:<8} {mem:<34} {'MISSING LEDGER':<28} -       -")
            continue
        recs = read_ledger(md)
        actual = head_hash(recs)
        declared = "?"
        o = os.path.join(md, "ORIENTATION.txt")
        if os.path.exists(o):
            for l in open(o):
                if l.startswith("LEDGER_HEAD:"):
                    declared = l.split(":", 1)[1].strip()
                    break
        fresh = "FRESH" if declared == actual else f"STALE ({declared})"
        claims = [x for _, x in tips(recs) if x["kind"] == "claim"]
        live = sum(1 for c in claims if c["status"] == "live")
        last_ts = recs[-1][1].get("ts", "?") if recs else "?"
        print(f"{rid:<16} {t['status']:<8} {mem:<34} {fresh:<28} {live}/{len(claims):<5} {last_ts}")


def cmd_rotate(d):
    records = read_ledger(d)
    keep_hashes = {t["h"] for _, t in tips(records)}
    events = [r for _, r in records if r["kind"] == "event"]
    keep_hashes |= {r["h"] for r in events[-ROTATE_KEEP_EVENTS:]}
    live, archived = [], []
    for _, rec in records:
        (live if rec["h"] in keep_hashes else archived).append(rec)
    if not archived:
        print("nothing to rotate")
        return
    os.makedirs(os.path.join(d, "archive"), exist_ok=True)
    stamp = _dt.date.today().strftime("%Y%m%d")
    n = 1
    while os.path.exists(os.path.join(d, "archive", f"ledger-{stamp}-{n}.jsonl")):
        n += 1
    arch = os.path.join(d, "archive", f"ledger-{stamp}-{n}.jsonl")
    with open(arch, "w") as f:
        for rec in archived:
            f.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
    with open(ledger_path(d), "w") as f:
        for rec in live:
            f.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
    cmd_project(d, quiet=True)
    print(f"archived {len(archived)} records -> {arch}; live ledger now {len(live)} records")
    print("NOTE: run close (head may have changed if the tail rotated)")


def main():
    p = argparse.ArgumentParser(
        prog="piton",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"piton {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("project", "validate", "close", "head", "sync-head", "status", "rotate"):
        s = sub.add_parser(name)
        s.add_argument("dir")
    sub.add_parser("overview")
    s = sub.add_parser("init")
    s.add_argument("dir")
    s.add_argument("--program", required=True)
    s = sub.add_parser("append")
    s.add_argument("dir")
    s.add_argument("stdin_marker", nargs="?", choices=["-"],
                   help="optional '-' to read from stdin (default when no --json/--file)")
    s.add_argument("--json", dest="json_str")
    s.add_argument("--file")
    s.add_argument("--session")
    args = p.parse_args()

    d = getattr(args, "dir", None)
    repo_root = os.getcwd()
    if args.cmd == "init":
        cmd_init(d, args.program)
    elif args.cmd == "append":
        if args.json_str:
            raw = args.json_str
        elif args.file:
            raw = open(args.file).read()
        else:
            raw = sys.stdin.read()
        cmd_append(d, raw, args.session)
    elif args.cmd == "project":
        cmd_project(d)
    elif args.cmd == "validate":
        sys.exit(cmd_validate(d, repo_root))
    elif args.cmd == "close":
        sys.exit(cmd_close(d, repo_root))
    elif args.cmd == "overview":
        cmd_overview(repo_root)
    elif args.cmd == "head":
        cmd_head(d)
    elif args.cmd == "sync-head":
        cmd_sync_head(d)
    elif args.cmd == "status":
        cmd_status(d)
    elif args.cmd == "rotate":
        cmd_rotate(d)


if __name__ == "__main__":
    main()
