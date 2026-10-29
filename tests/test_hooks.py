"""Tests for `kb hook <event>` and `kb journal`. Temp kb and temp git repos only."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_kb import REPO, KbTest  # noqa: E402

from kb import hooks  # noqa: E402

TASK = """# Demo task
Source: x  ·  Status (internal): {status}  ·  Depth: lean
Worktree: /x  ·  Branch: {branch}

## Steps
- a step

## Handoff (updated today)
- **Done:** {marker}
- **Next:** more
"""


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class HookTest(KbTest):
    SID = "sess-1234567890"

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("a")
        self.task("alpha", "doing", "main", "HANDOFF-ALPHA")
        self.task("beta", "doing", "other", "HANDOFF-BETA")
        self.task("gone", "done", "main", "HANDOFF-GONE")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "init")
        self.env = {**os.environ, "KB_DIR": str(self.kb)}

    def task(self, slug, status, branch, marker):
        d = self.repo / ".tasks" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "TASK.md").write_text(TASK.format(status=status, branch=branch, marker=marker))

    def card(self, stack="python"):
        self.ok("project", "add", "demo", "--path", str(self.repo), "--stack", stack)

    def hook(self, event, **data):
        rc, out, err = self.run_kb("hook", event, stdin=json.dumps(data))
        self.assertEqual(rc, 0, err)
        return out

    def start(self, sid=None):
        return self.hook("session-start", session_id=sid or self.SID, cwd=str(self.repo), source="startup")

    def stop(self, msg="m"):
        return self.hook("stop", session_id=self.SID, last_assistant_message=msg)

    def end(self, reason="other"):
        return self.hook("session-end", session_id=self.SID, cwd=str(self.repo), reason=reason)

    def journal(self):
        d = self.kb / "journal"
        return "".join(p.read_text() for p in sorted(d.glob("*.md"))) if d.is_dir() else ""

    def state(self, sid=None):
        return self.kb / ".state" / "sessions" / f"{sid or self.SID}.json"

    def cli(self, cwd, *args):
        return subprocess.run([str(REPO / "bin" / "kb"), *args], cwd=cwd, env=self.env,
                              capture_output=True, text=True)


class SessionStartTest(HookTest):
    def test_card_tasks_journal(self):
        self.card()
        (self.kb / "journal").mkdir()
        (self.kb / "journal" / "2026-01-01.md").write_text(
            "## 2026-01-01 10:00 · proj-demo · main · abcd1234\n- cwd: x\n")
        o = json.loads(self.start())
        h = o["hookSpecificOutput"]
        self.assertEqual(h["hookEventName"], "SessionStart")
        ctx = h["additionalContext"]
        self.assertIn("id: proj-demo", ctx)
        self.assertIn("HANDOFF-ALPHA", ctx)          # full handoff, current branch
        self.assertNotIn("HANDOFF-BETA", ctx)        # other branch: one line only
        self.assertNotIn("HANDOFF-GONE", ctx)        # done task excluded
        self.assertIn("Demo task (doing)", ctx)
        self.assertIn("abcd1234", ctx)
        self.assertIn('kb journal friction "<text>"', ctx)
        self.assertEqual(o["systemMessage"], "pipeline: proj-demo · 2 open tasks · 1 journal entry")
        self.assertTrue(self.state().exists())
        self.assertEqual((self.kb / ".state" / ".gitignore").read_text().strip(), "*")

    def test_state_fields_and_kept_on_second_start(self):
        self.card()
        self.start()
        st = json.loads(self.state().read_text())
        for k in ("cwd", "project", "branch", "start_head", "started_at"):
            self.assertTrue(st[k], k)
        self.assertEqual((st["project"], st["branch"]), ("proj-demo", "main"))
        st["started_at"] = 1.0
        self.state().write_text(json.dumps(st))
        self.start()
        self.assertEqual(json.loads(self.state().read_text())["started_at"], 1.0)

    def test_caps(self):
        self.card(stack="S" * 5000)
        self.task("alpha", "doing", "main", "H" * 6000)
        (self.kb / "journal").mkdir()
        (self.kb / "journal" / "2026-01-01.md").write_text(
            "## 2026-01-01 10:00 · proj-demo · main · abcd1234\n- summary: " + "J" * 4000 + "\n")
        ctx = json.loads(self.start())["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), 8000)
        self.assertIn("[...truncated]", ctx)
        self.assertLess(ctx.count("S"), 2600)
        self.assertLess(ctx.count("H"), 3100)
        self.assertLess(ctx.count("J"), 1600)

    def test_git_without_card_gives_hint(self):
        ctx = json.loads(self.start())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("kb project add", ctx)
        self.assertNotIn("HANDOFF", ctx)

    def test_neither_git_nor_card_is_silent(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        out = self.hook("session-start", session_id="s", cwd=str(plain), source="startup")
        self.assertEqual(out, "")
        self.assertFalse(self.state("s").exists())

    def test_missing_kb_dir(self):
        rc, out, _ = self.run_kb("--kb", str(self.tmp / "nokb"), "hook", "session-start",
                                 stdin=json.dumps({"session_id": "s", "cwd": str(self.repo)}), kb=False)
        self.assertEqual((rc, out), (0, ""))
        self.assertFalse((self.tmp / "nokb").exists())

    def test_bad_stdin_logged_not_raised(self):
        rc, out, _ = self.run_kb("hook", "stop", stdin="{not json")
        self.assertEqual((rc, out), (0, ""))
        log = (self.kb / ".state" / "hook-errors.log").read_text()
        self.assertIn(" stop ", log)
        self.assertIn("JSONDecodeError", log)
        self.assertEqual(len(log.splitlines()), 1)


class StopEndTest(HookTest):
    def test_stop_counts_and_truncates(self):
        self.card()
        self.start()
        self.stop("x" * 900)
        self.stop("y" * 900)
        st = json.loads(self.state().read_text())
        self.assertEqual(st["turns"], 2)
        self.assertEqual(st["last_assistant_message"], "y" * 500)

    def test_stop_without_state_is_noop(self):
        self.assertEqual(self.hook("stop", session_id="nope", last_assistant_message="x"), "")
        self.assertFalse(self.state("nope").exists())

    def test_end_writes_entry(self):
        self.card()
        self.start()
        (self.repo / "a.txt").write_text("changed")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "second commit")
        for i in range(12):
            (self.repo / f"new{i}.txt").write_text("n")
        self.stop("did\nthe thing")
        self.end()
        j = self.journal()
        self.assertRegex(j, r"## \d{4}-\d\d-\d\d \d\d:\d\d · proj-demo · main · sess-123\n")
        for needle in (f"- cwd: {self.repo}", "- reason: other", "- turns: 1", "- duration: ",
                       "- summary: did the thing", "second commit"):
            self.assertIn(needle, j)
        changed = [ln for ln in j.splitlines() if ln.startswith("- changed:")][0]
        self.assertIn("(+3 more)", changed)  # 12 untracked + a.txt (+ .tasks stays committed) = 13, 10 shown
        self.assertNotIn("friction", j)
        self.assertFalse(self.state().exists())

    def test_end_attached_friction(self):
        self.card()
        self.start()
        self.stop()
        self.assertIn("friction noted", hooks.add_friction(self.kb, "  user\ncorrected me ", str(self.repo)))
        self.end("clear")
        self.assertIn("- friction: user corrected me", self.journal())

    def test_end_skips_zero_turns_and_deletes_state(self):
        self.card()
        self.start()
        self.end("clear")
        self.assertEqual(self.journal(), "")
        self.assertFalse(self.state().exists())

    def test_end_skips_unmatched_dir(self):
        p = self.state("zz")
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"session_id": "zz", "cwd": str(self.tmp), "project": "", "git": False,
                                 "turns": 3, "started_at": time.time()}))
        self.hook("session-end", session_id="zz", reason="other")
        self.assertFalse(p.exists())
        self.assertEqual(self.journal(), "")

    def test_end_timing(self):
        self.card()
        self.start()
        self.stop()
        t = time.time()
        r = subprocess.run([str(REPO / "bin" / "kb"), "hook", "session-end"], env=self.env, text=True,
                           input=json.dumps({"session_id": self.SID, "cwd": str(self.repo), "reason": "other"}),
                           capture_output=True)
        dt = time.time() - t
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("proj-demo", self.journal())
        self.assertLess(dt, 0.3, f"session-end took {dt:.3f}s")


class JournalCliTest(HookTest):
    def test_friction_standalone_and_tail_filter(self):
        r = self.cli(self.repo, "journal", "friction", "plain", "note")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.journal(), r"- friction \(\d\d:\d\d, repo\): plain note")
        (self.kb / "journal" / "2026-01-01.md").write_text(
            "## 2026-01-01 10:00 · proj-a · main · aaaaaaaa\n- turns: 1\n\n"
            "## 2026-01-01 11:00 · proj-b · main · bbbbbbbb\n- turns: 2\n\n"
            "## 2026-01-01 12:00 · proj-a · main · cccccccc\n- turns: 3\n")
        out = self.ok("journal", "tail", "--project", "proj-a")
        self.assertIn("aaaaaaaa", out)
        self.assertIn("cccccccc", out)
        self.assertNotIn("bbbbbbbb", out)
        self.assertLess(out.index("aaaaaaaa"), out.index("cccccccc"))
        out = self.ok("journal", "tail", "-n", "2")  # today's standalone line sorts last
        self.assertNotIn("aaaaaaaa", out)
        self.assertNotIn("bbbbbbbb", out)
        self.assertIn("cccccccc", out)
        self.assertLess(out.index("cccccccc"), out.index("plain note"))
        out = self.ok("journal", "tail", "--project", "repo")
        self.assertIn("plain note", out)
        self.assertNotIn("cccccccc", out)

    def test_friction_attaches_from_subdir(self):
        self.card()
        self.start()
        sub = self.repo / "sub"
        sub.mkdir()
        r = self.cli(sub, "journal", "friction", "from sub")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.state().read_text())["friction"], ["from sub"])
        self.assertEqual(self.journal(), "")


class StaleStateTest(HookTest):
    def age(self, p, hours):
        t = time.time() - hours * 3600
        os.utime(p, (t, t))

    def test_start_prunes_old_state(self):
        self.card()
        self.start("old")
        self.start("recent")
        self.age(self.state("old"), 49)
        self.age(self.state("recent"), 47)
        self.start()
        self.assertFalse(self.state("old").exists())
        self.assertTrue(self.state("recent").exists())
        self.assertTrue(self.state().exists())

    def test_friction_ignores_old_state(self):
        self.card()
        self.start()
        self.age(self.state(), 13)
        self.assertIn("written to journal", hooks.add_friction(self.kb, "late", str(self.repo)))
        self.assertNotIn("friction", json.loads(self.state().read_text()))
        self.age(self.state(), 11)
        self.assertIn("friction noted", hooks.add_friction(self.kb, "fresh", str(self.repo)))


class TailProjectFilterTest(HookTest):
    def test_empty_project_is_a_filter_none_is_not(self):
        (self.kb / "journal").mkdir()
        (self.kb / "journal" / "2026-01-01.md").write_text(
            "## 2026-01-01 10:00 · proj-a · main · aaaaaaaa\n- turns: 1\n")
        self.assertEqual(hooks.tail(self.kb, "", 5), [])
        self.assertEqual(len(hooks.tail(self.kb, None, 5)), 1)
        self.assertIn("aaaaaaaa", self.ok("journal", "tail"))


class RunKbScriptTest(unittest.TestCase):
    def test_fallback_and_absent(self):
        script = REPO / "plugin" / "hooks" / "run-kb.sh"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "kbdir").mkdir()
            base = {"PATH": "/usr/bin:/bin", "KB_DIR": str(d / "kbdir")}
            # kb not on PATH: falls back to ${CLAUDE_PLUGIN_ROOT}/../bin/kb (the real one)
            r = subprocess.run([str(script), "journal", "tail"],
                               env={**base, "CLAUDE_PLUGIN_ROOT": str(REPO / "plugin")},
                               capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stderr), (0, ""))
            # kb absent everywhere: silent exit 0
            r = subprocess.run([str(script), "hook", "stop"], env={**base, "CLAUDE_PLUGIN_ROOT": str(d / "nothing")},
                               capture_output=True, text=True, input="{}")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
            # the fallback path really executes the file it found
            (d / "bin").mkdir()
            (d / "bin" / "kb").write_text("#!/bin/sh\necho ran-fallback\n")
            (d / "bin" / "kb").chmod(0o755)
            (d / "plugin").mkdir()
            r = subprocess.run([str(script), "x"], env={**base, "CLAUDE_PLUGIN_ROOT": str(d / "plugin")},
                               capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "ran-fallback")


if __name__ == "__main__":
    unittest.main()
