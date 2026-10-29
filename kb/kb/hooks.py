"""Session lifecycle hooks: `kb hook session-start|stop|session-end` (stdin = hook JSON).

Hooks never fail a session: run() always returns 0 and logs exceptions to
<kb>/.state/hook-errors.log. No network, no LLM, never reads the transcript.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CARD_CAP, HANDOFF_CAP, JOURNAL_CAP, TOTAL_CAP = 2500, 3000, 1500, 8000
SUMMARY_CAP = 500
FRICTION_HINT = ('If the user corrects you or a tool fails in a way worth remembering, '
                 'log it with: kb journal friction "<text>".')
EVENTS = ("session-start", "stop", "session-end")


def now():
    return datetime.datetime.now()


def cap(text, limit):
    if len(text) <= limit:
        return text
    marker = "\n[...truncated]"
    return text[:max(0, limit - len(marker))] + marker


def git(cwd, *args):
    """Stdout of a git command, or None on failure."""
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def one_line(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ---- paths and state ---------------------------------------------------------

def ensure_state_root(kb_root):
    d = Path(kb_root) / ".state"
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")
    return d


def state_dir(kb_root):
    d = ensure_state_root(kb_root) / "sessions"
    d.mkdir(exist_ok=True)
    return d


def state_path(kb_root, sid):
    return state_dir(kb_root) / (re.sub(r"[^A-Za-z0-9_.-]", "_", sid) + ".json")


def load_state(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def save_state(p, st):
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False))
    os.replace(tmp, p)


STALE_DELETE_SECS = 2 * 86400   # SessionEnd never fired (crash, kill)
STALE_FRICTION_SECS = 12 * 3600


def prune_stale(kb_root):
    cutoff = time.time() - STALE_DELETE_SECS
    for p in state_dir(kb_root).glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def log_error(kb_root, event, exc):
    try:
        with open(ensure_state_root(kb_root) / "hook-errors.log", "a") as f:
            f.write(f"{now().isoformat(timespec='seconds')} {event} {type(exc).__name__}: {one_line(str(exc))}\n")
    except OSError:
        pass


# ---- journal -------------------------------------------------------------------

def journal_dir(kb_root):
    return Path(kb_root) / "journal"


def append_journal(kb_root, text, when=None):
    d = journal_dir(kb_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{(when or now()).date().isoformat()}.md"
    prefix = "\n" if p.exists() and p.stat().st_size else ""
    with open(p, "a", encoding="utf-8") as f:
        f.write(prefix + text.rstrip("\n") + "\n")


def journal_records(kb_root):
    """All entries, oldest first, as (ts, project, text); ts is 'YYYY-MM-DDTHH:MM' (minute
    resolution). Standalone `- friction (` lines are entries of their own."""
    out = []
    d = journal_dir(kb_root)
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        day, cur = p.stem, None

        def flush():
            if cur:
                out.append((cur[0], cur[1], "\n".join(cur[2]).rstrip()))

        for line in p.read_text(encoding="utf-8", errors="replace").split("\n"):
            if line.startswith("## "):
                flush()
                parts = line[3:].split(" · ")
                m = re.match(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d)", parts[0])
                ts = f"{m.group(1)}T{m.group(2)}" if m else f"{day}T00:00"
                cur = (ts, parts[1].strip() if len(parts) > 1 else "", [line])
            elif line.startswith("- friction ("):
                flush()
                m = re.match(r"- friction \((\d\d:\d\d)?[^,)]*, ([^)]*)\)", line)
                cur = (f"{day}T{m.group(1) or '00:00'}" if m else f"{day}T00:00",
                       m.group(2).strip() if m else "", [line])
            elif cur and line.strip():
                cur[2].append(line)
        flush()
    return out


def journal_entries(kb_root, project=None):
    """All entries, oldest first, as (project, text)."""
    out = [(p, t) for _, p, t in journal_records(kb_root)]
    if project is not None:
        out = [e for e in out if e[0] == project]
    return out


def tail(kb_root, project=None, n=5):
    es = journal_entries(kb_root, project)
    return [t for _, t in es[-n:]] if n > 0 else []


def add_friction(kb_root, text, cwd):
    """Attach to the newest live session whose cwd equals, contains or is inside `cwd`;
    else append a standalone line to today's journal. Returns a short description."""
    cwd = os.path.realpath(cwd)
    best, best_m = None, -1
    for p in state_dir(kb_root).glob("*.json"):
        if time.time() - p.stat().st_mtime > STALE_FRICTION_SECS:
            continue
        st = load_state(p)
        if not st or not st.get("cwd"):
            continue
        c = os.path.realpath(st["cwd"])
        rel = c == cwd or c.startswith(cwd.rstrip(os.sep) + os.sep) or cwd.startswith(c.rstrip(os.sep) + os.sep)
        m = p.stat().st_mtime_ns
        if rel and m > best_m:
            best, best_m = (p, st), m
    text = one_line(text)
    if best:
        p, st = best
        st.setdefault("friction", []).append(text)
        save_state(p, st)
        return f"friction noted for session {st.get('session_id', '')[:8]}"
    t = now()
    name = find_card_id(kb_root, cwd) or os.path.basename(cwd) or cwd
    append_journal(kb_root, f"- friction ({t:%H:%M}, {name}): {text}", t)
    return "friction written to journal"


