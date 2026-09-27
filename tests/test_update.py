#!/usr/bin/env python3
"""Tests for update.py.

    python -m unittest discover -s tests

Standard library only. sync_goals.py is imported from the sibling
Roadmap-board folder, exactly as update.py does, so these run on the machine
that holds both repos and are skipped anywhere else.

Four properties carry the design, and each has its own class:
  - the Roadmap-board side is only ever read (ReadOnlyTest);
  - entries without a syncKey come out byte-identical (ManualEntriesTest);
  - a date is only ever written on a not-done -> done transition (DateTest);
  - a private-looking string stops the write (LeakGuardTest).
"""
from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import update  # noqa: E402

if not (update.DEFAULT_ROADMAP_DIR / "sync_goals.py").exists():
    raise unittest.SkipTest("Roadmap-board/sync_goals.py is not next to this repo")
sg = update.load_sync_goals(update.DEFAULT_ROADMAP_DIR)

ROADMAP = "ROADMAP_STATUS.json"
TODAY = "2026-09-27"
EARLIER = "2026-09-01"


def status(project: str, *statuses: str, details: dict | None = None, titles: dict | None = None) -> dict:
    steps = []
    for i, s in enumerate(statuses, start=1):
        steps.append({"n": i, "title": (titles or {}).get(i, f"Step {i}"), "status": s,
                      "detail": (details or {}).get(i, f"Detail {i}"), "evidence": "commit abc1234"})
    return {"schema_version": 1, "project": project, "steps": steps}


def segments(done: int, dates: dict | None = None) -> list[dict]:
    dates = dates or {}
    return [{"done": i < done, "date": dates.get(i) if i < done else None} for i in range(10)]


MANUAL = [
    {"id": "p1", "syncKey": None, "category": "book", "title": "Outliers", "subtitle": "สัมฤทธิ์พิศวง",
     "source": "", "note": "", "steps": segments(1, {0: "2026-04-11"})},
    {"id": "p2", "syncKey": None, "category": "project", "title": "AlphaFlow", "subtitle": "",
     "source": "Nansen API", "note": "", "stage": "ออกแบบกลยุทธ์", "steps": segments(0)},
]


class Workspace:
    """A throwaway Roadmap-board folder, project folders and a data file."""

    def __init__(self, root: Path):
        self.root = root
        self.board = root / "Roadmap-board"
        self.board.mkdir()
        (self.board / "board.html").write_text("<html>board</html>", encoding="utf-8")
        self.registry_path = self.board / "projects.toml"
        self.data_path = root / "2026.json"
        self.projects: list[tuple[str, Path]] = []

    def add(self, folder: str, data: dict) -> Path:
        repo = self.root / folder
        repo.mkdir(parents=True, exist_ok=True)
        path = repo / ROADMAP
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if all(f != folder for f, _ in self.projects):
            self.projects.append((folder, path))
        self._write_registry()
        return path

    def _write_registry(self):
        blocks = [
            f'[[project]]\nname = "{f}"\nrepo = "{(self.root / f).as_posix()}"\nstatus_json = "{p.as_posix()}"\n'
            for f, p in self.projects
        ]
        self.registry_path.write_text("\n".join(blocks), encoding="utf-8")

    def write_data(self, projects: list[dict], trailing_newline: bool = False) -> str:
        text = json.dumps({"year": 2026, "updatedAt": EARLIER, "projects": projects}, indent=2, ensure_ascii=False)
        text += "\n" if trailing_newline else ""
        self.data_path.write_bytes(text.encode("utf-8"))
        return text

    def data(self) -> dict:
        return json.loads(self.data_path.read_bytes().decode("utf-8"))

    def entry(self, key: str) -> dict:
        return next(p for p in self.data()["projects"] if p.get("syncKey") == key)

    def run(self, today: str = TODAY, write: bool = True) -> update.Outcome:
        registry = sg.load_registry(self.registry_path)
        return update.update_file(self.data_path, registry, sg, today, write=write)


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Workspace(Path(self._tmp.name))


# --------------------------------------------------------------------------

