#!/usr/bin/env python3
"""Sync roadmap progress into data/<year>.json, validate, commit, push.

    python update.py              # the whole job, one command
    python update.py --dry-run    # compute, check and print; write nothing

Standard library only. Tests: python -m unittest discover -s tests

THIS REPO IS THE SOURCE OF TRUTH. data/<year>.json is edited here and nowhere
else: manual entries (books, courses, hand-kept projects) by editing the file,
roadmap projects by running this script. There is no import or export step.

WHAT IT READS, AND WHAT IT NEVER TOUCHES. The project list is Roadmap-board's
projects.toml, and each project's steps come from the ROADMAP_STATUS.json it
names. Both are read through sync_goals.py, imported from the Roadmap-board
folder rather than copied, so "progress" means the same thing on the board and
here. Nothing in that folder is written: not the status files, not
projects.toml, not board.html. Bytecode caching is switched off before the
import for the same reason. tests/test_update.py asserts this.

WHAT A SYNC CHANGES. Only entries whose syncKey matches a registered project.
Their 10 segments are set from sync_goals' progress, and a "roadmap" object
carries every step (n, title, status) plus the current step (first not
done) and the next one. Step details are deliberately left out: they are
working notes from private repos, and this file is public. Title, category, subtitle, source and note are set
once, when the entry is created, and are left alone after that, so a rename
made in the data file sticks. Entries without a syncKey are never modified,
and the file keeps its exact layout, so they stay byte-identical.

DATES ARE FIRST-SEEN DATES. Status files record no completion dates. A step
(or segment) that is done now but was not done in the data file as it stood
before this run is dated today; that is the day this script first saw it
done, which is all it can honestly claim. A step that was already done keeps
whatever it had, which for everything done before this script existed is
null. A project seen for the first time gets no dates at all: its done steps
were finished on days nobody recorded, and today would be a guess.

THE LEAK GUARD. The data file is published on GitHub Pages, and roadmap
titles are free text written for a private repo. Before anything is written,
every string in the output is scanned for local paths, email addresses and
key- or token-shaped strings. One hit and nothing is written; the field is
printed instead. Reserved example domains (example.com, .test, ...) are not
real addresses and pass.

FIRST RUN. Until a push has been confirmed once, the script stops before
committing, shows the diff, and asks. The confirmation is remembered in a
file inside .git, so it never reaches the published site.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
# A sibling folder, so no machine-specific path is written into this file,
# which is itself public in the GitHub repository.
DEFAULT_ROADMAP_DIR = REPO.parent / "Roadmap-board"
VALIDATOR = REPO / "scripts" / "validate-data.mjs"

STEP_COUNT = 10
NEW_ENTRY_CATEGORY = "project"
FIRST_PUSH_MARKER = "goals-dashboard-first-push-confirmed"
DIFF_LINE_MAX = 200


# --------------------------------------------------------------------------
# Reading the roadmap side (all of it through sync_goals)
# --------------------------------------------------------------------------

def load_sync_goals(roadmap_dir: Path):
    """Import sync_goals.py from the Roadmap-board folder. Its import runs no
    code beyond definitions; bytecode writing is off so the import does not
    drop a __pycache__ into a folder this script promises not to write to."""
    sys.dont_write_bytecode = True
    folder = str(Path(roadmap_dir).resolve())
    if not (Path(folder) / "sync_goals.py").exists():
        sys.exit(f"sync_goals.py not found in {folder}. Pass --roadmap-dir.")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return importlib.import_module("sync_goals")


def read_steps(sg, entry: dict) -> list[dict] | None:
    """The status file's steps, read through sync_goals.read_status (which
    opens it "r"). None when the file is missing or unreadable."""
    raw = str(entry.get("status_json", "")).strip()
    if not raw:
        return None
    data = sg.read_status(Path(raw).expanduser())
    if data is None:
        return None
    return [s for s in (data.get("steps") or []) if isinstance(s, dict)]


def is_done(sg, status) -> bool:
    return str(status or "").strip().lower() in sg.DONE_STATUSES


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _step_number(value, position: int):
    """The step's own n. A step without a usable one gets its 1-based
    position, so the viewer and the validator always have something to show."""
    if isinstance(value, bool) or value is None:
        return position
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    return position


# --------------------------------------------------------------------------
# Building one synced entry
# --------------------------------------------------------------------------

def build_segments(progress: int, prev_steps, today: str) -> list[dict]:
    """The 10 segments for a progress of 0-10. A segment newly done since the
    previous file is dated today; one already done keeps its date; with no
    previous entry there is nothing to compare against, so no dates."""
    out = []
    for i in range(STEP_COUNT):
        if i >= progress:
            out.append({"done": False, "date": None})
            continue
        before = None
        if isinstance(prev_steps, list) and i < len(prev_steps) and isinstance(prev_steps[i], dict):
            before = prev_steps[i]
        if prev_steps is None:
            date = None
        elif before is not None and before.get("done") is True:
            date = before.get("date")
        else:
            date = today
        out.append({"done": True, "date": date})
    return out


def build_roadmap(sg, steps: list[dict], prev_roadmap, today: str) -> dict:
    """Every step, plus the current (first not done) and next (second not
    done) steps, in the order the status file lists them.

    firstSeenDone follows the same rule as segment dates. prev_roadmap None
    means this script has never synced the project, so nothing is dated. A
    step absent from the previous roadmap that is done now was, by definition,
    not done in the previous file, so it is dated today."""
    previous = None
    if isinstance(prev_roadmap, dict):
        previous = {}
        for s in prev_roadmap.get("steps") or []:
            if isinstance(s, dict):
                previous.setdefault(json.dumps(s.get("n")), s)

    out = []
    for position, s in enumerate(steps, start=1):
        n = _step_number(s.get("n"), position)
        status = _text(s.get("status")).strip()
        seen = None
        if is_done(sg, status) and previous is not None:
            before = previous.get(json.dumps(n))
            if before is not None and is_done(sg, before.get("status")):
                seen = before.get("firstSeenDone")
            else:
                seen = today
        out.append({
            "n": n,
            "title": _text(s.get("title")).strip(),
            "status": status,
            "firstSeenDone": seen,
        })

    pending = [s for s in out if not is_done(sg, s["status"])]

    def ref(i):
        return {"n": pending[i]["n"], "title": pending[i]["title"]} if i < len(pending) else None

    return {"current": ref(0), "next": ref(1), "steps": out}


def _new_id(key: str, taken: set[str]) -> str:
    base = f"sync-{key}"
    candidate, k = base, 2
    while candidate in taken:
        candidate, k = f"{base}-{k}", k + 1
    return candidate


@dataclass
class ProjectReport:
    key: str
    title: str = ""
    state: str = "unchanged"  # new | changed | unchanged | skipped | unregistered
    progress: int = 0
    current: dict | None = None
    newly_done: int = 0
    note: str = ""


def sync(data: dict, registry: list[dict], sg, today: str) -> tuple[dict, list[ProjectReport]]:
    """Returns a new data dict and a per-project report. `data` is not
    mutated; entries without a syncKey are carried over as the same values."""
    out = copy.deepcopy(data)
    projects = out.setdefault("projects", [])
    by_key: dict[str, dict] = {}
    for p in projects:
        if isinstance(p, dict) and isinstance(p.get("syncKey"), str) and p["syncKey"]:
            by_key.setdefault(p["syncKey"], p)
    taken = {p.get("id") for p in projects if isinstance(p, dict)}

    reports: list[ProjectReport] = []
    registered: set[str] = set()
    for entry in registry:
        summary = sg.project_entry(entry)
        key = summary["syncKey"]
        registered.add(key)
        prev = by_key.get(key)
        report = ProjectReport(key=key, title=(prev or summary).get("title", key))

        steps = read_steps(sg, entry)
        if steps is None:
            # Zeroing the entry here would untick its segments and then date
            # them all "today" once the file came back. Leave it as it is.
            report.state = "skipped"
            report.note = "status file missing or unreadable; entry left as it was"
            if prev is not None:
                report.progress = sum(1 for s in prev.get("steps", []) if s.get("done"))
                report.current = (prev.get("roadmap") or {}).get("current")
            reports.append(report)
            continue

        segments = build_segments(summary["progress"], prev["steps"] if prev else None, today)
        roadmap = build_roadmap(sg, steps, prev.get("roadmap") if prev else None, today)
        report.progress = summary["progress"]
        report.current = roadmap["current"]
        prev_roadmap = prev.get("roadmap") if prev else None
        if isinstance(prev_roadmap, dict):
            was_done = {json.dumps(s.get("n")) for s in prev_roadmap.get("steps") or []
                        if isinstance(s, dict) and is_done(sg, s.get("status"))}
            report.newly_done = sum(1 for s in roadmap["steps"]
                                    if is_done(sg, s["status"]) and json.dumps(s["n"]) not in was_done)

        if prev is None:
            new_id = _new_id(key, taken)
            taken.add(new_id)
            prev = {
                "id": new_id,
                "syncKey": key,
                "category": NEW_ENTRY_CATEGORY,
                "title": summary["title"],
                "subtitle": summary["subtitle"],
                "source": sg.SOURCE_LABEL,
                "note": "",
                "steps": segments,
                "roadmap": roadmap,
                "syncedAt": today,
            }
            projects.append(prev)
            by_key[key] = prev
            report.state = "new"
        elif prev.get("steps") != segments or prev.get("roadmap") != roadmap:
            prev["steps"] = segments
            prev["roadmap"] = roadmap
            prev["syncedAt"] = today
            report.state = "changed"
        reports.append(report)

    for key, p in by_key.items():
        if key not in registered:
            reports.append(ProjectReport(
                key=key, title=p.get("title", key), state="unregistered",
                progress=sum(1 for s in p.get("steps", []) if isinstance(s, dict) and s.get("done")),
                current=(p.get("roadmap") or {}).get("current"),
                note="not in projects.toml; entry left as it was",
            ))

    if out != data:
        out["updatedAt"] = today
    return out, reports


# --------------------------------------------------------------------------
# Leak guard
# --------------------------------------------------------------------------

# RFC 2606 / 6761 names that cannot be anyone's address.
RESERVED_EMAIL_DOMAIN = re.compile(
    r"(?:^|\.)(?:example\.(?:com|org|net)|example|test|invalid|localhost)$", re.I
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")

LEAK_PATTERNS = [
    ("local path (drive letter)", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")),
    ("local path (network share)", re.compile(r"\\\\[A-Za-z0-9._$-]+\\")),
    ("local path (\\Users\\)", re.compile(r"\\Users\\", re.I)),
    ("local path (/Users/)", re.compile(r"/Users/")),
    ("local path (/home/)", re.compile(r"/home/")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY")),
    ("token", re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}"
        r"|xox[abposr]-[A-Za-z0-9-]{10,}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,})"
    )),
    ("private key (WIF)", re.compile(r"\b5[HJK][1-9A-HJ-NP-Za-km-z]{49}\b")),
    ("secret assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|access[_-]?token|auth[_-]?token|password|passwd"
        r"|private[_-]?key|posting[_-]?key|active[_-]?key)\b\s*[:=]\s*['\"]?[^\s'\",;]{8,}"
    )),
]
# Long runs mixing upper, lower and digits look like keys. Commit SHAs (hex,
# one case) and snake_case identifiers do not mix all three, so they pass.
BLOB = re.compile(r"[A-Za-z0-9+/_=-]{32,}")


@dataclass
class Leak:
    where: str
    kind: str
    match: str
    context: str = ""


def _mask(kind: str, text: str) -> str:
    if kind.startswith("local path") or kind == "email address":
        return text
    return text[:4] + "…" if len(text) > 4 else text


def scan_string(value: str) -> list[tuple[str, str, int, int]]:
    hits = []
    for m in EMAIL.finditer(value):
        if not RESERVED_EMAIL_DOMAIN.search(m.group(1)):
            hits.append(("email address", m.group(0), m.start(), m.end()))
    for kind, pattern in LEAK_PATTERNS:
        for m in pattern.finditer(value):
            hits.append((kind, m.group(0), m.start(), m.end()))
    for m in BLOB.finditer(value):
        t = m.group(0)
        if re.search(r"[A-Z]", t) and re.search(r"[a-z]", t) and re.search(r"\d", t):
            hits.append(("key-like string", t, m.start(), m.end()))
    return hits


def find_leaks(obj, where: str = "") -> list[Leak]:
    """Every string in obj (keys included) that looks private."""
    leaks: list[Leak] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{where}.{k}" if where else str(k)
            leaks += find_leaks(k, path + " (key)")
            leaks += find_leaks(v, path)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaks += find_leaks(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        for kind, text, start, end in scan_string(obj):
            ctx = obj[max(0, start - 30):min(len(obj), end + 30)].replace("\n", " ")
            if kind not in ("email address",) and not kind.startswith("local path"):
                ctx = ctx.replace(text, _mask(kind, text))
            leaks.append(Leak(where, kind, _mask(kind, text), ctx))
    return leaks


# --------------------------------------------------------------------------
# The data file
# --------------------------------------------------------------------------

class NotCanonical(Exception):
    pass


def dump(data: dict, like: str) -> str:
    """Serialise the way the file is already laid out: 2-space JSON, raw
    UTF-8, the same line endings and the same trailing newline (or none)."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if like.endswith("\n"):
        text += "\n"
    if "\r\n" in like:
        text = text.replace("\n", "\r\n")
    return text


