"""Tests for `kb backlog` and `kb idea`. Temp kb and temp git repos only."""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_hooks import sh  # noqa: E402
from test_kb import KbTest  # noqa: E402

TASK = """# {title}
Source: x  ·  Status (internal): {status}  ·  Depth: lean
Worktree: /x  ·  Branch: {branch}

## Steps
### S1 — First · `done` · Builder: sonnet
### S2 — Second step · `doing` · Builder: sonnet
### S3 — Third · `todo`

## Handoff (updated today)
- **Done:** S1
- **Next:** {next}
  continues here.
- **Where:** nowhere
"""


class BacklogTest(KbTest):
    def mktask(self, root, name, status="doing", branch="main", next_="do S2", title=None):
        d = Path(root) / ".tasks" / name
        d.mkdir(parents=True)
        (d / "TASK.md").write_text(TASK.format(title=title or name, status=status, branch=branch, next=next_))

    def repo(self, name="repo"):
        r = self.tmp / name
        r.mkdir()
        sh(r, "git", "init", "-q", "-b", "main")
        return r

    def commit(self, r):
        (r / "a.txt").write_text("a")
        sh(r, "git", "add", "-A")
        sh(r, "git", "commit", "-qm", "init")

    def card(self, name, path, status="active"):
        self.ok("project", "add", name, "--path", str(path), "--status", status)

    def bl(self, *args):
        return json.loads(self.ok("backlog", "--json", *args))

    def test_main_worktree_task(self):
        r = self.repo()
        self.mktask(r, "alpha", title="Alpha task")
        self.commit(r)
        self.card("app", r)
        out = self.ok("backlog")
        self.assertIn("proj-app", out)
        self.assertIn("Alpha task", out)
        self.assertIn("S2 — Second step  (2 open)", out)
        self.assertIn("next: do S2 continues here.", out)
        t = self.bl()["projects"][0]["tasks"][0]
        self.assertEqual((t["status"], t["branch"], t["open_steps"]), ("doing", "main", 2))

    def test_linked_worktree_card_path(self):
        r = self.repo()
        self.mktask(r, "alpha")
        self.commit(r)
        wt = self.tmp / "wt"
        sh(r, "git", "worktree", "add", "-q", "-b", "feat", str(wt))
        self.card("app", wt)
        g = self.bl()
        self.assertEqual(g["projects"][0]["tasks"][0]["title"], "alpha")

    def test_non_git_path(self):
        d = self.tmp / "plain"
        d.mkdir()
        self.mktask(d, "alpha")
        self.card("plain", d)
        self.assertEqual(len(self.bl()["projects"][0]["tasks"]), 1)

    def test_missing_path_warns(self):
        self.card("gone", self.tmp / "nope")
        rc, out, err = self.run_kb("backlog")
        self.assertEqual(rc, 0)
        self.assertIn("warning: proj-gone: path missing", out)
        self.assertEqual(len(self.bl()["warnings"]), 1)

    def test_done_parked_filtered_and_all(self):
        d = self.tmp / "p"
        d.mkdir()
        self.mktask(d, "a", "doing")
        self.mktask(d, "b", "done")
        self.mktask(d, "c", "parked")
        self.card("p", d)
        self.assertEqual([t["title"] for t in self.bl()["projects"][0]["tasks"]], ["a"])
        self.assertEqual(len(self.bl("--all")["projects"][0]["tasks"]), 3)

    def test_empty_and_done_projects_omitted(self):
        d, e = self.tmp / "p", self.tmp / "q"
        d.mkdir()
        e.mkdir()
        self.mktask(e, "a")
        self.card("p", d)
        self.card("q", e, "done")
        self.assertEqual(self.bl()["projects"], [])
        self.assertEqual([p["id"] for p in self.bl("--all")["projects"]], ["proj-q"])

    def test_project_filter(self):
        for n in ("p", "q"):
            d = self.tmp / n
            d.mkdir()
            self.mktask(d, "a")
            self.card(n, d)
        self.assertEqual([p["id"] for p in self.bl("--project", "proj://q")["projects"]], ["proj-q"])
        self.assertEqual([p["id"] for p in self.bl("--project", "p")["projects"]], ["proj-p"])
        rc, _, err = self.run_kb("backlog", "--project", "zzz")
        self.assertEqual(rc, 1)

    def test_next_capped(self):
        d = self.tmp / "p"
        d.mkdir()
        self.mktask(d, "a", next_="x" * 500)
        self.card("p", d)
        self.assertLessEqual(len(self.bl()["projects"][0]["tasks"][0]["next"]), 200)

    def test_ideas_unfiled_inbox(self):
        d = self.tmp / "p"
        d.mkdir()
        self.card("p", d)
        pid = self.ok("idea", "add", "filed idea", "--project", "proj://p", "--tags", "a,b").strip()
        self.ok("idea", "add", "loose idea")
        done = self.ok("idea", "add", "old idea", "--project", "p").strip()
        self.ok("idea", "done", done)
        (self.kb / "inbox").mkdir()
        (self.kb / "inbox" / "x.md").write_text("x")
        (self.kb / "inbox" / "y.md").write_text("y")
        g = self.bl()
        self.assertEqual([i["id"] for i in g["projects"][0]["ideas"]], [pid])
        self.assertEqual([i["title"] for i in g["unfiled"]], ["loose idea"])
        self.assertEqual(g["inbox"], {"count": 2, "files": ["x.md", "y.md"]})
        out = self.ok("backlog")
        self.assertIn("unfiled", out)
        self.assertEqual(out.strip().splitlines()[-1], "inbox: 2  x.md, y.md")
        self.assertEqual(len(self.bl("--all")["projects"][0]["ideas"]), 2)
        self.assertNotIn("unfiled", self.ok("backlog", "--project", "p"))

    def test_idea_add_done_check(self):
        d = self.tmp / "p"
        d.mkdir()
        self.card("p", d)
        text = "first line " + "w" * 80 + "\nsecond line"
        old = os.getcwd()
        os.chdir(d)
        try:
            nid = self.ok("idea", "add", text).strip()
        finally:
            os.chdir(old)
        n = self.ok("read", nid)
        self.assertIn("project: proj-p", n)
        self.assertIn("status: active", n)
        self.assertIn("second line", n)
        note = json.loads(self.ok("read", nid, "--json"))["note"]
        self.assertLessEqual(len(note["title"]), 60)
        self.assertEqual(note["type"], "idea")
        self.assertEqual(self.ok("idea", "add", text).strip(), nid + "-2")
        # second idea has the same title: done only touches the first
        note_file = self.kb / "notes" / f"{nid}.md"
        note_file.write_text(note_file.read_text().replace(f"updated: {note['updated']}", "updated: 2000-01-01"))
        self.ok("idea", "done", nid)
        after = json.loads(self.ok("read", nid, "--json"))["note"]
        self.assertEqual(after["status"], "done")
        self.assertNotEqual(after["updated"], "2000-01-01")
        self.ok("check")
        self.assertEqual(self.run_kb("idea", "done", "kb://p")[0], 1)

    def test_fast(self):
        for n in ("a", "b", "c"):
            r = self.repo(n)
            self.mktask(r, "t")
            self.commit(r)
            self.card(n, r)
        t = time.time()
        self.ok("backlog")
        self.assertLess(time.time() - t, 0.5)


if __name__ == "__main__":
    unittest.main()