class ReadOnlyTest(WorkspaceCase):
    """Nothing under Roadmap-board, and no status file, is opened to write."""

    PROTECTED = {ROADMAP, "projects.toml", "board.html"}

    def _guard(self):
        opened: list[tuple[str, str]] = []
        violations: list[str] = []

        def name_of(path) -> str:
            try:
                return Path(os.fspath(path)).name
            except TypeError:
                return ""

        def check(path, mode: str, via: str):
            if name_of(path) in self.PROTECTED:
                opened.append((name_of(path), mode))
                if any(c in mode for c in "wxa+"):
                    violations.append(f"{via}({path!r}, mode={mode!r})")

        real = {
            "open": builtins.open, "io_open": io.open, "path_open": Path.open,
            "write_text": Path.write_text, "write_bytes": Path.write_bytes,
            "replace": os.replace, "remove": os.remove, "unlink": Path.unlink,
        }

        def fake_open(file, mode="r", *a, **kw):
            check(file, mode, "open")
            return real["open"](file, mode, *a, **kw)

        def fake_io_open(file, mode="r", *a, **kw):
            check(file, mode, "io.open")
            return real["io_open"](file, mode, *a, **kw)

        def fake_path_open(self_, mode="r", *a, **kw):
            check(self_, mode, "Path.open")
            return real["path_open"](self_, mode, *a, **kw)

        def refuse(key, via):
            def fn(target, *a, **kw):
                if name_of(target) in self.PROTECTED:
                    violations.append(f"{via}({target!r})")
                return real[key](target, *a, **kw)
            return fn

        def fake_replace(src, dst, *a, **kw):
            if name_of(dst) in self.PROTECTED or name_of(src) in self.PROTECTED:
                violations.append(f"os.replace({src!r}, {dst!r})")
            return real["replace"](src, dst, *a, **kw)

        for obj, attr, patched in [
            (builtins, "open", fake_open), (io, "open", fake_io_open), (Path, "open", fake_path_open),
            (Path, "write_text", refuse("write_text", "Path.write_text")),
            (Path, "write_bytes", refuse("write_bytes", "Path.write_bytes")),
            (os, "replace", fake_replace), (os, "remove", refuse("remove", "os.remove")),
            (Path, "unlink", refuse("unlink", "Path.unlink")),
        ]:
            original = getattr(obj, attr)
            setattr(obj, attr, patched)
            self.addCleanup(setattr, obj, attr, original)
        return opened, violations

    def _snapshot(self):
        files = [self.ws.registry_path, self.ws.board / "board.html"] + [p for _, p in self.ws.projects]
        return {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in files}

    def test_status_files_are_opened_read_only(self):
        self.ws.add("alpha", status("alpha", "done", "todo"))
        self.ws.add("beta", status("beta", "done", "done", "current"))
        self.ws.write_data(list(MANUAL))
        opened, violations = self._guard()

        outcome = self.ws.run()

        self.assertEqual(violations, [], f"wrote to a protected file: {violations}")
        # Path.open reaches io.open, so one read can pass two hooks; the count
        # is not the point. That they were read at all, and only read, is.
        status_opens = [mode for name, mode in opened if name == ROADMAP]
        self.assertGreaterEqual(len(status_opens), 2)
        self.assertTrue(all(m in ("r", "rb", "rt") for m in status_opens), status_opens)
        self.assertTrue(outcome.written)

    def test_roadmap_side_is_byte_identical_afterwards(self):
        self.ws.add("alpha", status("alpha", "done", "todo"))
        self.ws.write_data(list(MANUAL))
        before = self._snapshot()
        self.ws.run()
        self.ws.run(today="2026-09-28")
        self.assertEqual(self._snapshot(), before)

    def test_it_works_with_the_roadmap_side_marked_read_only(self):
        # The OS enforces what the guard above observes.
        self.ws.add("alpha", status("alpha", "done", "done", "todo"))
        self.ws.write_data(list(MANUAL))
        protected = [self.ws.registry_path, self.ws.board / "board.html"] + [p for _, p in self.ws.projects]
        for f in protected:
            os.chmod(f, stat.S_IREAD)
            self.addCleanup(os.chmod, f, stat.S_IREAD | stat.S_IWRITE)
        outcome = self.ws.run()
        self.assertTrue(outcome.written)
        self.assertEqual(self.ws.entry("alpha")["roadmap"]["current"], {"n": 3, "title": "Step 3"})

    def test_the_guard_itself_catches_a_write(self):
        path = self.ws.add("alpha", status("alpha", "done"))
        _, violations = self._guard()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("")
        self.assertTrue(violations)


# --------------------------------------------------------------------------