@dataclass
class Outcome:
    before: str
    after: str
    data: dict
    reports: list[ProjectReport] = field(default_factory=list)
    leaks: list[Leak] = field(default_factory=list)
    written: bool = False

    @property
    def changed(self) -> bool:
        return self.after != self.before


def update_file(data_path: Path, registry: list[dict], sg, today: str,
                write: bool = True, reformat: bool = False) -> Outcome:
    """Sync one year file. Nothing is written when the leak guard finds
    anything, when write is False, or when the output equals the input."""
    before = data_path.read_bytes().decode("utf-8")
    data = json.loads(before)
    if not reformat and dump(data, before) != before:
        raise NotCanonical(
            f"{data_path.name} is not laid out the way this script writes JSON "
            "(2-space indent, raw UTF-8). Rewriting it would change the bytes of "
            "manual entries too. Run once with --reformat if that is intended."
        )
    new_data, reports = sync(data, registry, sg, today)
    after = dump(new_data, before)
    outcome = Outcome(before, after, new_data, reports, find_leaks(new_data))
    if outcome.leaks or not write or not outcome.changed:
        return outcome
    data_path.write_bytes(after.encode("utf-8"))
    outcome.written = True
    return outcome


# --------------------------------------------------------------------------
# Output, validation, git
# --------------------------------------------------------------------------

