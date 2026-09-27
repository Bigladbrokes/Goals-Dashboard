# Goals dashboard

A read-only viewer for my multi-year goals, published on GitHub Pages. Plain
static files — no build step, no bundler. `index.html` loads React from a CDN
and renders whatever is in `data/`.

```
index.html              the viewer (self-contained)
data/index.json         which years exist
data/<year>.json        one file per year — the only source of data
scripts/validate-data.mjs  shape check, run in CI before deploy
update.py               roadmap sync + validate + commit + push
tests/                  tests for update.py
.github/workflows/deploy.yml
```

## Viewing

- `/` shows the year in `data/index.json` → `default`.
- `/?year=2027` shows that year, so any year is linkable. If the year is not
  listed in `index.json`, the page says so instead of going blank.
- The year switcher in the header appears once there is more than one year.

Nothing can be edited here. Progress is changed by editing the data files and
committing them.

## Updating

This repo is the source of truth; there is no import or export.

- **Roadmap projects** sync themselves. Run `python update.py`: it reads the
  Roadmap-board registry (`../Roadmap-board/projects.toml`) and each
  registered `ROADMAP_STATUS.json`, read-only, merges them into
  `data/<current year>.json` by `syncKey`, checks for anything private-looking
  (local paths, emails, keys), runs the validator, and commits and pushes if
  the data changed. It prints one line per project: progress and current
  step. `python update.py --dry-run` does the same and writes nothing.
- **Books, courses and hand-kept projects** are edited in the data file:
  tick the next steps, date the newly ticked ones with today, then run
  `python update.py` to validate, commit and push. `CLAUDE.md` has the exact
  rules.

The first time `update.py` would commit, it shows the diff and asks before
committing and pushing.

A new year: create `data/<year>.json` as `{"year": <year>, "projects": []}`
(carry over what continues), add the year to `data/index.json`, newest first,
and point `default` at it:

```json
{
  "years": [2027, 2026],
  "default": 2027
}
```

The Pages workflow validates `data/` again before deploying, so a malformed
file never goes live.

One-time setup: in the repo's *Settings → Pages*, set **Source** to
**GitHub Actions**.

## Data shape

`data/index.json`:

```json
{ "years": [2026], "default": 2026 }
```

`data/<year>.json`:

```json
{
  "year": 2026,
  "updatedAt": "2026-09-17",
  "projects": [
    {
      "id": "p3",
      "syncKey": null,
      "category": "course",
      "title": "Python Data Structures",
      "subtitle": "",
      "source": "Coursera",
      "note": "",
      "steps": [
        { "done": true, "date": "2026-02-01" },
        { "done": false, "date": null }
      ]
    }
  ]
}
```

Rules the validator enforces:

- `year` must match the filename; `updatedAt` is `YYYY-MM-DD` or absent.
- Every year file must be named `<year>.json` and be listed in `index.json`;
  every listed year must have a file. `default` must be one of `years`.
- Each project needs a unique non-empty `id`, a non-empty `title`, and a
  `category` of `course`, `book`, `project`, or `other`.
- `steps` is **exactly 10** entries (each is one 10% slice, in order). Each has
  `done: true|false` and `date: null` or `YYYY-MM-DD`. A step that is not done
  must not carry a date.
- `syncKey`/`syncedAt` are optional (`null` or a string / `YYYY-MM-DD`);
  `subtitle`, `source`, `note` and `stage` are optional strings.
- `stage` is free text for manual entries (where the project is now). An
  entry with a `roadmap` must not also have one.
- `roadmap` (synced entries only, written by `update.py`):
  `{ "current": {n, title} | null, "next": {n, title} | null, "steps": [...] }`.
  Each step is `{ n, title, status, detail, firstSeenDone }`, in the status
  file's order. `current` and `next` must be the first and second steps whose
  status is not `done`/`complete`/`completed`. `firstSeenDone` is `null` or
  `YYYY-MM-DD` and only on done steps. The entry's done segments must equal
  `floor(done steps / total * 10)`.

## Local preview

`fetch` needs HTTP, so opening the file directly will not load the data. Serve
the folder:

```
python -m http.server 8000
```

then open <http://localhost:8000/>.