class ManualEntriesTest(WorkspaceCase):
    @staticmethod
    def _blocks(text: str, data: dict) -> list[str]:
        """Each manual entry exactly as it is laid out inside the file."""
        out = []
        for p in data["projects"]:
            if p.get("syncKey"):
                continue
            block = json.dumps(p, indent=2, ensure_ascii=False).replace("\n", "\n    ")
            out.append("    " + block)
        return out

    def test_manual_entries_are_byte_identical_after_a_sync(self):
        self.ws.add("alpha", status("alpha", "done", "done", "todo"))
        before = self.ws.write_data(MANUAL[:1] + [
            {"id": "sync-alpha", "syncKey": "alpha", "category": "project", "title": "alpha",
             "subtitle": "", "source": "Roadmap board", "note": "", "steps": segments(3)},
        ] + MANUAL[1:])
        blocks = self._blocks(before, json.loads(before))
        self.assertEqual(len(blocks), 2)
        for b in blocks:
            self.assertEqual(before.count(b), 1)

        outcome = self.ws.run()

        after = self.ws.data_path.read_bytes().decode("utf-8")
        self.assertTrue(outcome.written, "the synced entry changed, so the file must have been written")
        for b in blocks:
            self.assertEqual(after.count(b), 1, "a manual entry's bytes changed")
        self.assertEqual([p for p in outcome.data["projects"] if not p.get("syncKey")], MANUAL)
        # Order is kept too: the synced entry is still between the two.
        self.assertEqual([p["id"] for p in outcome.data["projects"]], ["p1", "sync-alpha", "p2"])

    def test_the_real_data_file_keeps_its_manual_entries(self):
        real = REPO / "data" / "2026.json"
        text = real.read_bytes().decode("utf-8")
        self.ws.data_path.write_bytes(text.encode("utf-8"))
        for p in json.loads(text)["projects"]:
            if p.get("syncKey"):
                self.ws.add(p["syncKey"], status(p["title"], *(["done"] * 7 + ["todo"] * 3)))

        outcome = self.ws.run()

        after = self.ws.data_path.read_bytes().decode("utf-8")
        for b in self._blocks(text, json.loads(text)):
            self.assertIn(b, after)
        self.assertEqual(after.endswith("\n"), text.endswith("\n"))
        self.assertTrue(outcome.changed)

    def test_a_second_run_is_a_no_op(self):
        self.ws.add("alpha", status("alpha", "done", "todo"))
        self.ws.write_data(list(MANUAL), trailing_newline=True)
        self.ws.run()
        first = self.ws.data_path.read_bytes()
        outcome = self.ws.run(today="2026-10-02")
        self.assertFalse(outcome.changed)
        self.assertFalse(outcome.written)
        self.assertEqual(self.ws.data_path.read_bytes(), first)

    def test_a_hand_formatted_file_is_refused_not_rewritten(self):
        self.ws.add("alpha", status("alpha", "done"))
        self.ws.data_path.write_text(json.dumps({"year": 2026, "projects": MANUAL}), encoding="utf-8")
        before = self.ws.data_path.read_bytes()
        with self.assertRaises(update.NotCanonical):
            self.ws.run()
        self.assertEqual(self.ws.data_path.read_bytes(), before)


# --------------------------------------------------------------------------

