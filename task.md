# task.md — AI Team

## Manager decision (2026-11-04)
Pick: **p18 Elliott-RAG** (70%). Next step: index 6 — ปรับ ingest ให้เพิ่มข้อมูลโดยไม่ลบของเดิม.

Why:
1. Next step clear and technical, no real money or API keys.
2. Not synced entry: steps editable by hand.
3. Already usable in terminal, so change is incremental and testable.

Passed over: p22 (10%, step is a long-task experiment), p21 Hub (needs 2 weeks real use, time-bound), splinter-bot 90% and BP-Tutor (synced; progress comes from roadmap), p13/p12/p9 (trading-research, outcome unclear), p14-p17 (courses, user-paced).

## Team (3 agents, files in .claude/agents/)
- `team-investigator` read-only: find Elliott-RAG repo, read ingest code, report file:line + how ingest deletes data today.
- `team-builder` edits only ingest-related files: make ingest append-only (no delete of existing data).
- `team-verifier` read-only + run tests: prove old data survives re-ingest, no duplicates; show only failures.
Order: investigator -> builder -> verifier. Never run builder in parallel with another writer.

## Done means (step 6 ticked only when all true)
- Re-running ingest on new files adds records and leaves existing records unchanged.
- Re-ingesting the same file creates no duplicates.
- Project tests pass (output: failures only).
- Then manager ticks `steps[6]` in data/2026.json with today's date and runs `python update.py` (needs user OK).

## Status
- [x] Manager picked project
- [x] User approves plan
- [x] Investigator report (Elliott-RAG repo, a sibling folder, not git; ingest.py destructive by default)
- [x] Builder change (append-only default, --rebuild opt-in; backup ingest.py.bak-2026-11-04)
- [x] Verifier proof on temp db, fake embeddings: 5/5 pass
- [x] Round 2 (labels course/instructor + id-collision guard): builder done, verifier 6/6 pass incl. PDF paths; --rebuild-before-guard bug fixed by manager (guard now runs before delete; py_compile ok, fix itself not re-run)
- Open: old records in real db still lack course/instructor (needs backup of db/ + backfill, user approval); instructor blank for ~30 srt clips; real Ollama + --ocr-only untested
- (round 1) Not verified: PDF/OCR paths, real Ollama embeddings, appended cues to existing srt, project tests (none exist)
- [ ] Tick steps[6] + update.py
