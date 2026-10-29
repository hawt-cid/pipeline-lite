"""Deterministic half of the self-improvement loop: `kb retro gather|record`.

The judgment (what to change in which instruction file) lives in the /pipeline:retro skill.
"""
import datetime
import re
import subprocess
from pathlib import Path

from . import hooks
from .store import ID_RE, KbError

OUTPUT_CAP = 12000
FRICTION_CAP = 4000
MIN_FMT = "%Y-%m-%dT%H:%M"


def parse_iso(s):
    try:
        return datetime.datetime.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        raise KbError(f"not an ISO timestamp: {s!r} (e.g. 2026-10-29T14:30:00)")


def minute(s):
    """Normalise an ISO timestamp to the journal's minute resolution."""
    return parse_iso(s).strftime(MIN_FMT)


def last_until(kb):
    """Newest `until` among retro notes (minute resolution), or None."""
    best = None
    for p in kb.files():
        try:
            if "type: retro" not in p.read_text(encoding="utf-8")[:2000]:
                continue
            n = kb.load_file(p)
        except (KbError, OSError, UnicodeDecodeError):
            continue
        u = n.get("until")
        if n.get("type") == "retro" and u:
            try:
                m = minute(u)
            except KbError:
                continue
            if best is None or m > best:
                best = m
    return best


def scope_files(kb):
    """Instruction files in scope that exist (paths only)."""
    home = Path.home()
    cands = [home / ".claude" / "CLAUDE.md"]
    for card in sorted(kb.projects(), key=lambda n: n.id):
        raw = card.get("path")
        if not raw:
            continue
        pp = Path(raw).expanduser()
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(pp))
        cands += [pp / "CLAUDE.md", home / ".claude" / "projects" / slug / "memory" / "MEMORY.md"]
    toolkit = Path(__file__).resolve().parents[2]
    cands += sorted((toolkit / "plugin" / "skills").glob("*/SKILL.md"))
    cands += sorted((home / ".claude" / "skills").glob("*/SKILL.md"))
    out = []
    for c in cands:
        if c.is_file() and str(c) not in out:
            out.append(str(c))
    return out


def _friction_of(ts, project, text):
    """Verbatim friction texts of one journal record."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"- friction(?: \([^)]*\))?: (.*)$", line)
        if m:
            out.append({"date": ts[:10], "project": project, "text": m.group(1)})
    return out


def gather(kb, since=None, project=None, now=None):
    now = now or datetime.datetime.now()
    if since:
        since_m, source = minute(since), "--since"
    else:
        since_m = last_until(kb)
        source = "last retro" if since_m else "none (no retro yet: everything)"
    # >= at minute resolution: an entry in the same minute as `until` may repeat, never get lost
    recs = [r for r in hooks.journal_records(kb.root)
            if (since_m is None or r[0] >= since_m) and (project is None or r[1] == project)]
    recs.reverse()  # newest first
    counts = {}
    for _, proj, _ in recs:
        counts[proj or "-"] = counts.get(proj or "-", 0) + 1
    friction = [f for ts, pr, tx in recs for f in _friction_of(ts, pr or "-", tx)]
    return {"gathered_at": now.strftime("%Y-%m-%dT%H:%M:%S"), "since": since_m, "since_source": source,
            "project": project, "counts": counts, "friction": friction,
            "entries": [{"ts": ts, "project": pr, "text": tx} for ts, pr, tx in recs],
            "files": scope_files(kb)}


def _fit(items, render, budget):
    """Keep leading items while they fit; returns (rendered list, omitted count)."""
    out, used = [], 0
    for i, it in enumerate(items):
        r = render(it)
        if used + len(r) + 1 > budget:
            return out, len(items) - i
        out.append(r)
        used += len(r) + 1
    return out, 0


def render(g):
    """Text report, <= OUTPUT_CAP chars; says what was cut."""
    head = [f"gathered_at: {g['gathered_at']}",
            f"since: {g['since'] or '-'} ({g['since_source']})" + (f"  project: {g['project']}" if g["project"] else ""),
            f"entries: {len(g['entries'])}  " + ", ".join(f"{p}: {c}" for p, c in sorted(g["counts"].items()))]
    fr, fr_omit = _fit(g["friction"], lambda f: f"- {f['date']} {f['project']}: {f['text']}", FRICTION_CAP)
    head += [f"\n## Friction ({len(g['friction'])})"] + (fr or ["(none)"])
    if fr_omit:
        head.append(f"... {fr_omit} older friction lines omitted")
    tail = ["\n## Instruction files in scope (existing)"] + (g["files"] or ["(none)"])
    head_s, tail_s = "\n".join(head), "\n".join(tail)
    reserve = len("\n\n## Entries (newest first)\n... 99999 older entries omitted\n")
    budget = OUTPUT_CAP - len(head_s) - len(tail_s) - reserve
    shown, omit = _fit(g["entries"], lambda e: e["text"] + "\n", max(budget, 0))
    mid = ["\n## Entries (newest first)"] + ([("\n".join(shown)).rstrip("\n")] if shown else ["(none)"])
    if omit:
        mid.append(f"... {omit} older entries omitted")
    return head_s + "\n" + "\n".join(mid) + "\n" + tail_s + "\n"


def to_json(g):
    """JSON form with the same cap: older entries are dropped and counted."""
    out = dict(g)
    base = len(str(g["files"])) + len(str(g["friction"])) + 600
    shown, omit = _fit(g["entries"], lambda e: e["text"] + "\n", max(OUTPUT_CAP - base, 0))
    out["entries"] = g["entries"][:len(shown)]
    out["entries_omitted"] = omit
    return out


# ---- record -----------------------------------------------------------------

def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def record(kb, until, accepted, rejected, body):
    when = parse_iso(until)
    d = when.date().isoformat()
    nid, n = f"retro-{d}", 1
    while (kb.notes_dir / f"{nid}.md").exists():
        n += 1
        nid = f"retro-{d}-{n}"
    assert ID_RE.match(nid)
    note = kb.create("retro", f"Retro {d}", nid=nid, body=body,
                     extra={"until": when.isoformat(timespec="seconds"),
                            "accepted": accepted, "rejected": rejected})
    return note, commit(kb, note, d)


def commit(kb, note, date):
    """Commit journal/ + the note in the kb repo (local only). Returns a status line."""
    root = kb.root
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        return "kb is not a git repo: skipped commit"
    paths = [f"notes/{note.id}.md"] + (["journal"] if (root / "journal").is_dir() else [])
    r = _git(root, "add", "--", *paths)
    if r.returncode == 0:
        r = _git(root, "commit", "-q", "-m", f"retro {date}", "--", *paths)
    if r.returncode != 0:
        return f"commit failed: {(r.stderr or r.stdout).strip()}"
    return f"committed in kb: retro {date}"