class DateTest(WorkspaceCase):
    def _existing(self, key: str, done_segments: int, roadmap=None, dates=None) -> dict:
        e = {"id": f"sync-{key}", "syncKey": key, "category": "project", "title": key, "subtitle": "",
             "source": "Roadmap board", "note": "", "steps": segments(done_segments, dates)}
        if roadmap is not None:
            e["roadmap"] = roadmap
        return e

    def test_first_sync_of_an_existing_entry_backfills_nothing(self):
        # Before this script existed: 5 segments done, no roadmap object.
        self.ws.add("alpha", status("alpha", *(["done"] * 6 + ["todo"] * 4)))
        self.ws.write_data([self._existing("alpha", 5)])

        self.ws.run()

        e = self.ws.entry("alpha")
        self.assertTrue(all(s["firstSeenDone"] is None for s in e["roadmap"]["steps"]))
        # Segments 0-4 were done already: still undated. Segment 5 is new: today.
        self.assertEqual([s["date"] for s in e["steps"]], [None] * 5 + [TODAY] + [None] * 4)

    def test_a_brand_new_project_gets_no_dates(self):
        self.ws.add("alpha", status("alpha", "done", "done", "todo"))
        self.ws.write_data(list(MANUAL))
        self.ws.run()
        e = self.ws.entry("alpha")
        self.assertEqual(e["id"], "sync-alpha")
        self.assertTrue(all(s["date"] is None for s in e["steps"]))
        self.assertTrue(all(s["firstSeenDone"] is None for s in e["roadmap"]["steps"]))

    def test_only_the_not_done_to_done_transition_is_dated(self):
        self.ws.add("alpha", status("alpha", "done", "done", "todo", "todo"))
        self.ws.write_data([self._existing("alpha", 5)])
        self.ws.run(today=EARLIER)  # first sync: nothing dated
        roadmap = self.ws.entry("alpha")["roadmap"]
        roadmap["steps"][1]["firstSeenDone"] = "2026-08-15"  # an older recorded date must survive
        data = self.ws.data()
        data["projects"][0]["roadmap"] = roadmap
        self.ws.data_path.write_bytes(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))

        self.ws.add("alpha", status("alpha", "done", "done", "done", "todo"))
        outcome = self.ws.run()

        steps = self.ws.entry("alpha")["roadmap"]["steps"]
        self.assertEqual([s["firstSeenDone"] for s in steps], [None, "2026-08-15", TODAY, None])
        self.assertEqual([r.newly_done for r in outcome.reports], [1])
        # 3/4 done -> 7 segments; 5 were done, so exactly 5 and 6 are new.
        self.assertEqual([s["date"] for s in self.ws.entry("alpha")["steps"]],
                         [None] * 5 + [TODAY, TODAY] + [None] * 3)

    def test_a_rerun_the_next_day_does_not_move_dates(self):
        self.ws.add("alpha", status("alpha", "done", "todo"))
        self.ws.write_data([self._existing("alpha", 0, roadmap={"current": None, "next": None, "steps": []})])
        self.ws.run(today=EARLIER)
        first = self.ws.entry("alpha")
        self.ws.run(today=TODAY)
        self.assertEqual(self.ws.entry("alpha"), first)
        self.assertEqual(first["roadmap"]["steps"][0]["firstSeenDone"], EARLIER)
        self.assertEqual(first["syncedAt"], EARLIER)

    def test_a_step_that_regresses_loses_its_date_and_is_redated_later(self):
        self.ws.add("alpha", status("alpha", "done", "todo"))
        self.ws.write_data([self._existing("alpha", 0, roadmap={"current": None, "next": None, "steps": []})])
        self.ws.run(today="2026-09-01")
        self.ws.add("alpha", status("alpha", "todo", "todo"))
        self.ws.run(today="2026-09-02")
        self.assertIsNone(self.ws.entry("alpha")["roadmap"]["steps"][0]["firstSeenDone"])
        self.assertFalse(self.ws.entry("alpha")["steps"][0]["done"])
        self.ws.add("alpha", status("alpha", "done", "todo"))
        self.ws.run(today="2026-09-03")
        self.assertEqual(self.ws.entry("alpha")["roadmap"]["steps"][0]["firstSeenDone"], "2026-09-03")
        self.assertEqual(self.ws.entry("alpha")["steps"][0]["date"], "2026-09-03")

    def test_in_progress_and_unverifiable_are_not_done(self):
        self.ws.add("alpha", status("alpha", "in_progress", "unverifiable", "current"))
        self.ws.write_data([self._existing("alpha", 0, roadmap={"current": None, "next": None, "steps": []})])
        self.ws.run()
        e = self.ws.entry("alpha")
        self.assertTrue(all(s["firstSeenDone"] is None for s in e["roadmap"]["steps"]))
        self.assertTrue(all(not s["done"] for s in e["steps"]))


# --------------------------------------------------------------------------