# ---- project / task context --------------------------------------------------

def find_card(kb_root, cwd):
    from .store import Kb
    try:
        return Kb(kb_root).find_project(cwd)
    except Exception:
        return None


def find_card_id(kb_root, cwd):
    c = find_card(kb_root, cwd)
    return c.id if c else ""


def main_worktree(cwd):
    out = git(cwd, "worktree", "list", "--porcelain")
    if out:
        first = out.split("\n", 1)[0]
        if first.startswith("worktree "):
            return first[len("worktree "):]
    return None


def open_tasks(cwd):
    """[(title, status, branch, handoff_text)] of open tasks in the main worktree."""
    root = main_worktree(cwd)
    res = []
    if not root:
        return res
    for p in sorted(Path(root).glob(".tasks/*/TASK.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sm = re.search(r"Status \(internal\):\s*([A-Za-z-]+)", text)
        status = sm.group(1).lower() if sm else ""
        if status in ("done", "parked"):
            continue
        bm = re.search(r"Branch:\s*([^\s·(]+)", text)
        tm = re.search(r"^#\s+(.+)$", text, re.M)
        hm = re.search(r"^## Handoff[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        res.append((tm.group(1).strip() if tm else p.parent.name, status or "?",
                    bm.group(1) if bm else "", hm.group(0).strip() if hm else ""))
    return res


# ---- events --------------------------------------------------------------------

def session_start(kb_root, data):
    cwd = data.get("cwd") or os.getcwd()
    sid = data.get("session_id") or ""
    in_git = git(cwd, "rev-parse", "--show-toplevel") is not None
    card = find_card(kb_root, cwd)
    if not in_git and not card:
        return None
    branch = (git(cwd, "symbolic-ref", "--short", "-q", "HEAD") or "") if in_git else ""
    head = (git(cwd, "rev-parse", "HEAD") or "") if in_git else ""
    pid = card.id if card else ""
    if sid:
        p = state_path(kb_root, sid)
        prune_stale(kb_root)
        if not p.exists():
            save_state(p, {"session_id": sid, "cwd": cwd, "project": pid, "branch": branch,
                           "start_head": head, "git": in_git, "started_at": time.time(),
                           "turns": 0, "last_assistant_message": ""})
    if not card:
        hint = ("This git repo has no project card in the kb. "
                f'Register it with: kb project add <name> --path "{cwd}"')
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": hint},
                "systemMessage": "pipeline: no project card for this repo (kb project add)"}
    parts = ["## Project card\n" + cap(card.text().strip(), CARD_CAP)]
    tasks = open_tasks(cwd) if in_git else []
    if tasks:
        full = [f"### {t} ({s}, this branch)\n{h}" for t, s, b, h in tasks if branch and b == branch and h]
        short = [f"- {t} ({s})" for t, s, b, h in tasks if not (branch and b == branch and h)]
        parts.append("## Open tasks\n" + cap("\n".join(full + short), HANDOFF_CAP))
    entries = tail(kb_root, pid, 5)
    if entries:
        parts.append("## Recent journal\n" + cap("\n\n".join(entries), JOURNAL_CAP))
    parts.append(FRICTION_HINT)
    ctx = cap("\n\n".join(parts), TOTAL_CAP)
    msg = (f"pipeline: {pid} · {len(tasks)} open task{'' if len(tasks) == 1 else 's'}"
           f" · {len(entries)} journal entr{'y' if len(entries) == 1 else 'ies'}")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx},
            "systemMessage": msg}