def _ref(step) -> str:
    return f"{step['n']}. {step['title']}" if step else "-"


def print_summary(outcome: Outcome, rel: str) -> None:
    print(f"{rel}:")
    width = max([len(r.title) for r in outcome.reports] + [8])
    for r in outcome.reports:
        extra = f"  (+{r.newly_done} step{'s' if r.newly_done != 1 else ''} done)" if r.newly_done else ""
        flag = "" if r.state in ("changed", "unchanged") else f"  [{r.state}: {r.note or 'added'}]"
        print(f"  {r.title:<{width}}  {r.progress * 10:>3}%  now: {_ref(r.current)}{extra}{flag}")


def print_leaks(leaks: list[Leak]) -> None:
    print(f"LEAK GUARD: {len(leaks)} string(s) look private. Nothing was written.", file=sys.stderr)
    for leak in leaks:
        print(f"  {leak.where}\n    {leak.kind}: {leak.match!r}\n    …{leak.context}…", file=sys.stderr)
    print(
        "Fix the source text (for roadmap fields, in that project's own roadmap "
        "source, then regenerate its status file) and run again.",
        file=sys.stderr,
    )


def run_validator() -> bool:
    node = shutil.which("node")
    if node is None:
        print("node is not on PATH; cannot run scripts/validate-data.mjs.", file=sys.stderr)
        return False
    r = subprocess.run([node, str(VALIDATOR)], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode == 0


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8")


def show_diff(rel: str) -> None:
    diff = git("diff", "HEAD", "--", rel).stdout or f"(new file {rel})\n"
    long_lines = 0
    for line in diff.splitlines():
        if len(line) > DIFF_LINE_MAX:
            long_lines += 1
            line = f"{line[:DIFF_LINE_MAX]}… (+{len(line) - DIFF_LINE_MAX} chars)"
        print(line)
    if long_lines:
        print(f"\n({long_lines} long line(s) shortened. Full diff: git diff {rel})")
    print(git("diff", "HEAD", "--stat", "--", rel).stdout)


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        print()
        return False


def commit_message(today: str, outcome: Outcome) -> str:
    lines = [f"Update goals data ({today})", ""]
    for r in outcome.reports:
        lines.append(f"- {r.title}: {r.progress * 10}%, now {_ref(r.current)}")
    return "\n".join(lines) + "\n"


def commit_and_push(rel: str, today: str, outcome: Outcome) -> int:
    if not git("status", "--porcelain", "--", rel).stdout.strip():
        print("No data changes since the last commit. Nothing to commit.")
        return 0

    marker = REPO / git("rev-parse", "--git-path", FIRST_PUSH_MARKER).stdout.strip()
    first_run = not marker.exists()
    if first_run:
        print("\nFirst run: here is what would be committed and pushed.\n")
        show_diff(rel)
        if not confirm("Commit and push this? [y/N] "):
            print(f"Stopped before committing. {rel} is written but not committed; "
                  "run update.py again to be asked again.")
            return 3

    for step in (("add", "--", rel), ("commit", "-m", commit_message(today, outcome), "--", rel)):
        r = git(*step)
        if r.returncode != 0:
            print(f"git {step[0]} failed:\n{r.stdout}{r.stderr}", file=sys.stderr)
            return 1
    print(git("log", "-1", "--format=Committed %h %s").stdout.strip())

    r = git("push")
    if r.returncode != 0:
        print(f"Committed locally, but git push failed:\n{r.stderr}Run git push when fixed.", file=sys.stderr)
        return 1
    if first_run:
        marker.write_text(today + "\n", encoding="utf-8")
    print("Pushed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync roadmap progress into the goals data, then commit and push.")
    ap.add_argument("--roadmap-dir", type=Path, default=DEFAULT_ROADMAP_DIR,
                    help="Folder holding projects.toml and sync_goals.py (default: ../Roadmap-board).")
    ap.add_argument("--dry-run", action="store_true", help="Print the summary and any leaks; write nothing.")
    ap.add_argument("--reformat", action="store_true", help="Allow rewriting a file not in the canonical layout.")
    ap.add_argument("--today", default=None, help="ISO date, for testing only.")
    args = ap.parse_args(argv)

    today = args.today or dt.date.today().isoformat()
    data_path = DATA_DIR / f"{today[:4]}.json"
    rel = data_path.relative_to(REPO).as_posix()
    if not data_path.exists():
        print(f"{rel} does not exist. Start the year by creating it and listing the year in "
              "data/index.json (see README), then run again.", file=sys.stderr)
        return 1

    sg = load_sync_goals(args.roadmap_dir)
    registry = sg.load_registry(Path(args.roadmap_dir) / "projects.toml")
    try:
        outcome = update_file(data_path, registry, sg, today, write=not args.dry_run, reformat=args.reformat)
    except NotCanonical as e:
        print(e, file=sys.stderr)
        return 1

    if outcome.leaks:
        print_leaks(outcome.leaks)
        return 2
    print_summary(outcome, rel)
    if args.dry_run:
        print("Dry run: nothing written." + (" (The sync would change the file.)" if outcome.changed else ""))
        return 0

    if not run_validator():
        if outcome.written:
            data_path.write_bytes(outcome.before.encode("utf-8"))
            print(f"Validation failed; {rel} restored to how it was.", file=sys.stderr)
        return 1
    return commit_and_push(rel, today, outcome)


if __name__ == "__main__":
    raise SystemExit(main())
