"""Command line interface: python3 -m kb <command> (normally via bin/kb)."""
import argparse
import json
import os
import sys

from . import hooks
from .store import STATUSES, TYPES, Kb, KbError, find_kb, resolve_id, today


def csv(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def card_line(n):
    return f"{n.id}  {n.get('status') or '-'}  {n.get('path') or '-'}"


def cmd_new(kb, a):
    body = sys.stdin.read() if a.body == "-" else (a.body or "")
    extra = {"path": os.path.abspath(os.path.expanduser(a.path)) if a.path else "",
             "stack": a.stack, "commands": a.commands or [], "status": a.status}
    n = kb.create(a.type, a.title, a.id, a.project or "", csv(a.tags), csv(a.refs), body, extra)
    print(f"{n.id}\n{n.path}")


def cmd_read(kb, a):
    note = kb.get(a.id)
    refs, back = [], []
    if a.refs:
        for r in note.refs():
            try:
                refs.append(kb.get(r))
            except KbError:
                refs.append(r)  # dangling: keep the bare id
        back = kb.backlinks(note.id)
    if a.json:
        emit({"note": note.to_dict(),
              "refs": [r.to_dict() if not isinstance(r, str) else {"id": r, "missing": True} for r in refs],
              "backlinks": [{"id": b.id, "title": b.get("title")} for b in back]})
        return
    print(note.text(), end="")
    if a.refs:
        for r in refs:
            print(f"\n=== ref: {r if isinstance(r, str) else r.id} ===")
            print("(missing)" if isinstance(r, str) else r.text(), end="" if not isinstance(r, str) else "\n")
        print("\n=== backlinks ===")
        print("\n".join(f"{b.id}  {b.get('title')}" for b in back) or "(none)")


def cmd_search(kb, a):
    hits = kb.search(" ".join(a.query), a.type, a.project, a.limit)
    if a.json:
        return emit(hits)
    for h in hits:
        print(f"{h['id']}  [{h['type']}]  {h['title']}")
        if h["snippet"]:
            print(f"    {h['snippet']}")


def cmd_link(kb, a):
    src, dst = kb.get(a.src), kb.get(a.dst)
    refs = src.meta.setdefault("refs", [])
    if dst.id in refs:
        print(f"already linked: {src.id} -> {dst.id}")
        return
    refs.append(dst.id)
    src.meta["updated"] = today()
    kb.save(src)
    print(f"linked: {src.id} -> {dst.id}")


def cmd_check(kb, a):
    problems, count = kb.check()
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} problem(s) in {count} parsed note(s)", file=sys.stderr)
        return 1
    print(f"OK: {count} note(s)")


def cmd_index(kb, a):
    kb.index(force=True).close()
    print(f"index rebuilt: {kb.root / '.index' / 'kb.sqlite'}")


def cmd_project(kb, a):
    if a.pcmd == "add":
        a.type, a.title, a.id, a.tags, a.refs, a.body = "project", a.name, None, "", "", ""
        a.project = None
        return cmd_new(kb, a)
    if a.pcmd == "list":
        cards = sorted(kb.projects(), key=lambda n: n.id)
        if a.json:
            return emit([n.to_dict() for n in cards])
        for n in cards:
            print(card_line(n))
        return
    card = kb.find_project(a.path or os.getcwd())
    if card is None:
        print(f"no project card for {a.path or os.getcwd()}", file=sys.stderr)
        return 1
    if a.json:
        return emit(card.to_dict())
    print(card.text(), end="")


def cmd_journal(kb, a):
    if a.jcmd == "friction":
        print(hooks.add_friction(kb.root, " ".join(a.text), os.getcwd()))
        return
    for e in hooks.tail(kb.root, a.project, a.n):
        print(e + "\n")


def build_parser():
    def common_opts(**kw):  # fresh actions each time: set_defaults would leak into shared ones
        c = argparse.ArgumentParser(add_help=False)
        c.add_argument("--kb", metavar="PATH", help="kb directory (default: $KB_DIR or ~/agent-kb)", **kw)
        c.add_argument("--json", action="store_true", help="JSON output (read, search, project list/find)", **kw)
        return c

    common = common_opts(default=argparse.SUPPRESS)
    p = argparse.ArgumentParser(prog="kb", description="Markdown knowledge store and project registry.",
                                parents=[common_opts()])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, **kw):
        sp = sub.add_parser(name, help=help_, parents=[common], **kw)
        sp.set_defaults(fn=fn)
        return sp

    def card_opts(sp):
        sp.add_argument("--stack", default="", help="e.g. 'python, sqlite'")
        sp.add_argument("--commands", action="append", help="a useful command (repeat for several)")
        sp.add_argument("--status", help="active|paused|done")

    s = add("new", cmd_new, "create a note")
    s.add_argument("type", choices=TYPES)
    s.add_argument("title")
    s.add_argument("--id")
    s.add_argument("--project")
    s.add_argument("--tags", help="comma separated")
    s.add_argument("--refs", help="comma separated ids")
    s.add_argument("--body", help="body text, or - for stdin")
    s.add_argument("--path", help="project notes: directory of the project")
    card_opts(s)

    s = add("read", cmd_read, "print a note")
    s.add_argument("id")
    s.add_argument("--refs", action="store_true", help="also print referenced notes and backlinks")

    s = add("search", cmd_search, "full-text search")
    s.add_argument("query", nargs="+")
    s.add_argument("--type", choices=TYPES)
    s.add_argument("--project")
    s.add_argument("--limit", type=int, default=10)

    s = add("link", cmd_link, "add a ref from one note to another")
    s.add_argument("src", metavar="from")
    s.add_argument("dst", metavar="to")

    add("check", cmd_check, "validate all notes")
    add("index", cmd_index, "force a search index rebuild")

    s = add("project", cmd_project, "project registry")
    ps = s.add_subparsers(dest="pcmd", required=True)
    a = ps.add_parser("add", parents=[common], help="register a project")
    a.add_argument("name")
    a.add_argument("--path", required=True)
    card_opts(a)
    ps.add_parser("list", parents=[common], help="list project cards")
    f = ps.add_parser("find", parents=[common], help="card for a directory (default: cwd); exit 1 if none")
    f.add_argument("--path")
    s = add("journal", cmd_journal, "session journal (local only)")
    js = s.add_subparsers(dest="jcmd", required=True)
    f = js.add_parser("friction", parents=[common],
                      help="log a friction note: attaches to the newest live session whose cwd equals, "
                           "contains or is inside the current dir; else a standalone journal line")
    f.add_argument("text", nargs="+")
    t = js.add_parser("tail", parents=[common], help="print the last N journal entries (newest last)")
    t.add_argument("--project")
    t.add_argument("-n", type=int, default=5)

    s = add("hook", None, "session lifecycle hook handler (stdin = hook JSON; always exits 0)")
    s.add_argument("event", choices=hooks.EVENTS)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.cmd == "hook":
        return hooks.run(a.event, a.kb)
    try:
        if a.cmd == "project" and a.pcmd == "add" and a.status and a.status not in STATUSES:
            raise KbError(f"status must be one of: {', '.join(STATUSES)}")
        return getattr(a, "fn")(Kb(find_kb(a.kb)), a) or 0
    except KbError as e:
        print(f"kb: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
