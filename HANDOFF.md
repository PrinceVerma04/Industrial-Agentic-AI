# Session handoff — 8 September 2026 (end of day)

## FIRST THING TOMORROW: push to GitHub

Not a git repo yet — everything is local only, no backup. A reminder is
scheduled for 09:00 IST (routine `trig_013hcaBdu5Ce1rrfHAqXvrJR`).

```bash
cd /home/coral/Desktop/MP
cat .gitignore                    # MUST exclude .venv/ (7.2 GB) and data/stores/
git init && git add -A
git status --short | head -50
git count-objects -vH             # confirm nothing huge got staged
git commit -m "Initial commit: Sovereign On-Premise Agentic AI Workbench (SIH26117)"
gh repo create sovereign-workbench --private --source=. --remote=origin --push
```

**Two cautions:**
- **Make it PRIVATE.** `data/corpus/` currently contains real resumes with
  names, email addresses and phone numbers. Either keep the repo private or
  add `data/corpus/` to `.gitignore` before the first `git add`.
- **Check `.gitignore` before `git add`, not after.** Committing `.venv/` once
  puts 7.2 GB permanently in history and the repo becomes unusable.

## Resume here

```bash
cd /home/coral/Desktop/MP
source .venv/bin/activate
make app            # Streamlit console -> http://127.0.0.1:8501
```

Nothing is running. The Kùzu lock is free.

## Where it stands

```
70 tests passing            499 graph nodes / 391 relations / 37 communities
12 tools, 23 node types     59 vectors, 5 models (~24 GB)
agent run 12.7s             extraction 18.1s/chunk
```

All five rubric points verified by execution. 32-page report at
`report/main.pdf`, packaged as `SIH26117_Project_Report.zip`.

## Today's late work

- **Performance: 5–9× on queries, 13.8× on indexing.** Reasoning tokens were
  ~73% of every call and discarded. Fixed via `task_options` in
  `config/models.yaml` + skipping the terminal critique call.
- **Streamlit console** (`make app`) — six tabs, renders generated files inline.
- **Loopback bind** — Streamlit defaulted to `0.0.0.0` and advertised a public
  URL. Fixed in `.streamlit/config.toml`.
- **Read-only graph access** for the console.

## Start tomorrow with: the grounding gate

Ask the system *"What is the capital of India?"* — it will answer **New Delhi**
from model weights, with nothing marking it as not-from-your-documents.

The same path will invent a confident design pressure for a vessel absent from
the corpus and write it into an approval note. Correct-looking and fabricated is
the worst failure mode for this problem statement.

Needs three-way behaviour:

| Case | Should do |
|---|---|
| In corpus | answer **with citations** |
| General knowledge, not in corpus | answer, **flagged as ungrounded** |
| Org-specific, not in corpus | **refuse** — "not found in the knowledge base" |

Two changes, ~1 hour:
1. Relevance threshold on retrieval — don't synthesise when nothing relevant returned.
2. Post-synthesis grounding check — mark claims that trace to no chunk.

## Then

3. Index `.png`/`.jpg` — silently skipped by `ingest_dir` today.
4. Graph visualisation in the UI — best remaining demo value.
5. Ingest `.docx`/`.xlsx`.
6. Code → spreadsheet chain still returns empty; simple goals work.
7. `qwen2.5-coder:7b` never routed to.

## Gotchas

- **Kùzu: many readers OR one writer.** `make index` needs the console and API
  stopped. Diagnose with `fuser -v data/stores/graph`.
- Only run **one** Streamlit instance — two caused today's lock error.
- Changing `ONTOLOGY=` changes the DB schema → `rm -rf data/stores/graph && make index`.
- Tune speed in `config/models.yaml` → `task_options`. Truncated answers mean
  raising that task's `num_predict`.
- The canary correctly reports "REACHED" on a machine with internet.
