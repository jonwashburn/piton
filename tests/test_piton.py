import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PITON = os.path.join(ROOT, "piton.py")


def run(args, cwd, stdin=None, expect=0):
    p = subprocess.run([sys.executable, PITON] + args, cwd=cwd, input=stdin,
                       capture_output=True, text=True)
    if expect is not None:
        assert p.returncode == expect, f"{args}: rc={p.returncode}\n{p.stdout}\n{p.stderr}"
    return p


class TestPiton(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="piton_test_")
        self.mem = os.path.join(self.root, "proj", "memory")
        os.makedirs(os.path.dirname(self.mem))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_roundtrip(self):
        run(["init", self.mem, "--program", "Demo"], self.root)
        rec = {"kind": "claim", "id": "C-a", "title": "t", "tier": "MEASURED",
               "status": "live", "statement": "s", "evidence": [], "gate": "g"}
        run(["append", self.mem, "--session", "t1"], self.root,
            stdin=json.dumps(rec))
        # stale orientation must fail validation until close
        run(["validate", self.mem], self.root, expect=1)
        run(["close", self.mem], self.root, expect=0)
        out = run(["status", self.mem], self.root).stdout
        self.assertIn("FRESH", out)
        self.assertIn("claims=1", out)

    def test_claim_chain_and_fork(self):
        run(["init", self.mem, "--program", "Demo"], self.root)
        rec = {"kind": "claim", "id": "C-a", "title": "t", "tier": "HYPOTHESIS",
               "status": "live", "statement": "v1"}
        run(["append", self.mem, "--session", "t"], self.root, stdin=json.dumps(rec))
        rec["statement"] = "v2"
        run(["append", self.mem, "--session", "t"], self.root, stdin=json.dumps(rec))
        run(["close", self.mem], self.root, expect=0)
        lines = open(os.path.join(self.mem, "ledger.jsonl")).read().splitlines()
        r1, r2 = json.loads(lines[0]), json.loads(lines[1])
        self.assertEqual(r2["prev"], r1["h"])
        # simulate a fork: hand-append a record whose prev skips the tip
        forged = dict(r1)
        forged["statement"] = "v3"
        forged.pop("h")
        forged["prev"] = r1["h"]  # stale prev (tip is r2)
        import hashlib
        blob = json.dumps(forged, sort_keys=True, separators=(",", ":"))
        forged["h"] = hashlib.sha256(blob.encode()).hexdigest()[:12]
        with open(os.path.join(self.mem, "ledger.jsonl"), "a") as f:
            f.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")
        p = run(["validate", self.mem], self.root, expect=1)
        self.assertIn("fork", p.stdout)

    def test_closed_claim_needs_receipt(self):
        run(["init", self.mem, "--program", "Demo"], self.root)
        rec = {"kind": "claim", "id": "C-a", "title": "t", "tier": "MEASURED",
               "status": "closed_positive", "statement": "s"}
        run(["append", self.mem, "--session", "t"], self.root, stdin=json.dumps(rec))
        p = run(["validate", self.mem], self.root, expect=1)
        self.assertIn("no evidence and no gate", p.stdout)

    def test_batch_append_and_registry(self):
        reg = os.path.join(self.root, "memory")
        run(["init", reg, "--program", "Registry"], self.root)
        batch = "\n".join([
            json.dumps({"kind": "program", "id": "P-root", "name": "Root",
                        "status": "active", "summary": "top"}),
            json.dumps({"kind": "program", "id": "P-sub", "name": "Sub",
                        "status": "active", "summary": "child",
                        "parents": ["P-root"]}),
        ])
        run(["append", reg, "--session", "t"], self.root, stdin=batch)
        run(["close", reg], self.root, expect=0)
        claims = open(os.path.join(reg, "claims.txt")).read()
        self.assertIn("P-root", claims)
        self.assertIn("  P-sub", claims)  # indented under parent
        out = run(["overview"], self.root).stdout
        self.assertIn("P-root", out)
        self.assertIn("P-sub", out)


if __name__ == "__main__":
    unittest.main()
