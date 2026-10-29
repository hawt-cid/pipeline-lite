"""Tests for the kb CLI. Every test uses a throwaway kb and never touches ~/agent-kb."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "kb"))

from kb import __main__ as cli  # noqa: E402
from kb import store  # noqa: E402


class KbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.kb = self.tmp / "kb"
        (self.kb / "notes").mkdir(parents=True)
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        os.environ.pop("KB_DIR", None)
        os.environ.pop("KB_NO_FTS", None)

    def run_kb(self, *args, stdin=None, kb=True):
        """Run the CLI in-process; returns (exit code, stdout, stderr)."""
        argv = (["--kb", str(self.kb)] if kb else []) + list(args)
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = cli.main(argv)
                except SystemExit as e:
                    rc = e.code
        finally:
            sys.stdin = old_stdin
        return rc, out.getvalue(), err.getvalue()

    def ok(self, *args, **kw):
        rc, out, err = self.run_kb(*args, **kw)
        self.assertEqual(rc, 0, f"{args}: {err}")
        return out

    def write(self, name, text):
        (self.kb / "notes" / name).write_text(text, encoding="utf-8")

    def note(self, nid, title="T", body="", extra="", type_="note"):
        self.write(f"{nid}.md", f"---\nid: {nid}\ntype: {type_}\ntitle: {title}\n{extra}---\n\n{body}\n")


class FrontmatterTest(KbTest):
    def test_round_trip(self):
        meta = {"id": "x", "type": "project", "title": 'Quote "me": [x], y', "project": "p",
                "tags": ["a", "b,c", "d e", '"q"'], "refs": ["r1"], "status": "active",
                "created": "2026-01-01", "updated": "2026-01-02", "path": "/a b/c",
                "stack": "py, sqlite", "commands": ["make test", "echo 'a, b' | cat", '"lead"', "- dash"],
                "extra": "kept"}
        text = store.format_note(meta, "Body [[r1]]\n\nmore")
        meta2, body2 = store.parse_frontmatter(text)
        self.assertEqual(meta2, meta)
        self.assertEqual(body2, "Body [[r1]]\n\nmore")
        self.assertEqual(store.format_note(meta2, body2), text)

    def test_inline_and_block_commands_parse(self):
        m, _ = store.parse_frontmatter("---\nid: a\ncommands: [one, two]\n---\n")
        self.assertEqual(m["commands"], ["one", "two"])
        m, _ = store.parse_frontmatter("---\nid: a\ncommands:\n  - one two\n  - three\nempty:\n---\n")
        self.assertEqual(m["commands"], ["one two", "three"])
        self.assertEqual(m["empty"], "")

    def test_errors(self):
        for bad in ("no fm", "---\nid: a\n", "---\njunk line\n---\n", "---\n  - x\n---\n", "---\nid: [a\n---\n"):
            with self.assertRaises(store.KbError, msg=bad):
                store.parse_frontmatter(bad)


class CommandsTest(KbTest):
    def test_new_and_read(self):
        out = self.ok("new", "note", "Hello World", "--tags", "a,b", "--body", "text here")
        nid, path = out.split("\n")[:2]
        self.assertEqual(nid, "hello-world")
        self.assertEqual(Path(path), self.kb / "notes" / "hello-world.md")
        text = self.ok("read", "hello-world")
        self.assertIn("title: Hello World", text)
        self.assertIn("tags: [a, b]", text)
        self.assertIn(f"created: {store.today()}", text)
        self.assertIn("text here", text)

    def test_new_refuses_overwrite(self):
        self.ok("new", "note", "Dup")
        rc, _, err = self.run_kb("new", "note", "Dup")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)

    def test_new_body_stdin_and_bad_id(self):
        self.ok("new", "howto", "From stdin", "--body", "-", stdin="line1\nline2")
        self.assertIn("line2", self.ok("read", "from-stdin"))
        rc, _, _ = self.run_kb("new", "note", "x", "--id", "Bad_ID")
        self.assertEqual(rc, 1)

    def test_shorthands(self):
        self.ok("new", "project", "Demo")
        self.assertTrue((self.kb / "notes" / "proj-demo.md").exists())
        for ref in ("proj://demo", "kb://proj-demo", "proj-demo"):
            self.assertIn("id: proj-demo", self.ok("read", ref))
        self.ok("new", "note", "Other", "--refs", "proj://demo")
        self.assertIn("refs: [proj-demo]", self.ok("read", "kb://other"))
        self.ok("link", "other", "kb://proj-demo")  # idempotent with shorthand

    def test_read_refs_and_backlinks(self):
        self.note("a", "Alpha", "see [[b]] and [[ghost]]", extra="refs: [proj://c]\n")
        self.note("b", "Beta", "beta body")
        self.note("proj-c", "Gamma", "gamma body", type_="project")
        self.note("d", "Delta", "points at [[a]]")
        out = self.ok("read", "a", "--refs")
        self.assertIn("=== ref: b ===", out)
        self.assertIn("beta body", out)
        self.assertIn("gamma body", out)
        self.assertIn("=== ref: ghost ===\n(missing)", out)
        self.assertIn("=== backlinks ===\nd  Delta", out)
        data = json.loads(self.ok("read", "a", "--refs", "--json"))
        self.assertEqual(data["note"]["id"], "a")
        self.assertEqual({r["id"] for r in data["refs"]}, {"b", "ghost", "proj-c"})
        self.assertEqual(data["backlinks"], [{"id": "d", "title": "Delta"}])

    def test_read_missing(self):
        rc, _, err = self.run_kb("read", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("no such note", err)

    def test_link_idempotent(self):
        self.note("a", "A")
        self.note("b", "B")
        self.ok("link", "a", "b")
        first = (self.kb / "notes" / "a.md").read_text()
        self.assertIn("refs: [b]", first)
        out = self.ok("link", "a", "b")
        self.assertIn("already", out)
        self.assertEqual((self.kb / "notes" / "a.md").read_text(), first)
        rc, _, _ = self.run_kb("link", "a", "missing")
        self.assertEqual(rc, 1)

    def test_link_bumps_updated(self):
        self.note("a", "A", extra="updated: 2000-01-01\n")
        self.note("b", "B")
        self.ok("link", "a", "b")
        self.assertIn(f"updated: {store.today()}", self.ok("read", "a"))


class SearchTest(KbTest):
    def setUp(self):
        super().setUp()
        self.note("sqlite-tips", "SQLite tips", "Use WAL mode for concurrency", "tags: [db]\nproject: p1\n", "howto")
        self.note("cooking", "Pasta", "boil water; mention sqlite once", "project: p2\n")
        self.note("other", "Other", "nothing relevant")

    def test_search_ranks_and_filters(self):
        hits = json.loads(self.ok("search", "sqlite", "--json"))
        self.assertEqual([h["id"] for h in hits], ["sqlite-tips", "cooking"])
        self.assertEqual(json.loads(self.ok("search", "sqlite", "--type", "howto", "--json"))[0]["id"], "sqlite-tips")
        self.assertEqual([h["id"] for h in json.loads(self.ok("search", "sqlite", "--project", "p2", "--json"))], ["cooking"])
        self.assertEqual(len(json.loads(self.ok("search", "sqlite", "--limit", "1", "--json"))), 1)
        self.assertEqual(json.loads(self.ok("search", "zzzz", "--json")), [])
        self.assertTrue((self.kb / ".index" / "kb.sqlite").exists())

    def test_search_text_output_and_odd_query(self):
        out = self.ok("search", "wal", "mode")
        self.assertIn("sqlite-tips  [howto]  SQLite tips", out)
        self.assertEqual(self.run_kb("search", 'NEAR( " AND *')[0], 0)  # FTS syntax chars are inert

    def test_auto_reindex_after_edit_add_delete(self):
        self.assertEqual(json.loads(self.ok("search", "banana", "--json")), [])
        p = self.kb / "notes" / "other.md"
        p.write_text(p.read_text().replace("nothing relevant", "banana split recipe"))
        self.assertEqual([h["id"] for h in json.loads(self.ok("search", "banana", "--json"))], ["other"])
        self.note("new-one", "New", "banana again")
        self.assertEqual(len(json.loads(self.ok("search", "banana", "--json"))), 2)
        (self.kb / "notes" / "new-one.md").unlink()
        self.assertEqual(len(json.loads(self.ok("search", "banana", "--json"))), 1)

    def test_index_command(self):
        self.assertIn("index rebuilt", self.ok("index"))

    def test_rg_fallback_same_shape(self):
        fts = json.loads(self.ok("search", "sqlite", "--json"))
        os.environ["KB_NO_FTS"] = "1"
        rg = json.loads(self.ok("search", "sqlite", "--json"))
        self.assertEqual([h["id"] for h in rg], [h["id"] for h in fts])
        self.assertEqual(set(rg[0]), set(fts[0]))
        self.assertEqual([h["id"] for h in json.loads(self.ok("search", "sqlite", "--type", "howto", "--json"))], ["sqlite-tips"])
        self.assertEqual([h["id"] for h in json.loads(self.ok("search", "sqlite", "--project", "p2", "--json"))], ["cooking"])
        self.assertEqual(json.loads(self.ok("search", "zzzz", "--json")), [])
        self.assertIn("sqlite-tips  [howto]", self.ok("search", "wal"))


class CheckTest(KbTest):
    def check(self):
        rc, out, _ = self.run_kb("check")
        return rc, out

    def test_ok(self):
        self.note("a", "A", "[[b]]")
        self.note("b", "B")
        rc, out = self.check()
        self.assertEqual((rc, out.strip()), (0, "OK: 2 note(s)"))

    def test_each_failure_class(self):
        cases = {
            "unparseable": ("bad.md", "no frontmatter", "unparseable frontmatter"),
            "missing": ("m.md", "---\nid: m\ntype: note\n---\n", "missing required field 'title'"),
            "type": ("t.md", "---\nid: t\ntype: weird\ntitle: T\n---\n", "unknown type 'weird'"),
            "filename": ("f.md", "---\nid: other\ntype: note\ntitle: T\n---\n", "does not match filename"),
            "dangling": ("d.md", "---\nid: d\ntype: note\ntitle: T\nrefs: [nowhere]\n---\n[[gone]]", "dangling ref 'gone'"),
        }
        for label, (name, text, expect) in cases.items():
            with self.subTest(label):
                self.write(name, text)
                rc, out = self.check()
                self.assertEqual(rc, 1)
                self.assertIn(expect, out)
                (self.kb / "notes" / name).unlink()

    def test_duplicate_ids(self):
        self.note("a", "A")
        self.write("a-copy.md", "---\nid: a\ntype: note\ntitle: A2\n---\n")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("duplicate id 'a'", out)


class ProjectTest(KbTest):
    def add(self, name, path, *extra):
        return self.ok("project", "add", name, "--path", str(path), *extra)

    def test_add_list(self):
        d = self.tmp / "work" / "alpha"
        d.mkdir(parents=True)
        out = self.add("Alpha", d, "--stack", "python", "--commands", "make test", "--commands", "make run, fast", "--status", "paused")
        self.assertTrue(out.startswith("proj-alpha\n"))
        card = self.ok("read", "proj://alpha")
        self.assertIn("type: project", card)
        self.assertIn(f"path: {d}", card)
        self.assertIn("status: paused", card)
        self.assertIn("commands:\n  - make test\n  - make run, fast\n", card)
        self.assertEqual(self.ok("project", "list").strip(), f"proj-alpha  paused  {d}")
        self.assertEqual(json.loads(self.ok("project", "list", "--json"))[0]["commands"], ["make test", "make run, fast"])
        self.assertEqual(self.run_kb("project", "add", "Bad", "--path", "/x", "--status", "nope")[0], 1)
        self.assertEqual(self.run_kb("project", "add", "Alpha", "--path", str(d))[0], 1)  # exists

    def test_find_ancestor_longest_and_none(self):
        outer, inner = self.tmp / "w" / "outer", self.tmp / "w" / "outer" / "inner"
        deep = inner / "src" / "x"
        deep.mkdir(parents=True)
        self.add("outer", outer)
        self.add("inner", inner)
        self.add("prefix", self.tmp / "w" / "out")  # must not match "outer" by string prefix
        self.assertIn("id: proj-inner", self.ok("project", "find", "--path", str(deep)))
        self.assertIn("id: proj-inner", self.ok("project", "find", "--path", str(inner)))
        self.assertIn("id: proj-outer", self.ok("project", "find", "--path", str(outer)))
        self.assertEqual(json.loads(self.ok("project", "find", "--path", str(deep), "--json"))["id"], "proj-inner")
        rc, out, _ = self.run_kb("project", "find", "--path", str(self.tmp))
        self.assertEqual((rc, out), (1, ""))

    def test_find_symlink_tilde_and_cwd(self):
        real = self.tmp / "real"
        (real / "sub").mkdir(parents=True)
        (self.tmp / "link").symlink_to(real)
        self.add("real", real)
        self.assertIn("proj-real", self.ok("project", "find", "--path", str(self.tmp / "link" / "sub")))
        os.environ["HOME"] = str(self.tmp)
        self.write("proj-tilde.md", "---\nid: proj-tilde\ntype: project\ntitle: Tilde\npath: ~/real/sub\n---\n")
        self.assertIn("proj-tilde", self.ok("project", "find", "--path", "~/real/sub"))
        old = os.getcwd()
        os.chdir(real / "sub")
        try:
            self.assertIn("type: project", self.ok("project", "find"))
        finally:
            os.chdir(old)

    def test_find_fast_cold(self):
        target = self.tmp / "t" / "proj"
        target.mkdir(parents=True)
        for i in range(100):
            self.note(f"n{i}", f"Note {i}", "filler " * 200)
        for i in range(10):
            self.add(f"p{i}", self.tmp / f"p{i}")
        self.add("target", target)
        t = time.perf_counter()
        r = subprocess.run([str(REPO / "bin" / "kb"), "--kb", str(self.kb), "project", "find", "--path", str(target)],
                           capture_output=True, text=True)
        dt = time.perf_counter() - t
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("proj-target", r.stdout)
        self.assertLess(dt, 0.2, f"project find took {dt:.3f}s")


class LocationTest(KbTest):
    def test_precedence_and_errors(self):
        env_kb, flag_kb = self.tmp / "envkb", self.tmp / "flagkb"
        for d in (env_kb, flag_kb):
            (d / "notes").mkdir(parents=True)
        os.environ["KB_DIR"] = str(env_kb)
        self.ok("new", "note", "In env", kb=False)
        self.assertTrue((env_kb / "notes" / "in-env.md").exists())
        self.ok("--kb", str(flag_kb), "new", "note", "In flag", kb=False)  # flag before the subcommand
        self.assertTrue((flag_kb / "notes" / "in-flag.md").exists())
        self.assertFalse((env_kb / "notes" / "in-flag.md").exists())
        self.ok("new", "note", "Both", "--kb", str(flag_kb), kb=False)  # flag after
        self.assertTrue((flag_kb / "notes" / "both.md").exists())

    def test_missing_dir_error_and_bin_wrapper(self):
        rc, _, err = self.run_kb("--kb", str(self.tmp / "nope"), "check", kb=False)
        self.assertEqual(rc, 1)
        self.assertIn("kb directory not found", err)
        r = subprocess.run([str(REPO / "bin" / "kb"), "check"], capture_output=True, text=True,
                           env={**os.environ, "KB_DIR": str(self.tmp / "nope")})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("kb directory not found", r.stderr)

    def test_bin_via_symlink(self):
        link = self.tmp / "bin-link"
        link.symlink_to(REPO / "bin" / "kb")
        r = subprocess.run([str(link), "--kb", str(self.kb), "check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
