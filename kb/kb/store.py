"""Note files, frontmatter, references and the search index."""
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

TYPES = ("note", "project", "idea", "decision", "howto")
STATUSES = ("active", "paused", "done")
BASE_FIELDS = ("id", "type", "title", "project", "tags", "refs", "status", "created", "updated")
PROJECT_FIELDS = ("path", "stack", "commands")
LIST_FIELDS = ("tags", "refs", "commands")
BLOCK_LISTS = ("commands",)  # written as `- item` lines; parsed either way
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")


class KbError(Exception):
    pass


# ---- ids -------------------------------------------------------------------

def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def resolve_id(raw):
    """proj://name -> proj-name, kb://id -> id, anything else unchanged."""
    raw = raw.strip()
    if raw.startswith("proj://"):
        return "proj-" + raw[len("proj://"):]
    if raw.startswith("kb://"):
        return raw[len("kb://"):]
    return raw


# ---- frontmatter (restricted YAML subset) ----------------------------------
# key: value          scalar (double-quoted JSON string when it needs escaping)
# key: [a, b]         inline list
# key:                followed by `  - item` lines: block list
# Anything else between the `---` fences is a parse error.

def _scalar(s):
    s = s.strip()
    if s.startswith('"'):
        try:
            v = json.loads(s)
        except ValueError:
            raise KbError(f"bad quoted string: {s}")
        if not isinstance(v, str):
            raise KbError(f"bad quoted string: {s}")
        return v
    return s


def _split_inline(s):
    items, buf, quoted, esc = [], "", False, False
    for ch in s:
        if quoted:
            buf += ch
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
            buf += ch
        elif ch == ",":
            items.append(buf)
            buf = ""
        else:
            buf += ch
    if quoted:
        raise KbError(f"unterminated quote in list: [{s}]")
    items.append(buf)
    return [_scalar(i) for i in items if i.strip()]


def _quote(s, inline=False, block=False):
    bad = s != s.strip() or not s or "\n" in s or s.startswith('"')
    if inline:
        bad = bad or any(c in s for c in ',[]"')
    elif not block:
        bad = bad or s.startswith("[")
    return json.dumps(s, ensure_ascii=False) if bad else s


def parse_frontmatter(text):
    """Return (meta dict, body). Raises KbError on anything malformed."""
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        raise KbError("no frontmatter (file must start with ---)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    except StopIteration:
        raise KbError("frontmatter is not closed with ---")
    meta, key = {}, None
    for n, line in enumerate(lines[1:end], 2):
        if not line.strip():
            continue
        m = re.match(r"^\s+-\s?(.*)$", line)
        if m:
            if key is None or not isinstance(meta[key], list):
                raise KbError(f"line {n}: list item without a list key")
            meta[key].append(_scalar(m.group(1)))
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*))?$", line.rstrip())
        if not m:
            raise KbError(f"line {n}: cannot parse: {line}")
        key, val = m.group(1), (m.group(2) or "").strip()
        if key in meta:
            raise KbError(f"line {n}: duplicate key {key}")
        if val.startswith("["):
            if not val.endswith("]"):
                raise KbError(f"line {n}: unterminated list")
            meta[key] = _split_inline(val[1:-1])
            key = None
        elif val == "" and key in LIST_FIELDS:
            meta[key] = []  # items may follow as a block
        else:
            meta[key] = _scalar(val)
            key = None
    for f in LIST_FIELDS:
        if f in meta and not isinstance(meta[f], list):
            meta[f] = [meta[f]] if meta[f] else []
    body = "\n".join(lines[end + 1:]).strip("\n")
    return meta, body


def format_note(meta, body):
    keys = [k for k in BASE_FIELDS + PROJECT_FIELDS if k in meta]
    keys += [k for k in meta if k not in keys]
    out = ["---"]
    for k in keys:
        v = meta[k]
        if isinstance(v, list):
            if k in BLOCK_LISTS and v:
                out.append(f"{k}:")
                out += [f"  - {_quote(i, block=True)}" for i in v]
            else:
                out.append(f"{k}: [{', '.join(_quote(i, inline=True) for i in v)}]")
        else:
            v = str(v)
            out.append(f"{k}: {_quote(v)}" if v else f"{k}:")
    out.append("---")
    text = "\n".join(out) + "\n"
    body = body.strip("\n")
    return text + ("\n" + body + "\n" if body else "")


# ---- store -----------------------------------------------------------------

def today():
    return datetime.date.today().isoformat()


