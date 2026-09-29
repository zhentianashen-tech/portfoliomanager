# Portfolio Manager

A local-first toolkit for organizing, labeling, reviewing, and arranging a photography project into
Book spreads and Exhibition blocks.

This public repository contains no photographs and no editorial material from its originating
portfolio. It opens with fictional mock records and neutral generated placeholders. Your real images,
captions, titles, comments, catalogs, and saved versions stay local unless you intentionally add them.

## What it covers

- recursive photo intake from chapter or folder names;
- deterministic visual measurements and optional local Ollama labeling;
- resumable JSON/CSV catalogs and compact local thumbnails;
- uncropped contact sheets for human review;
- a starter five-photo grouping utility;
- a browser-based Book and Exhibition arranging desk;
- independent usage labels for Book, Exhibition, both, or neither;
- empty slots, drag replacement, block ordering, editable titles/comments, undo/redo, and version
  restore;
- readable JSON and Markdown edit traces.

## Try the mock-up

The editor uses only the Python standard library:

```bash
python3 tools/run_sequence_editor.py
```

Open `http://127.0.0.1:8765/`. The demo contains fifteen fictional photo records, ten placed slots,
five unused records, and no image files.

## Use your own photographs

Python 3.12 is the tested environment.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

1. Put source files under `photos/originals/<chapter>/`. This directory is ignored by Git.
2. Start a local Ollama server if you want machine-written labels, then run:

   ```bash
   python3 tools/analyze_portfolio.py
   ```

3. Optionally create contact sheets:

   ```bash
   python3 tools/make_contact_sheets.py
   ```

4. Create a neutral first sequence from eligible catalog entries:

   ```bash
   python3 tools/create_sequence.py
   ```

5. Open the arranging desk:

   ```bash
   python3 tools/run_sequence_editor.py
   ```

The editor automatically prefers `analysis/catalog.json` and `analysis/sequence.json`. If either is
missing, it falls back to the fictional demo. Custom paths can be supplied with `--catalog` and
`--sequence`.

## Privacy model

The repository ignores common photographic formats globally, generated analysis data, edit traces,
environment files, credentials, and local virtual environments. Review `git status` before every
push. Use `git check-ignore -v <path>` when checking whether a sensitive file is excluded.

The Apache 2.0 license applies to the software and documentation only. It does not grant rights to
photographs or other creative content processed with the tool; see `NOTICE.md`.

## Project map

- `editor/` — local arranging interface;
- `tools/analyze_portfolio.py` — feature extraction and optional local vision labels;
- `tools/make_contact_sheets.py` — review sheets;
- `tools/create_sequence.py` — neutral five-slot starter sequence;
- `tools/run_sequence_editor.py` — localhost server, persistence, and readable traces;
- `demo/` — fictional metadata only, with no image files;
- `analysis/` — local generated work, ignored by Git;
- `photos/originals/` — local source archive, ignored by Git.
