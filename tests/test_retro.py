"""Tests for `kb retro gather|record`. Temp kb, temp HOME, never the real ones."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kb import REPO, KbTest  # noqa: E402

from kb import retro  # noqa: E402


def entry(date, hm, proj, extra=""):
    return (f"## {date} {hm} · {proj} · main · abcd1234\n- cwd: /x\n- turns: 3\n"
            f"- summary: did {date} {hm}\n{extra}")


class RetroTest(KbTest):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        self.home.mkdir()
        os.environ["HOME"] = str(self.home)  # restored by KbTest cleanup
        for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
            os.environ[k] = "t"
        for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
            os.environ[k] = "t@t"
        (self.kb / "journal").mkdir()

    def jwrite(self, date, *chunks):
        (self.kb / "journal" / f"{date}.md").write_text("\n".join(chunks))

    def gather(self, *args):
        return self.ok("retro", "gather", *args)

    def gjson(self, *args):
        return json.loads(self.ok("retro", "gather", "--json", *args))

    def git_init(self):
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.kb, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.kb, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.kb, check=True)

    def seed(self):
        self.jwrite("2026-10-01", entry("2026-10-01", "10:00", "proj-a",
                                         "- friction: old thing\n"))
        self.jwrite("2026-10-10", entry("2026-10-10", "09:00", "proj-b") + "\n"
                    + entry("2026-10-10", "11:00", "proj-a", "- friction: new thing\n")
                    + "\n- friction (12:30, proj-b): standalone gripe\n")


class GatherTest(RetroTest):
    def test_all_when_no_retro(self):
        self.seed()
        g = self.gjson()
        self.assertIsNone(g["since"])
        self.assertEqual(g["counts"], {"proj-a": 2, "proj-b": 2})
        self.assertEqual([e["ts"] for e in g["entries"]],
                         ["2026-10-10T12:30", "2026-10-10T11:00", "2026-10-10T09:00", "2026-10-01T10:00"])
        self.assertEqual(g["entries_omitted"], 0)

    def test_friction_parsing(self):
        self.seed()
        out = self.gather()
        self.assertIn("## Friction (3)", out)
        self.assertIn("- 2026-10-10 proj-b: standalone gripe", out)
        self.assertIn("- 2026-10-10 proj-a: new thing", out)
        self.assertIn("- 2026-10-01 proj-a: old thing", out)
        self.assertLess(out.index("Friction"), out.index("## Entries"))

    def test_since_marker_from_retro_note(self):
        self.seed()
        self.write("retro-2026-10-05.md",
                   "---\nid: retro-2026-10-05\ntype: retro\ntitle: R\nuntil: 2026-10-05T08:00:00\n"
                   "accepted: [a]\nrejected: []\n---\n\nbody\n")
        g = self.gjson()
        self.assertEqual(g["since"], "2026-10-05T08:00")
        self.assertEqual(len(g["entries"]), 3)
        self.assertNotIn("old thing", json.dumps(g))

    def test_newest_retro_wins_and_since_override(self):
        self.seed()
        for d, u in (("2026-10-02", "2026-10-02T00:00:00"), ("2026-10-10", "2026-10-10T10:00:00")):
            self.write(f"retro-{d}.md", f"---\nid: retro-{d}\ntype: retro\ntitle: R\nuntil: {u}\n---\n")
        self.assertEqual(len(self.gjson()["entries"]), 2)
        self.assertEqual(len(self.gjson("--since", "2026-10-01T00:00:00")["entries"]), 4)
        self.assertEqual(len(self.gjson("--since", "2026-10-10T11:01")["entries"]), 1)

    def test_bad_since(self):
        rc, _, err = self.run_kb("retro", "gather", "--since", "yesterday")
        self.assertEqual(rc, 1)
        self.assertIn("ISO", err)

    def test_project_filter(self):
        self.seed()
        g = self.gjson("--project", "proj-b")
        self.assertEqual(g["counts"], {"proj-b": 2})
        self.assertTrue(all(e["project"] == "proj-b" for e in g["entries"]))
        self.assertEqual([f["text"] for f in g["friction"]], ["standalone gripe"])

    def test_cap_and_truncation_message(self):
        chunks = [entry("2026-10-10", f"{h:02d}:{m:02d}", "proj-a", "- summary2: " + "x" * 600 + "\n")
                  for h in range(10, 20) for m in (0, 30)]
        self.jwrite("2026-10-10", *chunks)  # 20 entries x ~700 chars > 12k
        out = self.gather()
        self.assertLessEqual(len(out), retro.OUTPUT_CAP)
        self.assertRegex(out, r"\.\.\. \d+ older entries omitted")
        self.assertIn("2026-10-10 19:30", out)      # newest kept
        self.assertNotIn("2026-10-10 10:00", out)   # oldest cut
        g = self.gjson()
        self.assertGreater(g["entries_omitted"], 0)
        self.assertEqual(len(g["entries"]) + g["entries_omitted"], 20)

    def test_friction_truncation(self):
        lines = "\n".join(f"- friction (10:{i % 60:02d}, proj-a): {'y' * 100} {i}" for i in range(80))
        self.jwrite("2026-10-10", lines)
        out = self.gather()
        self.assertLessEqual(len(out), retro.OUTPUT_CAP)
        self.assertRegex(out, r"\.\.\. \d+ older friction lines omitted")

    def test_empty_journal(self):
        out = self.gather()
        self.assertIn("entries: 0", out)
        self.assertIn("(none)", out)
        g = self.gjson()
        self.assertEqual((g["entries"], g["friction"], g["counts"]), ([], [], {}))
        (self.kb / "journal").rmdir()
        self.assertEqual(self.gjson()["entries"], [])

    def test_scope_lists_only_existing(self):
        proj = self.tmp / "work" / "app"
        proj.mkdir(parents=True)
        self.ok("project", "add", "app", "--path", str(proj))
        self.ok("project", "add", "bare", "--path", str(self.tmp / "work" / "bare"))
        files = [self.home / ".claude" / "CLAUDE.md",
                 proj / "CLAUDE.md",
                 self.home / ".claude" / "projects" / str(proj).replace("/", "-") / "memory" / "MEMORY.md",
                 self.home / ".claude" / "skills" / "mine" / "SKILL.md"]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")
        listed = self.gjson()["files"]
        for f in files:
            self.assertIn(str(f), listed)
        for s in (REPO / "plugin" / "skills").glob("*/SKILL.md"):
            self.assertIn(str(s), listed)
        self.assertTrue(all(Path(p).is_file() for p in listed))
        self.assertFalse(any("bare" in p for p in listed))
        self.assertIn(str(proj / "CLAUDE.md"), self.gather())

    def test_scope_dotted_path_slug(self):
        proj = self.tmp / "work" / "my.app_x"
        proj.mkdir(parents=True)
        self.ok("project", "add", "dotted", "--path", str(proj))
        slug = "".join(c if c.isalnum() else "-" for c in str(proj))
        mem = self.home / ".claude" / "projects" / slug / "memory" / "MEMORY.md"
        mem.parent.mkdir(parents=True)
        mem.write_text("x")
        self.assertIn("my-app-x", slug)
        self.assertIn(str(mem), self.gjson()["files"])

    def test_scope_empty_home(self):
        listed = self.gjson()["files"]
        self.assertFalse(any(str(self.home) in p for p in listed))
        self.assertTrue(any(p.endswith("retro/SKILL.md") for p in listed))


class RecordTest(RetroTest):
    def test_note_valid_and_suffixing(self):
        out = self.ok("retro", "record", "--until", "2026-10-12T09:30:00", "--accepted", "a,b",
                      "--rejected", "c", "--body", "-", stdin="## Proposals\n- p1\n")
        self.assertIn("retro-2026-10-12", out)
        self.assertIn("not a git repo", out)
        n = self.kb / "notes" / "retro-2026-10-12.md"
        text = n.read_text()
        self.assertIn("type: retro", text)
        self.assertIn("until: 2026-10-12T09:30:00", text)
        self.assertIn("accepted: [a, b]", text)
        self.assertIn("rejected: [c]", text)
        self.assertIn("## Proposals", text)
        self.assertEqual(self.run_kb("check")[0], 0)
        self.ok("retro", "record", "--until", "2026-10-12T18:00:00")
        self.ok("retro", "record", "--until", "2026-10-12T19:00:00")
        self.assertTrue((self.kb / "notes" / "retro-2026-10-12-2.md").exists())
        self.assertTrue((self.kb / "notes" / "retro-2026-10-12-3.md").exists())
        self.assertEqual(self.run_kb("check")[0], 0)

    def test_body_file_and_bad_until(self):
        f = self.tmp / "body.md"
        f.write_text("from file\n")
        self.ok("retro", "record", "--until", "2026-10-12T09:30", "--body-file", str(f))
        self.assertIn("from file", (self.kb / "notes" / "retro-2026-10-12.md").read_text())
        rc, _, err = self.run_kb("retro", "record", "--until", "soon")
        self.assertEqual(rc, 1)
        rc, _, err = self.run_kb("retro", "record", "--until", "2026-10-12", "--body-file", str(self.tmp / "nope"))
        self.assertEqual(rc, 1)
        self.assertIn("body file", err)

    def test_other_types_unaffected(self):
        self.ok("new", "note", "Plain")
        self.assertEqual(self.run_kb("check")[0], 0)

    def test_marker_moves(self):
        self.seed()
        self.assertEqual(len(self.gjson()["entries"]), 4)
        self.ok("retro", "record", "--until", "2026-10-10T10:00:00")
        g = self.gjson()
        self.assertEqual(g["since"], "2026-10-10T10:00")
        self.assertEqual([e["ts"] for e in g["entries"]], ["2026-10-10T12:30", "2026-10-10T11:00"])
        self.ok("retro", "record", "--until", "2026-10-11T00:00:00")
        self.assertEqual(self.gjson()["entries"], [])
        self.assertIn("entries: 0", self.gather())

    def test_commit_made_no_remote(self):
        self.seed()
        self.git_init()
        (self.kb / "journal" / "2026-10-11.md").write_text(entry("2026-10-11", "08:00", "proj-a"))
        (self.kb / "notes" / "unrelated.md").write_text(
            "---\nid: unrelated\ntype: note\ntitle: U\n---\n")
        out = self.ok("retro", "record", "--until", "2026-10-12T09:30:00")
        self.assertIn("committed in kb", out)
        show = subprocess.run(["git", "show", "--stat", "--format=%s", "HEAD"], cwd=self.kb,
                              capture_output=True, text=True, check=True).stdout
        self.assertIn("retro 2026-10-12", show)
        self.assertIn("journal/2026-10-11.md", show)
        self.assertIn("notes/retro-2026-10-12.md", show)
        self.assertNotIn("unrelated", show)
        self.assertEqual(subprocess.run(["git", "remote"], cwd=self.kb, capture_output=True,
                                        text=True).stdout.strip(), "")

    def test_non_git_kb_no_crash(self):
        out = self.ok("retro", "record", "--until", "2026-10-12T09:30:00")
        self.assertIn("skipped commit", out)
        self.assertFalse((self.kb / ".git").exists())

    def test_kb_inside_other_repo_not_committed(self):
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.tmp, check=True)
        out = self.ok("retro", "record", "--until", "2026-10-12T09:30:00")
        self.assertIn("skipped commit", out)
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(log.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