def stop(kb_root, data):
    sid = data.get("session_id")
    if not sid:
        return None
    p = state_path(kb_root, sid)
    st = load_state(p)
    if st is None:
        return None
    st["turns"] = st.get("turns", 0) + 1
    st["last_assistant_message"] = (data.get("last_assistant_message") or "")[:SUMMARY_CAP]
    save_state(p, st)
    return None


def fmt_duration(sec):
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def cap_list(items, limit, sep):
    shown = items[:limit]
    more = len(items) - len(shown)
    return sep.join(shown) + (f" (+{more} more)" if more > 0 else "")


def session_end(kb_root, data):
    sid = data.get("session_id")
    if not sid:
        return None
    p = state_path(kb_root, sid)
    st = load_state(p)
    if st is None:
        return None
    try:
        if st.get("turns", 0) == 0 or not (st.get("git") or st.get("project")):
            return None
        cwd = st.get("cwd") or data.get("cwd") or ""
        start = st.get("start_head") or ""
        t = now()
        label = st.get("project") or os.path.basename(cwd.rstrip("/")) or cwd
        lines = [f"## {t:%Y-%m-%d %H:%M} · {label} · {st.get('branch') or '-'} · {sid[:8]}",
                 f"- cwd: {cwd}",
                 f"- reason: {data.get('reason', '')}",
                 f"- turns: {st.get('turns', 0)}",
                 f"- duration: {fmt_duration(time.time() - st.get('started_at', time.time()))}"]
        if st.get("git") and os.path.isdir(cwd):
            files = []
            for line in (git(cwd, "status", "--porcelain") or "").split("\n"):
                if len(line) > 3:
                    files.append(line[3:].split(" -> ")[-1].strip('"'))
            if start:
                files += (git(cwd, "diff", "--name-only", f"{start}..HEAD") or "").split("\n")
            files = list(dict.fromkeys(f for f in files if f))
            if files:
                lines.append(f"- changed: {cap_list(files, 10, ', ')}")
            if start:
                commits = [c for c in (git(cwd, "log", "--oneline", f"{start}..HEAD") or "").split("\n") if c]
                if commits:
                    lines.append(f"- commits: {cap_list(commits, 5, '; ')}")
        summary = one_line(st.get("last_assistant_message"))
        if summary:
            lines.append(f"- summary: {summary}")
        for f in st.get("friction", []):
            lines.append(f"- friction: {f}")
        append_journal(kb_root, "\n".join(lines), t)
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    return None


HANDLERS = {"session-start": session_start, "stop": stop, "session-end": session_end}


def run(event, kb_flag=None, stdin=None):
    """Entry point for `kb hook <event>`. Always returns 0."""
    root = None
    try:
        root = Path(kb_flag or os.environ.get("KB_DIR") or "~/agent-kb").expanduser()
        if not root.is_dir():
            return 0
        raw = (stdin if stdin is not None else sys.stdin).read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
        out = HANDLERS[event](root, data)
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception as e:  # never fail a session
        if root is not None and root.is_dir():
            log_error(root, event, e)
    return 0
