# Writing security checks

Every security finding in SynthSift comes from a **check**: a small Python class
that looks at one turn of a conversation and reports what it finds. The
built-in checks live in [`src/synthsift/checks/`](src/synthsift/checks), one
file per theme. You can switch any of them off, change their severity, replace
one with your own version, or add new ones. To add a check, drop a `.py` file
into your data directory. There is no fork and no rebuild.

This guide is the SDK reference. The [README](README.md#security-analysis)
describes what the built-in checks flag.

- [Your first check](#your-first-check)
- [Where checks live](#where-checks-live)
- [The `Check` class](#the-check-class)
- [What a check sees: `EventContext`](#what-a-check-sees-eventcontext)
- [Command lines: `CommandFacts`](#command-lines-commandfacts)
- [Making findings](#making-findings)
- [Run order and cooperating checks](#run-order-and-cooperating-checks)
- [Settings](#settings)
- [Replacing a built-in check](#replacing-a-built-in-check)
- [Corpus checks](#corpus-checks)
- [Testing a check](#testing-a-check)
- [Guidelines](#guidelines)
- [Built-in checks](#built-in-checks)

## Your first check

This check flags tool calls that turn off TLS certificate verification. Save it
as `.synthsift/checks/tls.py`, the `checks` folder of the data directory
(`synthsift serve --data-dir …`, default `.synthsift`):

```python
"""Tool calls that switch off TLS certificate checks."""

import re

from synthsift.checks import Check, register

INSECURE = re.compile(r"(?:^|\s)(?:-k|--insecure|--no-check-certificate)(?=\s|$)|verify\s*=\s*False|"
                      r"NODE_TLS_REJECT_UNAUTHORIZED=0")


@register
class TlsVerificationOff(Check):
    name = "tls_verification_off"
    label = "TLS verification turned off"
    description = "curl -k, wget --no-check-certificate, verify=False, NODE_TLS_REJECT_UNAUTHORIZED=0."
    category = "weakening"
    categories = {"weakening": "Weakened security"}  # a new legend / filter group
    severity = "low"
    events = ("tool_call",)
    order = 600

    def run(self, ctx):
        c = ctx.command
        m = INSECURE.search(c.text)
        if m:
            flag = m.group(0).strip()
            yield self.finding(ctx, f"`{c.base}` turned off certificate checks ({flag})",
                               entities=[r.key for r in c.remotes], value=flag)
```

Then open **Settings → Modules → Security analysis**. The check appears in the
**Checks** table, marked with its file name. Press **Run** and its findings
appear wherever the built-in ones do:
- the Security tab;
- the timeline;
- the dashboard;
- as flagged nodes in the graph.

`synthsift checks --data-dir .synthsift` lists the same table in a terminal.

## Where checks live

| Where | What |
|---|---|
| `src/synthsift/checks/base.py` | The SDK: `Check`, `CorpusCheck`, `EventContext`, `Finding`, `Mention`, `register`, the categories and severities |
| `src/synthsift/checks/command.py` | `CommandFacts`, the command-line parser behind `ctx.command` |
| `src/synthsift/checks/*.py` | The built-in checks: `destructive`, `sensitive`, `dataflow`, `secrets`, `watchlist`, `lists` |
| `<data dir>/checks/*.py` | Your checks. Files starting with `_` are skipped, so use them for shared helpers |

Every module in the `checks` package is imported at start-up, so a file added
there becomes a built-in check.

Files in `<data dir>/checks/` are read again whenever the security module runs,
when the Settings page lists the checks (or you press **Reload**), and by
`synthsift checks`. A file that fails to import is skipped and the problem is
shown in the Checks table. The other files still load.

If a check raises while it runs, it is skipped for the rest of that run. Its
row is marked *failed last run* and shows the error. Built-in checks are not
protected this way: their exceptions propagate, so a bug fails loudly in tests.

Findings are cached like every module's output. The cache key includes the
contents of every check file, so editing a check re-runs the scan the next time
modules run. The **Run** button does this straight away. When nothing has
changed, the run is a no-op.

## The `Check` class

Subclass `Check`, set the class attributes, implement `run`, and decorate the
class with `@register`:

| Attribute | Default | Meaning |
|---|---|---|
| `name` | required | Unique id. Used in Settings and as the default `rule` of findings. A check with the name of a built-in replaces it |
| `label` | `""` | Short title. The default label of findings |
| `description` | `""` | One or two sentences, shown in Settings |
| `category` | `"watchlist"` | A key of `CATEGORIES` (below), or one declared in `categories` |
| `categories` | `{}` | New categories this check introduces: `{key: label}` |
| `severity` | `"medium"` | `info`, `low`, `medium`, `high` or `critical`. On an instance, `self.severity` is the severity chosen in Settings, or this default if none was chosen |
| `severity_from` | `""` | Set it when findings take their severity from somewhere else. Settings then shows this text instead of a severity picker (see `list_hits`) |
| `rule` | `""` | Rule recorded on findings. Defaults to `name` |
| `events` | `()` | Turn types to run on: `user`, `assistant`, `system`, `thought`, `tool_call`, `tool_result`. Empty means all |
| `order` | `500` | Run order, lowest first. See [Run order](#run-order-and-cooperating-checks) |
| `enabled` | `True` | `False` *parks* the check: it is listed but never runs, whatever Settings say |
| `options` | `()` | Settings fields the check reads. See [Settings](#settings) |

| Method | |
|---|---|
| `run(ctx)` | Yields (or returns a list of) `Finding`s for one turn |
| `applies(ctx)` | Whether to call `run` for this turn. By default it checks `events`. Override it for cheaper pre-filters |
| `finding(ctx, detail, **kw)` | Makes a finding on the current turn. See [Making findings](#making-findings) |
| `make(conv, event, detail, **kw)` | The same, for any turn (corpus checks) |
| `opt(key, default=None)` | A setting's value |

One instance of each check is created per scan with the current settings, so
`__init__` is the place to compile patterns that depend on a setting (see
`watchlist.py`). Call `super().__init__(cfg)` first.

Two base classes cover the common shapes:

- **`destructive.CommandPattern`**: set `pattern`, a regular expression over
  the command line. The finding's rule is `op.<name>`.
- **`secrets.SecretPattern`**: set `pattern`. The first match in a turn is
  reported, shortened so the secret is never shown in full. The rule is
  `secret.<name>`.

```python
import re

from synthsift.checks import register
from synthsift.checks.secrets import SecretPattern


@register
class ExampleVendorToken(SecretPattern):
    name = "example_vendor_token"
    description = "API tokens of the (made-up) Example vendor: exv_ + 32 characters."
    severity = "high"
    order = 475
    pattern = re.compile(r"\bexv_[0-9A-Za-z]{32}\b")
```

## What a check sees: `EventContext`

`run(ctx)` receives one turn. Everything on `ctx` is computed the first time it
is asked for, then shared by every check that runs on the same turn:

| | |
|---|---|
| `ctx.event` | The turn: `id`, `conv`, `seq`, `type`, `turn`, `timestamp`, `tool_name`, `arguments` (dict, tool calls), `is_error`, `subagent` |
| `ctx.conv`, `ctx.type` | Shortcuts for `event.conv` and `event.type` |
| `ctx.paragraphs` | The turn's paragraphs: `text`, `role`, `code`. A tool call's paragraphs also have `arg`, the argument name |
| `ctx.text` | All of the turn's text, one paragraph per line |
| `ctx.mentions` | Entities the NLP modules found in the turn, as `Mention`s |
| `ctx.command` | A tool call's command line, parsed (`CommandFacts`). `None` for other turns |
| `ctx.found` | Findings already made for this turn by checks that ran earlier |
| `ctx.has(rule)` | Whether an earlier check reported `rule`, or a rule starting with `rule.` |
| `ctx.opt(key, default)` | A setting's value |

A **`Mention`** has:
- `key`: the normalised entity, which is also its node id in the graph;
- `category`: `file_path`, `url`, `domain`, `ip`, `host`, `email`, `credential`, `cloud`, … (see `categories.py`);
- `text`: the text as written.

It also has four role properties:
- `remote`: a URL, domain, IP, host, email or cloud resource;
- `local`: a file path;
- `secret`: a credential;
- `sensitive`: a well-known credential location such as `/etc/shadow`, `~/.ssh/id_rsa` or `.env` (`SENSITIVE_PATHS`).

Checks never run their own entity extraction. They use what the NLP and regex
modules found, so custom patterns and vocabularies added in Settings reach the
checks too.

## Command lines: `CommandFacts`

For a tool call, `ctx.command` is parsed once into facts about the direction
data moves. The parsing is structural: it holds no list of named offensive
tools.

| Field | |
|---|---|
| `text` | The command line, with here-document bodies that are data dropped (a script fed to `python - <<EOF`). Bodies fed to a shell are kept |
| `base` | The program it runs (`sudo`, `env`, `VAR=x` skipped), or the tool's name |
| `mentions` | Only the entities that are part of the command itself, not file contents being written or a description |
| `remotes` | Remote destinations, loopback left out |
| `locals` | Files, device files left out |
| `secrets` | Credential-shaped entities |
| `writes` | Raw write targets: `-o`, `--output`, `>` |
| `local_writes`, `local_reads` | Files it writes, and files it reads |
| `sources` | `local_reads + secrets`: data that could leave the host |
| `uploads` | Sends data: `curl -d @f` / `-T` / `-F` / `-X POST` from an HTTP client, an object-store copy, scp / rsync to a host |
| `pipes_to_network` | Data piped into a network client (`… \| curl -T -`, `… \| nc host`) |
| `sends` | `uploads or pipes_to_network` |
| `downloads` | Fetches to disk, or clones |
| `fetch_and_run` | Pipes fetched content into an interpreter |
| `remote_exec` | Runs a command on another host (ssh) |
| `bulk_read` | Reads bulk data with no file entity: a DB query or dump, an archive |

The helpers `command_text(arguments, tool_name)`, `strip_heredocs(cmd)`,
`base_command(cmd)`, `http_segments(cmd)` and `write_targets(cmd)` in
`checks/command.py` can be imported if you need them for other text.

## Making findings

```python
yield self.finding(ctx, "detail shown in the UI", entities=[...], chain=[...], value="...",
                   label=None, severity=None, rule=None, category=None)
```

Anything left as `None` falls back to the class attribute (`self.severity` for
the severity).

| Finding field | |
|---|---|
| `detail` | One line, shown everywhere the finding is. Wrap commands and paths in backticks |
| `entities` | Entity keys (`Mention.key`) the finding involves. These nodes are flagged in the graph |
| `chain` | `(source, action, destination)` links. Each end is an entity key or a turn id (`ctx.event.id`). Each link is drawn as a bold `dataflow` edge |
| `value` | What matched, such as an indicator or a watchlist term. Optional |
| `rule` | A stable id for what fired. Use dotted names for families (`secret.jwt`, `watch.migration`) so `ctx.has("secret")` matches all of them |

The module stores each finding as a row of `a_findings`. Findings below the
**Minimum severity** setting are dropped after every check has run, so earlier
findings stay visible to later checks through `ctx.found` whatever that setting
is.

## Run order and cooperating checks

Checks run in `order`, lowest first, and every check sees the findings of
earlier checks on the same turn. The built-in ranges are:

| Order | Checks |
|---|---|
| 100–180 | Destructive commands and log / history clearing |
| 200 | Sensitive file accessed by a command |
| 300–330 | Data flow: fetch-and-run, exfiltration, download, remote execution |
| 400–480 | Secrets, and sensitive files mentioned anywhere |
| 900 | Watchlist |
| 950 | IOC / keyword list hits (corpus) |

The data-flow checks use this to stay exclusive. A command that sends data out
is not also reported as a download, and remote execution is only reported when
neither applies:

```python
def run(self, ctx):
    c = ctx.command
    if ctx.has("exfiltration") or not (c.downloads and c.remotes):
        return ()
    ...
```

If you switch `exfiltration` off, `download` reports those commands instead.

## Settings

All of these live under **Settings → Modules → Security analysis**:

- **Checks** table:
  - switch any check off (saved in `sec_checks_off`);
  - pick the severity it reports (saved in `sec_severity` as `name: severity` lines). The chosen severity becomes `self.severity`. A check may still raise it: exfiltration of a secret is always `critical`, and the watchlist uses it only for lines without a `[severity]` prefix.
- **Minimum severity**: hides findings below this level.
- Each built-in check's own `options`, such as **Scan tool output** and the **Analyst watchlist**.

A check declares the settings it reads as `Field`s from `synthsift.fields`:

```python
from synthsift.fields import Field

TOKEN_MIN = Field("sec_token_min", "Shortest token", "int", 20, "parse", "", "Ignore shorter tokens.", min=8, max=200)

class MyCheck(Check):
    options = (TOKEN_MIN,)

    def run(self, ctx):
        n = self.opt("sec_token_min", 20)
```

Use a `parse` scope so that changing the setting re-runs the scan. Fields
declared by several checks are shown once.

Settings fields are only shown for checks in the `checks` package. They are
added to the Settings schema at start-up, before your folder is read. In a file
of your own, keep tunables as constants at the top of the file. Editing the
file re-runs the scan anyway.

## Replacing a built-in check

Register a check with the built-in's `name`. Your file wins, and the Checks
table marks the row *replaces built-in*. Subclass the built-in to keep most of
it:

```python
import re

from synthsift.checks import register
from synthsift.checks.destructive import ForcePush


@register
class ForcePushToMain(ForcePush):
    """Only force pushes to the main branches matter here."""

    label = "Force push to a main branch"
    severity = "high"
    pattern = re.compile(r"\bgit\s+push\b[^\n]*(?:--force\b|-f\b)[^\n]*\b(?:main|master|release)\b")
```

Delete the file to get the built-in back. To remove a check without replacing
it, switch it off in Settings.

## Corpus checks

A `CorpusCheck` runs once over the whole corpus, after the per-turn checks. Use
one for findings that come from another module's tables. Implement
`run_corpus(cctx)` and make findings with `self.make(conv, event, detail, …)`.
The `cctx` argument is a `CorpusView` with these fields:

| | |
|---|---|
| `storage` | The DuckDB storage. Run SQL against module tables with `storage.query(sql, params)` |
| `enabled` | Names of the switched-on modules |
| `hash_of(paragraph_id)` | A paragraph's content hash, which module tables are keyed by |
| `paragraphs` | Every paragraph, by id: `conv`, `event`, `text`, … |
| `analysis` | Entities per paragraph id |
| `cfg` | The settings |

Set `needs_modules` to the modules whose tables you read, for example
`("ioc",)`. The check is skipped when any of them is off. `lists.py` (IOC and
keyword list hits) is the built-in example.

## Testing a check

`checks.scan` runs the switched-on checks over conversations you build in code.
The repository's own tests (`tests/test_checks.py`, `tests/test_security.py`)
use this helper:

```python
from pathlib import Path

from synthsift import checks
from synthsift.models import Block, Conversation, Message
from synthsift.modules.runner import analyze
from synthsift.segment import segment
from synthsift.settings import defaults


def scan(*messages, **settings):
    conv = Conversation(id="c1", messages=list(messages))
    cfg = {**defaults(), **settings}
    events, paras = segment(conv, cfg)
    analysis = analyze([(p.id, p.text, p.role, p.code) for p in paras], cfg)
    return checks.scan([conv], {"c1": events}, {p.id: p for p in paras}, analysis, cfg)


def call(cmd):
    return Message(role="assistant", blocks=[Block.tool_call("bash", {"command": cmd}, "id0")])


checks.load_user_checks([Path(".synthsift/checks")])  # your folder
[f] = scan(call("curl -k https://internal.example/status"))
assert f.rule == "tls_verification_off"
assert scan(call("curl https://internal.example/status")) == []
```

Pass `errors={}` to `checks.scan` to collect the exceptions your checks raise
instead of skipping them silently.

## Guidelines

- **Detect structure, not names.** Reason about what a command does (data
  leaving, a file overwritten, verification turned off) rather than keeping a
  list of tool or malware names. Lists of indicators belong in the IOC /
  keyword lists, where analysts manage them.
- **Stay deterministic.** No network calls, no LLMs, no randomness. The same
  transcript must give the same findings, and the cache relies on it.
- **Never echo a secret.** Shorten matched secrets the way `SecretPattern`
  does.
- **Keep `detail` short and specific.** It is a table cell, a tooltip and a
  timeline line.
- **Use reserved names in test data**: `example.com`, `.example`, `.test`,
  TEST-NET addresses (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`) and
  obviously fake secrets.
- **Keep checks fast.** They run on every turn. Do cheap tests first (`events`,
  `applies`) and compile patterns once.

## Built-in checks

| Check | File | Order | Category | Severity | Label |
|---|---|---|---|---|---|
| `recursive_delete` | destructive.py | 100 | destruction | high | Recursive force delete |
| `disk_overwrite` | destructive.py | 110 | destruction | critical | Disk / device overwrite |
| `drop_database` | destructive.py | 120 | destruction | high | Drop or truncate database |
| `unscoped_delete` | destructive.py | 130 | destruction | medium | Unscoped delete |
| `object_store_delete` | destructive.py | 140 | destruction | high | Recursive object-store delete |
| `force_push` | destructive.py | 150 | destruction | medium | Force history rewrite / push |
| `world_writable` | destructive.py | 160 | destruction | medium | Recursive world-writable permissions |
| `history_cleared` | destructive.py | 170 | evasion | high | Shell history cleared |
| `log_cleared` | destructive.py | 180 | evasion | high | System log cleared |
| `sensitive_file_access` | sensitive.py | 200 | credential_access | high | Sensitive file accessed |
| `fetch_and_run` | dataflow.py | 300 | execution | high | Downloaded content executed |
| `exfiltration` | dataflow.py | 310 | egress | high (critical with a secret) | Data leaving the host |
| `download` | dataflow.py | 320 | ingress | medium (low without a file written) | File downloaded to disk |
| `remote_exec` | dataflow.py | 330 | execution | low | Remote command execution |
| `private_key` | secrets.py | 400 | data_exposure | critical | Secret exposed |
| `aws_access_key` | secrets.py | 410 | data_exposure | high | Secret exposed |
| `gcp_api_key` | secrets.py | 420 | data_exposure | high | Secret exposed |
| `github_token` | secrets.py | 430 | data_exposure | high | Secret exposed |
| `slack_token` | secrets.py | 440 | data_exposure | high | Secret exposed |
| `bearer_token` | secrets.py | 450 | data_exposure | medium | Secret exposed |
| `jwt` | secrets.py | 460 | data_exposure | medium | Secret exposed |
| `password_assignment` | secrets.py | 470 | data_exposure | medium | Secret exposed |
| `sensitive_file_reference` | sensitive.py | 480 | credential_access | medium | Sensitive file referenced |
| `watchlist` | watchlist.py | 900 | watchlist | medium (lines may set their own) | Watchlist match |
| `list_hits` | lists.py | 950 | ioc / keyword | each list entry's | List hit |

The built-in categories, which are the legend and filter groups in the UI:

| Category | Label |
|---|---|
| `ingress` | Inbound download |
| `egress` | Outbound / exfiltration |
| `execution` | Fetch-and-run |
| `credential_access` | Sensitive resource access |
| `data_exposure` | Exposed secret |
| `destruction` | Destructive / data loss |
| `evasion` | Log / history clearing |
| `watchlist` | Watchlist match |
| `ioc` | IOC list hit |
| `keyword` | Keyword list hit |
