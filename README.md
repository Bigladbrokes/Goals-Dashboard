# Goals dashboard

A read-only viewer for my multi-year goals, published on GitHub Pages. Plain
static files — no build step, no bundler. `index.html` loads React from a CDN
and renders whatever is in `data/`.

```
index.html              the viewer (self-contained)
data/index.json         which years exist
data/<year>.json        one file per year — the only source of data
scripts/validate-data.mjs  shape check, run in CI before deploy
.github/workflows/deploy.yml
```

## Viewing

- `/` shows the year in `data/index.json` → `default`.
- `/?year=2027` shows that year, so any year is linkable. If the year is not
  listed in `index.json`, the page says so instead of going blank.
- The year switcher in the header appears once there is more than one year.

Nothing can be edited here. Progress is changed by editing the data files and
committing them.

## Yearly workflow

The dashboard you edit is the one in Claude (it has add/edit/import/export).
This repo only displays its output.

1. **Export** from the editable dashboard: *ดาวน์โหลด JSON*. You get
   `<year>.json`.
2. **Drop it in `data/`** — e.g. `data/2027.json`. Overwrite the existing file
   when you are updating a year you already published.
3. **Add the year to `data/index.json`**, newest first, and point `default` at
   the year you want visitors to land on:

   ```json
   {
     "years": [2027, 2026],
     "default": 2027
   }
   ```

4. **Check the shape** before committing:

   ```
   node scripts/validate-data.mjs
   ```

   It prints `data/ ok — …` or lists every problem it found.

5. **Commit and push to `main`.** The workflow validates `data/` again, then
   deploys the repo root to Pages. A malformed file fails the build, so the live
   page never picks up broken data.

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
  `subtitle`, `source`, and `note` are optional strings.

## Local preview

`fetch` needs HTTP, so opening the file directly will not load the data. Serve
the folder:

```
python -m http.server 8000
```

then open <http://localhost:8000/>.
