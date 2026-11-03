# Issue workflow

How an agent works through the roadmap on this repo. The user delegated supervision on 2026-10-30: the agent follows the order, implements, tests, records evidence and closes issues.

## Order

Follow `docs/roadmap.md` top to bottom; each phase is a GitHub milestone and blockers are native "blocked by" links. Never start an issue with an open blocker. Ask the user before starting an issue unless their prompt named it or said to take the next one.

**One issue per conversation.** When an issue is closed, stop and tell the user which issue comes next, so they can `/clear` and start it with a fresh context; never start a second issue in the same conversation, not even under `/goal`. A conversation that carries several issues compacts about once per issue and loses what was already tried: on 2026-11-03 a `/goal` over #113–#120 compacted six times, while #121 alone compacted none, at the same cost per issue. **Dónde vamos** is what lets the next conversation start from zero.

## Effort and sessions at once

- Implement at effort `high`; `.claude/settings.json` sets it as the default (`effortLevel`). Save `xhigh` for grilling, design and a bug that resists. Most of an issue's time is the model thinking, so this is the main lever on speed; the tests and the two reviewers catch what `high` misses.
- Only one session writes in the main checkout. Other sessions open at the same time only talk, research or write under `docs/research/`; one that changes tracked files works in a worktree of its own.
- A second session may implement at the same time only when all of these hold:
  - It works in its own worktree (`claude --worktree`, or `EnterWorktree`). `worktree.symlinkDirectories` in `.claude/settings.json` links `venv` and `models` into it. Copy `.claude/settings.local.json` from the main checkout into it (the file is not tracked), so the `gh` allow rules apply there too.
  - It runs every `pytest`, `python -m allin` and script with `PYTHONPATH=src`: the venv's editable install points at the main checkout's `src/`, so without it the worktree tests the other session's code. The pre-commit already does this.
  - Its issue has no open blocker and touches other modules than the issue in progress. In «Pre-RAG · Pendientes de F2» the groups are the Window (#154–#157), the code of #123 (#153, #159) and the Supervisor (#158).
  - The GPU is one. If another session's Server is up, use it when it has the model the test needs, or test everything else first; never start a second Server that doesn't fit beside it (**Testing with llama serve**).
  - Its xterm titles and data dirs carry its issue number (`allin-main-157`, `<scratch>/m157`), so `tools/xterm_drive.py` never drives the other session's Window.
  - Before opening its pull request, it merges `origin/main` into its branch and keeps both sessions' lines in **Dónde vamos**.

  Each extra session spends the plan's usage as fast as the first, so two at once reach the limit in half the time.

When the milestone "Pre-F2 · Pendientes de F1" starts, tell the user it is time to run `/grill-with-docs` for F2, so F2's issues exist before the pending ones run out.

## Per issue

1. Read the issue, `GLOSSARY.md` and the ADRs it names. Create its branch from an up-to-date `main` (**Branches and pull requests** below).
2. Implement test-first (`tdd` skill). Code, comments and Log in English; docs and GitHub text in Spanish. When the issue adds state that lives while a Turn runs, write a test for each crossing that can reach it mid-Turn: Shift+Enter, closing the Window, a failed Step, a fall that suspends the Turn, yielding to compact, Rewind, `/exit` or a restart, and `/profile switch`. The review of #116–#121 found its bugs there.
3. Run the full suite, `basedpyright` and `ruff`; all must be clean.
4. Review the change (`code-review` skill) and fix what it finds, test-first, before testing by hand, so the evidence shows the code that is merged.
5. Test by hand against every acceptance criterion. For a real Server, follow **Testing with llama serve** below.
6. Record evidence (below).
7. Commit to the issue's branch with `Refs #N` (never `Closes #N`), plus the attribution trailer. Push the branch and open its pull request.
8. Merge the pull request once every criterion is checked and the evidence is uploaded. Then close the issue with a comment in Spanish: what was built, how each criterion was checked (test name or manual step), the pull request, the commits that did it (short hash and subject, as they are in `main`) and the merge commit that brought them, and the evidence images. If a criterion was not met or could not be tested, say so and leave the issue open.
9. If the issue was already closed by the user, still verify it and comment; don't reopen it unless a criterion fails.
10. Mark the issue ✅ in `docs/roadmap.md` and update its **Dónde vamos** section (last done, next, open questions for the user), and commit that on the issue's branch before merging. A new session resumes from that section, so it must always be current.

Never edit the body of a closed issue. If a decision changes during an issue, update the issue body, the glossary or the ADR first.

## Editing and commands

- Edit files with the Edit tool: it fails when the old text isn't there. A script's `str.replace` changes nothing and says nothing when the text doesn't match; if a script must edit, have it check the match count first.
- `gh` on this machine is 2.46 and several of its forms fail: use the ones in **gh 2.46** of `docs/agents/issue-tracker.md`.
- Never wait with a bare `sleep N`; Claude Code blocks it. Wait on the condition itself with a bounded loop (for a Server, see **Testing with llama serve**), or run the command in the background and get notified when it ends.

## Metrics

`venv/bin/python -m scripts.metrics` prints the four DORA metrics over the last four weeks (a merge into `main` is a deploy; a `fix/` branch or `fix:` title is a failed change; an issue labelled `bug` is an incident) and the code's health: tests, coverage, mutation score (`[tool.mutmut]` lists the modules; add pure-logic modules as they appear), type errors, lint warnings, escapes (`cast`, `pyright: ignore`, `noqa`) and median PR size. Run it when a session starts and after each batch; quote it in **Dónde vamos** when a number moves. Falling coverage or mutation score, rising escapes or a rising change failure rate mean: stop adding and review what came in.

## Branches and pull requests

The user chose this on 2026-10-30: one short-lived branch and one pull request per issue, even while issues are done one after another, so each issue's change has its own page on GitHub.

- Name: `<type>/<issue>-<short-description>` (Conventional Branch), lowercase, hyphens, no accents. Types: `feature/`, `fix/`, `refactor/`, `docs/`, `chore/`. Issues done together share one branch: `feature/21-17-servidor-en-caliente`. Work not tied to an issue drops the number: `docs/ramas-y-pull-requests`.
- Branch from `main` after `git pull`; never commit to `main` directly.
- Pull request: `gh pr create --base main`, title like the main commit, body in Spanish (`pr` skill) with `Refs #N`, never `Closes #N`, since the issue is closed by hand with its evidence.
- Merge with `gh pr merge <n> --merge --delete-branch` (a merge commit groups the issue in `main`'s history), then `git switch main && git pull`.
- The `evidence` branch stays orphan and never gets a pull request.

## Evidence

- **Driving and capturing xterms**: the desktop is Wayland with xterm on Xwayland and no capture tool is installed. `tools/xterm_drive.py` lists AllIn's xterms, captures one, types into it, sends Shift+Enter and waits for an event in a Session's Log (`<data-dir>/sessions/<sid>/log.jsonl`); its docstring has the `uv run` line and the xterm options it needs.
- Run Main in its own xterm with `--data-dir` pointing to a throwaway folder, never the user's `var/`. Main's default Enlace is port 9931, so a Session made with `/new` uses the Server there; check that the port is free first. Find Main's pid with `pgrep -f "^./venv/bin/python -m allin --data-dir <dir>"` (the xterm's command line also contains that text) and only ever kill processes you started, matched by their exact title or pid.
- **Capture only what proves something.** Take a capture at the moment a criterion becomes visible (before and after the action that matters), not at every step. Pick the form that proves it best: a whole-window capture, a crop of the relevant line (the border, one Chat line), a short sequence of captures for something that changes over time, or plain text (a JSON state file, a Log excerpt, `/slots` output, a process or FIFO count) when text shows it more exactly than an image. If a criterion is only covered by tests, say so instead of capturing.
- **Evidence shows only the work.** Everything uploaded must come from the repository's code, its running processes and their data: the repo tree, its files, AllIn's Windows, Logs and state, the `llama serve` line and its output. Never run or upload anything that inspects the user's system outside the project (listing their home or other folders, other processes, hardware or desktop details). Before uploading, replace absolute paths outside the repo with placeholders (`<data-dir>`, `<scratch>`), and crop or retake a capture that shows such a path.
- Store evidence on the orphan branch `evidence`, under `issues/<n>/`: the captures and a `test.log` with the steps, the `llama serve` line and the results. Never commit evidence to `main`. Work on it in a worktree of its own (`git worktree list` shows one if it exists; else `git worktree add <scratch>/wt-evidence evidence`), and `git pull --ff-only` there first.
- Embed every image in the closing comment so it shows in the issue itself: `![what it shows](https://github.com/sayee-444/allin/blob/evidence/issues/<n>/<file>.png?raw=true)`. A plain link (`[Main](…png)`, without the `!`) only shows a word to click; #24, #25, #28, #34 and #55 were closed that way and got a later comment with the images embedded. Text files (`test.log`, JSON) stay as plain links. Before posting, check that each file exists on `origin/evidence`.

## Testing with llama serve

The binary is `llama` (`llama serve …`). Models are in `models/` (gitignored); `models/como llamar llama serve con mis diferentes modelos.txt` has the user's lines.

- Check `nvidia-smi` first. If the user's own `llama` processes hold the GPU, don't kill them; ask.
- **One Server, or testing functions in depth**: `Qwen3.8-27B-Uncensored-Q6_K.gguf` with `mmproj-Qwen3.8-27B-Uncensored-f16.gguf`, `-ctk q8_0 -ctv q8_0 -c 128000`. Only one fits.
- **Two Servers at once**: `Swift-1.5-Qwen3.8-27B-IQ3_XXS.gguf` (12.3 GB) with `mmproj-Swift-1.5-Qwen3.8-27B-F16.gguf`, `-ctk q4_0 -ctv q4_0`, e.g. `-c 64000` and `-c 32000`.
- **Three Servers, or testing only the mechanics** (queues, slot locks, several brains): `Swift-1.5-Qwen3.8-27B-IQ2_XXS.gguf` (8.9 GB), two comfortably, three only with a small `-c` and no `--mmproj` (measure with `nvidia-smi`). It reasons noticeably worse: never judge a coordinator Session's behaviour with it.
- Common flags: `-ngl all -fa on --jinja --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.05 --presence-penalty 0 --repeat-penalty 1.0 --kv-unified -np 2 --spec-type draft-mtp`. Change `-np`, `-c` and `--kv-unified` as the test needs.
- Port 9931 is usually the user's own Server: use it only when it is free; for raw `llama serve` probes use 9932+. Shut down every Server you started when done.
- Wait for a Server with one bounded loop on its `/health`: `timeout 300 sh -c 'until curl -sf http://127.0.0.1:9932/health >/dev/null; do sleep 2; done'`.
