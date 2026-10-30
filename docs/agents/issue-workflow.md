# Issue workflow

How an agent works through the roadmap on this repo. The user delegated supervision on 2026-10-30: the agent follows the order, implements, tests, records evidence and closes issues.

## Order

Follow `docs/roadmap.md` top to bottom; each phase is a GitHub milestone and blockers are native "blocked by" links. Never start an issue with an open blocker. Ask the user before starting each issue unless they said to keep going.

When the milestone "Pre-F2 · Pendientes de F1" starts, tell the user it is time to run `/grill-with-docs` for F2, so F2's issues exist before the pending ones run out.

## Per issue

1. Read the issue, `GLOSSARY.md` and the ADRs it names.
2. Implement test-first (`tdd` skill). Code, comments and Log in English; docs and GitHub text in Spanish.
3. Run the full suite, `basedpyright` and `ruff`; all must be clean.
4. Test by hand against every acceptance criterion. For a real Server, follow **Testing with llama serve** below.
5. Record evidence (below).
6. Commit to `main` with `Refs #N` (never `Closes #N`), plus the attribution trailer. Push.
7. Close the issue with a comment in Spanish: what was built, how each criterion was checked (test name or manual step), the commit, and the evidence images. If a criterion was not met or could not be tested, say so and leave the issue open.
8. If the issue was already closed by the user, still verify it and comment; don't reopen it unless a criterion fails.

9. Mark the issue ✅ in `docs/roadmap.md` and update its **Dónde vamos** section (last done, next, open questions for the user), and commit that with the issue. A new session resumes from that section, so it must always be current.

Never edit the body of a closed issue. If a decision changes during an issue, update the issue body, the glossary or the ADR first.

## Evidence

- **Driving and capturing xterms**: the desktop is Wayland with xterm on Xwayland and no capture tool is installed. Use python-xlib and Pillow through `uv run --with python-xlib --with pillow`, without adding them to the project: `XGetImage` on the xterm's frame window gives a real pixel capture, and `XSendEvent` of KeyPress/KeyRelease to the xterm's VT100 widget (the child of the window whose WM_CLASS is `XTerm`), with an explicit key event mask, types into it. xterm only accepts sent keys with `XTerm*allowSendEvents: true`: pass it with `-xrm` to Main's xterm and through `XENVIRONMENT=<file>` so the Windows that Main launches get it too. Only characters on the keyboard map can be typed (no `¿`).
- Run Main in its own xterm with `--data-dir` pointing to a throwaway folder, never the user's `var/`. Main's `base_url` is fixed to port 9931, so tests need the Server there; check that the port is free first. Find Main's pid with `pgrep -f "^./venv/bin/python -m allin --data-dir <dir>"` (the xterm's command line also contains that text) and only ever kill processes you started, matched by their exact title or pid.
- **Capture only what proves something.** Take a capture at the moment a criterion becomes visible (before and after the action that matters), not at every step. Pick the form that proves it best: a whole-window capture, a crop of the relevant line (the border, one Chat line), a short sequence of captures for something that changes over time, or plain text (a JSON state file, a Log excerpt, `/slots` output, a process or FIFO count) when text shows it more exactly than an image. If a criterion is only covered by tests, say so instead of capturing.
- **Evidence shows only the work.** Everything uploaded must come from the repository's code, its running processes and their data: the repo tree, its files, AllIn's Windows, Logs and state, the `llama serve` line and its output. Never run or upload anything that inspects the user's system outside the project (listing their home or other folders, other processes, hardware or desktop details). Before uploading, replace absolute paths outside the repo with placeholders (`<data-dir>`, `<scratch>`), and crop or retake a capture that shows such a path.
- Store evidence on the orphan branch `evidence`, under `issues/<n>/`: the captures and a `test.log` with the steps, the `llama serve` line and the results. Never commit evidence to `main`.
- Link images in the closing comment with `https://github.com/sayee-444/allin/blob/evidence/issues/<n>/<file>.png?raw=true`.

## Testing with llama serve

The binary is `llama` (`llama serve …`). Models are in `models/` (gitignored); `models/como llamar llama serve con mis diferentes modelos.txt` has the user's lines.

- Check `nvidia-smi` first. If the user's own `llama` processes hold the GPU, don't kill them; ask.
- **One Server, or testing functions in depth**: `Qwen3.8-27B-Uncensored-Q6_K.gguf` with `mmproj-Qwen3.8-27B-Uncensored-f16.gguf`, `-ctk q8_0 -ctv q8_0 -c 128000`. Only one fits.
- **Two Servers at once**: `Swift-1.5-Qwen3.8-27B-IQ3_XXS.gguf` (12.3 GB) with `mmproj-Swift-1.5-Qwen3.8-27B-F16.gguf`, `-ctk q4_0 -ctv q4_0`, e.g. `-c 64000` and `-c 32000`.
- Common flags: `-ngl all -fa on --jinja --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.05 --presence-penalty 0 --repeat-penalty 1.0 --kv-unified -np 2 --spec-type draft-mtp`. Change `-np`, `-c` and `--kv-unified` as the test needs.
- Port 9931 is usually the user's own Server. Main can only reach 9931 until the Supervisor's Enlace exists (#24), so use it only when it is free; for raw `llama serve` probes use 9932+. Shut down every Server you started when done.
