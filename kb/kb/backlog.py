"""Cross-project backlog: open tasks (from TASK.md files), open ideas and the inbox.

Read-only: only `git worktree list` is run, nothing is written outside the kb.
"""
import os

from .hooks import main_worktree, parse_tasks
from .store import KbError, resolve_id, slugify, today

NEXT_CAP = 200


def match_card(cards, raw):
    ref = resolve_id(raw)
    for c in cards:
        if ref in (c.id, "proj-" + ref, c.get("project")) or slugify(ref) == c.get("project"):
            return c
    return None


def ideas(kb):
    out = []
    for p in kb.files():
        try:
            if "type: idea" not in p.read_text(encoding="utf-8")[:2000]:
                continue
            n = kb.load_file(p)
        except (KbError, OSError, UnicodeDecodeError):
            continue
        if n.get("type") == "idea":
            out.append(n)
    return sorted(out, key=lambda n: n.id)


def idea_dict(n):
    return {"id": n.id, "title": n.get("title"), "status": n.get("status") or "active"}


def task_dict(t):
    open_ = [name for name, st in t["steps"] if st != "done"]
    nxt = t["next"]
    return {"title": t["title"], "status": t["status"], "branch": t["branch"],
            "next_step": open_[0] if open_ else "", "open_steps": len(open_),
            "next": nxt if len(nxt) <= NEXT_CAP else nxt[:NEXT_CAP - 3] + "..."}


def gather(kb, project=None, all_=False):
    allcards = sorted(kb.projects(), key=lambda n: n.id)
    cards = allcards
    if project:
        c = match_card(allcards, project)
        if c is None:
            raise KbError(f"no project card for '{project}'")
        cards = [c]
    notes = [n for n in ideas(kb) if all_ or n.get("status") != "done"]
    res = {"projects": [], "unfiled": [], "warnings": [], "inbox": {"count": 0, "files": []}}
    for c in cards:
        if not all_ and c.get("status") == "done":
            continue
        raw = c.get("path")
        path = os.path.expanduser(raw) if raw else ""
        if not path or not os.path.isdir(path):
            res["warnings"].append(f"{c.id}: path missing ({raw or 'not set'})")
            continue
        root = main_worktree(path) or path
        tasks = [task_dict(t) for t in parse_tasks(root, all_)]
        mine = [idea_dict(n) for n in notes if n.get("project") and match_card([c], n.get("project")) is c]
        if tasks or mine:
            res["projects"].append({"id": c.id, "path": raw, "tasks": tasks, "ideas": mine})
    if not project:
        res["unfiled"] = [idea_dict(n) for n in notes
                          if not n.get("project") or match_card(allcards, n.get("project")) is None]
    inbox = kb.root / "inbox"
    files = sorted(f.name for f in inbox.iterdir() if f.is_file() and not f.name.startswith(".")) if inbox.is_dir() else []
    res["inbox"] = {"count": len(files), "files": files}
    return res


def idea_line(i):
    return f"  idea: {i['id']}  {i['title']}" + ("  [done]" if i["status"] == "done" else "")


def render(g):
    out = [f"warning: {w}" for w in g["warnings"]]
    for p in g["projects"]:
        out.append(f"{p['id']}  {p['path']}")
        for t in p["tasks"]:
            out.append(f"  task: {t['title']}  [{t['status']}]" + (f"  {t['branch']}" if t["branch"] else ""))
            if t["next_step"]:
                out.append(f"    step: {t['next_step']}  ({t['open_steps']} open)")
            if t["next"]:
                out.append(f"    next: {t['next']}")
        out += [idea_line(i) for i in p["ideas"]]
    if g["unfiled"]:
        out.append("unfiled")
        out += [idea_line(i) for i in g["unfiled"]]
    ib = g["inbox"]
    out.append(f"inbox: {ib['count']}" + (f"  {', '.join(ib['files'])}" if ib["files"] else ""))
    return "\n".join(out) + "\n"


def idea_add(kb, text, project=None, tags=(), cwd=None):
    text = text.strip()
    if not text:
        raise KbError("idea text is empty")
    title = " ".join(text.split("\n", 1)[0].split())[:60].strip()
    if project:
        c = match_card(kb.projects(), project)
        pid = c.id if c else resolve_id(project)
    else:
        c = kb.find_project(cwd or os.getcwd())
        pid = c.id if c else ""
    base, n = slugify(title), 1
    while True:
        try:
            return kb.create("idea", title, base if n == 1 else f"{base}-{n}", pid, tags, (), text,
                             {"status": "active"})
        except KbError as e:
            if "already exists" not in str(e) or n > 50:
                raise
            n += 1


def idea_done(kb, raw_id):
    n = kb.get(raw_id)
    if n.get("type") != "idea":
        raise KbError(f"{n.id} is not an idea")
    n.meta["status"] = "done"
    n.meta["updated"] = today()
    kb.save(n)
    return n
