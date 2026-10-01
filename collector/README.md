# SynthSift collector

Gathers LLM agent transcripts from a machine into a zip that SynthSift can
load directly. It is separate from the SynthSift app on purpose:
- one Python file plus a folder of text files;
- standard library only (Python 3.8+) on Linux, macOS and Windows;
- no install needed: copy this folder to a laptop, server or incident-response
  target, run it there, and bring back the zip.

```bash
python3 synthsift_collect.py                            # your own sessions on this machine
sudo python3 synthsift_collect.py --all-users -o ir.zip # every home directory
python3 synthsift_collect.py --agents claude_code,openclaw --since-days 7
python3 synthsift_collect.py --dry-run                  # list what would be taken
python3 synthsift_collect.py --list-agents              # show the glob lists in use
python3 synthsift_collect.py --target claude_code=/data/claude-logs --target openclaw=/srv/oc
python3 synthsift_collect.py --target /mnt/image/home/bob   # a home directory somewhere else
```

On Windows use `py -3 synthsift_collect.py …`, in an administrator prompt for
`--all-users`.

Then upload the zip in SynthSift.

| Option | Meaning |
|---|---|
| `-o, --output` | zip to write (default `synthsift-collect-<host>-<time>.zip`) |
| `--host` | host name to file everything under (default: this machine's name) |
| `--all-users` | search every home directory (`/home/*`, `/Users/*`, `/root`, `C:\Users\*`) instead of just yours |
| `--target [AGENT=]DIR` | search this folder instead of the default locations; repeat for more (see below) |
| `--agents` | comma-separated agents to collect (default: every glob list) |
| `--since-days N` | only files changed in the last N days |
| `--globs DIR` | use another folder of glob lists |
| `--dry-run` | print `zip path ⇥ source path` for every match and write nothing |
| `--list-agents` | print the patterns each agent uses |

## Transcripts somewhere else: `--target`

By default the collector looks where each agent keeps its files. When they
are somewhere else (a custom config folder, an export, a backup, a mounted
disk), point it at one or more folders with `--target`, repeated as often as
needed:

- `--target AGENT=DIR`: DIR holds that agent's transcripts. Everything
  below it is searched for the agent's transcript files, whatever the folder
  layout, hidden folders included. The files are the names at the end of its glob list, for example
  `*.jsonl` for `claude_code`; `--list-agents` shows them. Paths in the zip
  are relative to DIR, and the files are filed under the folder owner's
  account.
- `--target DIR`: DIR is a home directory, e.g. `/mnt/image/home/bob`. It
  is searched with every agent's usual `~/…` locations and filed under the
  folder's name (`bob`).

With `--target`, only the targets are searched; add `--all-users` to search
every home directory as well. `--agents`, `--since-days` and `--dry-run`
apply to targets too.

## Glob lists: `globs/<agent>.txt`

There is one text file per agent. The file name is the SynthSift parser the
files are meant for, and becomes the agent folder in the zip.

| File | Agent | SynthSift parser |
|---|---|---|
| [`claude_code.txt`](globs/claude_code.txt) | Claude Code | ready |
| [`openclaw.txt`](globs/openclaw.txt) | OpenClaw (incl. Clawdbot / Moltbot installs) | ready |
| [`gemini.txt`](globs/gemini.txt) | Gemini CLI | ready |
| [`antigravity.txt`](globs/antigravity.txt) | Google Antigravity (IDE and CLI) | ready |
| [`hermes.txt`](globs/hermes.txt) | Hermes Agent | placeholder: files are kept, not parsed yet |

Each line is one glob:

```text
# comment
~/.claude/./projects/**/*.jsonl          ~ is the home directory of the user being collected
${CLAUDE_CONFIG_DIR}/./projects/**/*.jsonl
~/.openclaw/./agents/*/agent/openclaw-agent.sqlite
!*.lock                                  exclude files whose name matches
```

- `*`, `?`, `[...]` and `**` (any number of folders) work as usual.
- `${VAR}` is an environment variable of the user running the collector. The
  line is skipped when the variable is unset, and for other users, whose
  environment isn't known. The exceptions are `HOME`, `USERPROFILE`,
  `LOCALAPPDATA` and `APPDATA`, which are worked out from each user's home
  directory.
- `/./` marks where the path kept in the zip starts:
  `~/.openclaw/./agents/main/agent/openclaw-agent.sqlite` is stored as
  `<host>/<user>/openclaw/agents/main/agent/openclaw-agent.sqlite`. Without it,
  the part after the last folder with no wildcard is kept.
- The same file reached through two lines (e.g. `$CLAUDE_CONFIG_DIR` pointing
  at `~/.claude`, or a symlink) is collected once. Two different files that
  would land on the same zip path are kept apart with a `from-<folder>/`
  prefix, e.g. `~/.clawdbot` next to `~/.openclaw`.

**Adding an agent**: drop in `globs/<name>.txt`. Name it after a SynthSift
parser (`synthsift harnesses` lists them), so the files are routed to it. With
any other name the files are still collected, and SynthSift tries to recognise
their format.

## What ends up in the zip

```text
synthsift-collect-laptop-20260627-101500.zip
├── laptop/alice/claude_code/projects/-home-alice-api/<session>.jsonl
├── laptop/alice/openclaw/agents/main/agent/openclaw-agent.sqlite
├── laptop/bob/…
└── synthsift-manifest.csv
```

`synthsift-manifest.csv` has one row per file: agent, user, zip path, original
path, size, modification time (UTC), SHA-256, how it was copied and which glob
matched it. The zip comment records the collector version, host, collection
time, collecting account and OS. SynthSift ignores the manifest when
importing.

Nothing on the machine is changed:
- Files are only read.
- SQLite databases (OpenClaw, Hermes) are copied with SQLite's online backup
  API, which gives a consistent snapshot even while the agent is writing.
- If that fails, the database is copied as is, together with its `-wal` file.
- Files that can't be read (permissions) are listed at the end and skipped.

## Before you collect

- Claude Code and Gemini CLI delete sessions after 30 days by default, so
  collect regularly, or raise `cleanupPeriodDays` /
  `general.sessionRetention.maxAge`.
- OpenClaw incognito threads are never written to disk.
- Collecting other users' data needs root or administrator rights and should
  follow your organisation's policy.