class RoadmapShapeTest(WorkspaceCase):
    def test_current_is_first_not_done_and_next_is_second(self):
        self.ws.add("alpha", status("alpha", "done", "unverifiable", "done", "todo", "in_progress"))
        self.ws.write_data([])
        self.ws.run()
        rm = self.ws.entry("alpha")["roadmap"]
        self.assertEqual(rm["current"], {"n": 2, "title": "Step 2"})
        self.assertEqual(rm["next"], {"n": 4, "title": "Step 4"})
        self.assertEqual(set(rm["steps"][0]), {"n", "title", "status", "firstSeenDone"})

    def test_step_details_are_never_published(self):
        # Details are private working notes. Even one the leak guard would
        # catch must simply not reach the file.
        secret = "PRIVATE-NOTE see C:/Users/someone/keys.txt"
        self.ws.add("alpha", status("alpha", "done", "todo", details={1: secret, 2: secret}))
        self.ws.write_data([])
        outcome = self.ws.run()
        self.assertTrue(outcome.written)
        self.assertEqual(outcome.leaks, [])
        self.assertNotIn("PRIVATE-NOTE", self.ws.data_path.read_bytes().decode("utf-8"))

    def test_all_done_has_no_current_or_next(self):
        self.ws.add("alpha", status("alpha", "done", "done"))
        self.ws.write_data([])
        self.ws.run()
        rm = self.ws.entry("alpha")["roadmap"]
        self.assertIsNone(rm["current"])
        self.assertIsNone(rm["next"])
        self.assertTrue(all(s["done"] for s in self.ws.entry("alpha")["steps"]))

    def test_an_unreadable_status_file_leaves_the_entry_alone(self):
        path = self.ws.add("alpha", status("alpha", "done", "done"))
        self.ws.write_data([])
        self.ws.run(today=EARLIER)
        before = self.ws.entry("alpha")
        path.write_text("{ not json", encoding="utf-8")
        outcome = self.ws.run()
        self.assertEqual(self.ws.entry("alpha"), before)
        self.assertEqual(outcome.reports[0].state, "skipped")

    @unittest.skipIf(shutil.which("node") is None, "node not installed")
    def test_the_output_passes_the_validator(self):
        self.ws.add("alpha", status("alpha", "done", "unverifiable", "done", "todo", "in_progress", "done", "done"))
        self.ws.write_data(list(MANUAL))
        self.ws.run()
        site = Path(self._tmp.name) / "site"
        (site / "scripts").mkdir(parents=True)
        (site / "data").mkdir()
        shutil.copy(update.VALIDATOR, site / "scripts")
        shutil.copy(self.ws.data_path, site / "data" / "2026.json")
        (site / "data" / "index.json").write_text('{"years":[2026],"default":2026}', encoding="utf-8")
        r = subprocess.run(["node", str(site / "scripts" / "validate-data.mjs")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # And the validator rejects a roadmap whose current step is stale.
        data = json.loads((site / "data" / "2026.json").read_text(encoding="utf-8"))
        data["projects"][-1]["roadmap"]["current"] = {"n": 5, "title": "Step 5"}
        (site / "data" / "2026.json").write_text(json.dumps(data), encoding="utf-8")
        r = subprocess.run(["node", str(site / "scripts" / "validate-data.mjs")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("roadmap.current", r.stderr)

    @unittest.skipIf(shutil.which("node") is None, "node not installed")
    def test_step_labels_are_for_manual_entries_only(self):
        self.ws.add("alpha", status("alpha", "done", "todo"))
        manual = json.loads(json.dumps(MANUAL))
        for i, s in enumerate(manual[1]["steps"]):
            s["label"] = f"ขั้น {i + 1}"
        self.ws.write_data(manual)
        outcome = self.ws.run()
        # A sync leaves the labelled manual entry exactly as it was.
        self.assertEqual(outcome.data["projects"][1], manual[1])

        site = Path(self._tmp.name) / "site"
        (site / "scripts").mkdir(parents=True)
        (site / "data").mkdir()
        shutil.copy(update.VALIDATOR, site / "scripts")
        (site / "data" / "index.json").write_text('{"years":[2026],"default":2026}', encoding="utf-8")

        def validate(data):
            (site / "data" / "2026.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return subprocess.run(["node", str(site / "scripts" / "validate-data.mjs")],
                                  capture_output=True, text=True, encoding="utf-8")

        self.assertEqual(validate(outcome.data).returncode, 0)
        outcome.data["projects"][-1]["steps"][0]["label"] = "synced steps are rebuilt"
        r = validate(outcome.data)
        self.assertEqual(r.returncode, 1)
        self.assertIn("label is for manual entries", r.stderr)


# --------------------------------------------------------------------------

class LeakGuardTest(WorkspaceCase):
    def test_a_planted_path_blocks_the_write(self):
        self.ws.add("alpha", status("alpha", "done", "todo",
                                    titles={2: r"Move logs out of C:\Users\someone\AppData\bot.log"}))
        before = self.ws.write_data(list(MANUAL))

        outcome = self.ws.run()

        self.assertFalse(outcome.written)
        self.assertEqual(self.ws.data_path.read_bytes().decode("utf-8"), before)
        wheres = {leak.where for leak in outcome.leaks}
        # Step 2 is also the current step, so its title appears twice.
        self.assertEqual(wheres, {"projects[2].roadmap.steps[1].title", "projects[2].roadmap.current.title"})
        kinds = {leak.kind for leak in outcome.leaks}
        self.assertIn("local path (drive letter)", kinds)
        self.assertIn("local path (\\Users\\)", kinds)

    def test_main_stops_and_prints_the_field(self):
        self.ws.add("alpha", status("alpha", "done", titles={1: "notes in /home/me/notes"}))
        before = self.ws.write_data(list(MANUAL))
        data_dir = Path(self._tmp.name) / "data"
        data_dir.mkdir()
        shutil.copy(self.ws.data_path, data_dir / "2026.json")
        board = self.ws.board
        shutil.copy(update.DEFAULT_ROADMAP_DIR / "sync_goals.py", board / "sync_goals.py")

        real_data_dir = update.DATA_DIR
        update.DATA_DIR = data_dir
        self.addCleanup(setattr, update, "DATA_DIR", real_data_dir)
        err = io.StringIO()
        real_repo = update.REPO
        update.REPO = Path(self._tmp.name)
        self.addCleanup(setattr, update, "REPO", real_repo)
        with unittest.mock.patch("sys.stderr", err), unittest.mock.patch("sys.stdout", io.StringIO()):
            code = update.main(["--roadmap-dir", str(board), "--today", TODAY])

        self.assertEqual(code, 2)
        self.assertIn("projects[2].roadmap.steps[0].title", err.getvalue())
        self.assertIn("/home/", err.getvalue())
        self.assertEqual((data_dir / "2026.json").read_bytes().decode("utf-8"), before)

    def test_the_whole_file_is_scanned_not_just_synced_fields(self):
        self.ws.add("alpha", status("alpha", "done"))
        manual = json.loads(json.dumps(MANUAL))
        manual[0]["note"] = "draft at D:/notes/outliers.md"
        self.ws.write_data(manual)
        outcome = self.ws.run()
        self.assertFalse(outcome.written)
        self.assertEqual([leak.where for leak in outcome.leaks], ["projects[0].note"])

    def test_what_it_catches_and_what_it_lets_through(self):
        caught = {
            "a Windows path": r"open E:\data\x.csv",
            "a forward-slash drive path": "at F:/Claude/thing",
            "a mac home": "in /Users/someone/code",
            "a linux home": "in /home/someone/code",
            "a network share": r"on \\nas\backup\db",
            "an email": "ping someone@gmail.com about it",
            "a GitHub token": "ghp_" + "a1B2" * 9,
            "an OpenAI-style key": "sk-" + "Zx9" * 8,
            "an AWS key id": "AKIA" + "ABCDEFGHIJKLMNOP",
            "a JWT": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36",
            "a Hive WIF key": "5J" + "a" * 49,
            "a PEM block": "-----BEGIN RSA PRIVATE KEY-----",
            "an assigned secret": "api_key = hunter2hunter2",
            "a random blob": "Qm9zc2VzIGFyZSBub3Qgc2VjcmV0cyBidXQ3",
        }
        for label, text in caught.items():
            with self.subTest(label):
                self.assertTrue(update.find_leaks({"x": text}), f"missed {label}: {text!r}")

        allowed = {
            "a reserved example address": "a `Co-Authored-By: ... <someone@example.com>` trailer",
            "a repo-relative path": "path scripts/gate/test_gate.py and tests/test_api.py::test_x",
            "a URL": "see https://api.splinterlands.com/cards/get_details",
            "a commit SHA": "commit 2ae86e3f0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f",
            "snake_case": "test_all_and_modern_never_share_a_cache_entry_ever",
            "prose about keys": "Hive posting-key auth, and the approval token's ENCODING",
            "Thai": "ออกแบบกลยุทธ์",
            "a ratio": "3W/11L at 16:9 and 10:30",
        }
        for label, text in allowed.items():
            with self.subTest(label):
                self.assertEqual(update.find_leaks({"x": text}), [], f"false positive on {label}")


# --------------------------------------------------------------------------

class LiveRegistryTest(unittest.TestCase):
    """The real registry and the real data file, synced in memory only."""

    def test_the_real_sync_is_clean(self):
        registry = sg.load_registry(update.DEFAULT_ROADMAP_DIR / "projects.toml")
        text = (REPO / "data" / "2026.json").read_bytes().decode("utf-8")
        data, reports = update.sync(json.loads(text), registry, sg, TODAY)
        self.assertEqual(update.find_leaks(data), [])
        self.assertEqual([r.state for r in reports if r.state in ("skipped", "unregistered")], [])
        manual_before = [p for p in json.loads(text)["projects"] if not p.get("syncKey")]
        self.assertEqual([p for p in data["projects"] if not p.get("syncKey")], manual_before)


if __name__ == "__main__":
    unittest.main()
