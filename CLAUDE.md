# Goals dashboard

This repo is the single source of truth for the goals dashboard.
`data/<year>.json` is the data; `index.html` only displays it. There is no
import or export step: every change is an edit to the data file (manual
entries) or a run of `update.py` (roadmap projects), and `update.py` commits
and pushes.

Everything in the repo root is published on GitHub Pages, including this file
and the scripts. Keep machine paths, emails and keys out of all of it.

## Commands

```
python update.py            # sync roadmap projects, validate, commit, push
python update.py --dry-run  # print the summary and any leaks; write nothing
node scripts/validate-data.mjs
python -m unittest discover -s tests
```

`update.py` expects the Roadmap-board repo in a sibling folder
(`../Roadmap-board`); pass `--roadmap-dir` otherwise. It only reads that
folder. Never write to it, including `projects.toml`, `board.html` and any
`ROADMAP_STATUS.json`.

## Two kinds of entry

- **Synced** (`syncKey` is set): `steps`, `roadmap` and `syncedAt` belong to
  `update.py`. Do not edit them by hand; progress changes in the project's
  own roadmap and arrives on the next run. `title`, `subtitle`, `category`,
  `source` and `note` are set when the entry is created and may be edited.
  A synced entry must not have a `stage` (the validator rejects it).
  Its roadmap publishes step titles and statuses only, never the status
  files' `detail` text (private working notes; the validator rejects it).
- **Manual** (`syncKey: null`): books, courses, hand-kept projects. Edited by
  hand. Optional free-text `stage` says where the project is.

## Requests like "Outliers ถึง 30% แล้ว"

1. **Find the entry** in `data/<current year>.json` by `title` or `subtitle`
   (case-insensitive, Thai or English). If there is no match, or more than
   one, ask. If it is a synced entry, stop and say its progress comes from
   its roadmap.
2. **Set its steps to that progress.** Each of the 10 steps is 10%, in order:
   30% means `steps[0..2]` are `done: true` and the rest `done: false`.
   A figure that is not a multiple of 10 rounds down; say so in the reply.
3. **Date only the newly ticked steps.** A step that was not done and now is
   gets today's date (`YYYY-MM-DD`). Steps already done keep their dates,
   including `null`; never backfill. A step going back to not done gets
   `date: null`.
4. If the user said where they are (a chapter, a module), set `stage`.
5. **Keep the file layout**: 2-space indent, raw UTF-8 (no `\u` escapes), no
   trailing newline. `json.dumps(data, indent=2, ensure_ascii=False)` produces
   it. `update.py` refuses a file in any other layout, because rewriting it
   would change the bytes of every other entry.
6. **Run `python update.py`.** It syncs, validates, and commits and pushes
   the edit together with any roadmap changes.
7. Reply with the entry, old → new %, and which steps got today's date.

## Adding a manual entry

Append to `projects` with the next free `pN` id:

```json
{
  "id": "p10",
  "syncKey": null,
  "category": "book",
  "title": "…",
  "subtitle": "",
  "source": "",
  "note": "",
  "stage": "",
  "steps": [{ "done": false, "date": null }, …10 entries]
}
```

`category` is one of `course`, `book`, `project`, `other`. `stage` is
optional; omit it rather than leaving it empty when there is nothing to say.
A project with a `ROADMAP_STATUS.json` should be registered in
Roadmap-board's `projects.toml` instead (ask first), and `update.py` creates
its entry.

## Things update.py guarantees, and tests

- Status files are opened read-only; nothing in Roadmap-board is written.
- Entries without a `syncKey` come out byte-identical.
- Dates are set only on a not-done → done transition, as the day it was first
  seen done. A project's first sync dates nothing.
- The leak guard scans every string in the output for local paths, emails
  (except reserved example domains) and key-like strings, and refuses to
  write if it finds one.
- Until the first push is confirmed, it stops before committing, shows the
  diff and asks. The confirmation is stored inside `.git`.

`tests/test_update.py` covers each of these. Run the tests after changing
`update.py`, and keep `scripts/validate-data.mjs` and the check in
`index.html` in step with the data shape.