def find_kb(flag=None):
    kb = flag or os.environ.get("KB_DIR") or "~/agent-kb"
    kb = Path(kb).expanduser()
    if not kb.is_dir():
        raise KbError(f"kb directory not found: {kb} (set --kb or KB_DIR, or run install.sh)")
    return kb


class Note:
    def __init__(self, path, meta, body):
        self.path, self.meta, self.body = path, meta, body

    @property
    def id(self):
        return self.meta.get("id", "")

    def get(self, key, default=""):
        return self.meta.get(key, default)

    def refs(self):
        """Frontmatter refs plus body [[id]] links, normalised and de-duplicated."""
        out = []
        for r in list(self.meta.get("refs", [])) + LINK_RE.findall(self.body):
            r = resolve_id(r)
            if r and r not in out:
                out.append(r)
        return out

    def text(self):
        return format_note(self.meta, self.body)

    def to_dict(self):
        return {**self.meta, "body": self.body}


class Kb:
    def __init__(self, root):
        self.root = Path(root)
        self.notes_dir = self.root / "notes"

    def files(self):
        return sorted(self.notes_dir.glob("*.md")) if self.notes_dir.is_dir() else []

    def load_file(self, path):
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        return Note(path, meta, body)

    def load_all(self):
        """(notes, errors): errors is a list of (path, message)."""
        notes, errors = [], []
        for p in self.files():
            try:
                notes.append(self.load_file(p))
            except (KbError, UnicodeDecodeError, OSError) as e:
                errors.append((p, str(e)))
        return notes, errors

    def get(self, raw_id):
        nid = resolve_id(raw_id)
        p = self.notes_dir / f"{nid}.md"
        if not ID_RE.match(nid) or not p.is_file():
            raise KbError(f"no such note: {nid}")
        return self.load_file(p)

    def save(self, note, exclusive=False):
        self.notes_dir.mkdir(exist_ok=True)
        tmp = note.path.with_name(f".{note.path.name}.tmp")
        tmp.write_text(note.text(), encoding="utf-8")
        if exclusive:
            try:
                os.link(tmp, note.path)  # fails if it exists
            except FileExistsError:
                raise KbError(f"note already exists: {note.id}")
            finally:
                tmp.unlink()
        else:
            os.replace(tmp, note.path)

    def create(self, type_, title, nid=None, project="", tags=(), refs=(), body="", extra=None):
        if type_ not in TYPES:
            raise KbError(f"unknown type '{type_}' (one of: {', '.join(TYPES)})")
        title = " ".join(title.split())
        if not title:
            raise KbError("title is empty")
        slug = slugify(title)
        nid = resolve_id(nid) if nid else ("proj-" + slug if type_ == "project" else slug)
        if not ID_RE.match(nid):
            raise KbError(f"invalid id '{nid}' (lowercase letters, digits, '-'; must not start with '-')")
        d = today()
        meta = {"id": nid, "type": type_, "title": title,
                "project": project or (slug if type_ == "project" else ""),
                "tags": list(tags), "refs": [resolve_id(r) for r in refs],
                "status": "", "created": d, "updated": d}
        if type_ == "project":
            meta.update({"status": "active", "path": "", "stack": "", "commands": []})
        for k, v in (extra or {}).items():
            if v not in (None, "", []):
                meta[k] = v
        note = Note(self.notes_dir / f"{nid}.md", meta, body)
        self.save(note, exclusive=True)
        return note

    def backlinks(self, nid, notes=None):
        notes = notes if notes is not None else self.load_all()[0]
        return [n for n in notes if nid in n.refs() and n.id != nid]

    # ---- projects ----------------------------------------------------------

    def projects(self):
        out = []
        for p in self.files():
            try:  # cheap pre-filter: skip files that cannot be project cards
                if "type: project" not in p.read_text(encoding="utf-8")[:2000]:
                    continue
                n = self.load_file(p)
            except (KbError, OSError, UnicodeDecodeError):
                continue
            if n.get("type") == "project":
                out.append(n)
        return out

    def find_project(self, directory):
        """Card whose path is `directory` or an ancestor; longest path wins."""
        here = os.path.realpath(os.path.expanduser(directory))
        best, best_len = None, -1
        for n in self.projects():
            raw = n.get("path")
            if not raw:
                continue
            p = os.path.realpath(os.path.expanduser(raw))
            if (here == p or here.startswith(p.rstrip(os.sep) + os.sep)) and len(p) > best_len:
                best, best_len = n, len(p)
        return best

    # ---- search ------------------------------------------------------------

    def _signature(self):
        parts = []
        for p in self.files():
            st = p.stat()
            parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        return "\n".join(parts)

    def index(self, force=False):
        """Bring the FTS index up to date; returns an open connection."""
        d = self.root / ".index"
        d.mkdir(exist_ok=True)
        db = sqlite3.connect(d / "kb.sqlite")
        sig = self._signature()
        try:
            db.execute("create table if not exists meta(k text primary key, v text)")
            row = db.execute("select v from meta where k='sig'").fetchone()
            if not force and row and row[0] == sig:
                return db
            db.execute("drop table if exists notes")
            db.execute("create virtual table notes using fts5("
                       "id unindexed, type unindexed, project unindexed, title, tags, body)")
            notes, _ = self.load_all()
            db.executemany("insert into notes values (?,?,?,?,?,?)",
                           [(n.id, n.get("type"), n.get("project"), n.get("title"),
                             " ".join(n.get("tags", [])), n.body) for n in notes])
            db.execute("insert or replace into meta values('sig', ?)", (sig,))
            db.commit()
        except sqlite3.Error:
            db.close()
            raise
        return db

    def search(self, query, type_=None, project=None, limit=10):
        tokens = re.findall(r"\w+", query.lower())
        if not tokens:
            raise KbError("empty search query")
        if not os.environ.get("KB_NO_FTS"):  # KB_NO_FTS=1 forces the rg path
            try:
                return self._search_fts(tokens, type_, project, limit)
            except sqlite3.Error:
                pass  # FTS5 unavailable or index unusable: fall back
        return self._search_rg(tokens, type_, project, limit)

    def _search_fts(self, tokens, type_, project, limit):
        db = self.index()
        try:
            q = " ".join(f'"{t}"*' for t in tokens)
            sql = ("select id, type, title, snippet(notes, -1, '[', ']', '...', 10) "
                   "from notes where notes match ?")
            args = [q]
            if type_:
                sql += " and type = ?"
                args.append(type_)
            if project:
                sql += " and project = ?"
                args.append(project)
            sql += " order by bm25(notes, 0, 0, 0, 10, 5, 1) limit ?"
            args.append(limit)
            return [{"id": i, "type": t, "title": ti, "snippet": " ".join(sn.split())}
                    for i, t, ti, sn in db.execute(sql, args)]
        finally:
            db.close()

    def _search_rg(self, tokens, type_, project, limit):
        if not shutil.which("rg"):
            raise KbError("search needs SQLite FTS5 or ripgrep (rg); neither is available")
        hits = None
        for t in tokens:  # AND semantics: files matching every token
            r = subprocess.run(["rg", "-il", "--fixed-strings", "-e", t, "--", str(self.notes_dir)],
                               capture_output=True, text=True)
            files = {Path(x) for x in r.stdout.splitlines()}
            hits = files if hits is None else hits & files
        scored = []
        for p in hits or ():
            try:
                n = self.load_file(p)
            except (KbError, OSError, UnicodeDecodeError):
                continue
            if (type_ and n.get("type") != type_) or (project and n.get("project") != project):
                continue
            title, tags, body = n.get("title").lower(), " ".join(n.get("tags", [])).lower(), n.body.lower()
            score = sum(10 * title.count(t) + 5 * tags.count(t) + body.count(t) for t in tokens)
            snippet = next((ln.strip() for ln in n.body.splitlines()
                            if any(t in ln.lower() for t in tokens)), n.body[:80])
            scored.append((-score, n.id, {"id": n.id, "type": n.get("type"),
                                          "title": n.get("title"), "snippet": " ".join(snippet.split())[:120]}))
        return [s[2] for s in sorted(scored, key=lambda s: s[:2])][:limit]

    # ---- check -------------------------------------------------------------

    def check(self):
        problems = []
        notes, errors = self.load_all()
        problems += [f"{p.name}: unparseable frontmatter: {m}" for p, m in errors]
        seen = {}
        for n in notes:
            for f in ("id", "type", "title"):
                if not n.get(f):
                    problems.append(f"{n.path.name}: missing required field '{f}'")
            if n.get("type") and n.get("type") not in TYPES:
                problems.append(f"{n.path.name}: unknown type '{n.get('type')}'")
            if n.id and n.id != n.path.stem:
                problems.append(f"{n.path.name}: id '{n.id}' does not match filename")
            elif n.id and not ID_RE.match(n.id):
                problems.append(f"{n.path.name}: invalid id '{n.id}'")
            seen.setdefault(n.id, []).append(n.path.name)
        for nid, names in seen.items():
            if nid and len(names) > 1:
                problems.append(f"duplicate id '{nid}' in {', '.join(names)}")
        for n in notes:
            for r in n.refs():
                if r not in seen:
                    problems.append(f"{n.path.name}: dangling ref '{r}'")
        return problems, len(notes)
