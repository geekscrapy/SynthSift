"""IOC and keyword lists.

Lists can hold millions of entries, so matching is a database join rather
than a scan per indicator:

1. ``ioc_tokens`` (paragraph scope, parallel) cuts every paragraph into the
   tokens an indicator could equal: URLs and their hosts, emails, IPv4
   addresses, hashes, file paths, domains together with their parent domains,
   and word n-grams for keywords and phrases.  Defanged text
   (``bad[.]example``, ``hxxp://``) is refanged first.
2. ``ioc`` (corpus scope) bulk-loads the lists into DuckDB (``ioc_entries``,
   plus ``ioc_cidrs`` for address ranges) and matches with one hash join
   (and a range join for CIDR blocks).  Hits become entity *labels*: an
   entity that is on a list gets the list's label, a listed keyword with no
   entity becomes one, and the security module turns hits into findings.

Lists: files uploaded on the Settings page (kept in ``<data dir>/lists/``) and
any server-side files or globs in *List files on the server*.  Plain text
has one value per line (``#`` comments).  CSV / TSV with a header uses the
``value`` (or ``indicator`` / ``ioc`` / ``keyword`` …) column plus optional
``type``, ``label`` and ``severity`` columns.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Any

from ..db import table
from ..fields import Field
from ..nlp.regex_extractors import REGEX_BY_NAME
from .base import Module, register, span_table

SEVERITIES = ["info", "low", "medium", "high", "critical"]
KIND_CATEGORY = {"ip": "ip", "cidr": "ip", "domain": "domain", "url": "url", "email": "email", "hash": "hash",
                 "path": "file_path", "keyword": "keyword"}

# ------------------------------------------------------------------ tokens
_DEFANG = [(re.compile(r"\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)", re.I), "."), (re.compile(r"\[:\]"), ":"),
           (re.compile(r"\[@\]|\[at\]|\(at\)", re.I), "@"), (re.compile(r"\bhxxp", re.I), "http"),
           (re.compile(r"\bfxp\b", re.I), "ftp")]
_URL = re.compile(r"\b(?:https?|hxxps?|ftp|fxp)(?:\[:\]|:)//[^\s'\"<>()\]\[]+(?:\[\.\][^\s'\"<>()]+)*", re.I)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+(?:@|\[@\]|\[at\])[\w-]+(?:(?:\.|\[\.\]|\(\.\))[\w-]+)+", re.I)
_DOMAIN = re.compile(r"(?<![\w@/.-])(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.|\[\.\]|\(\.\)))+[a-z][a-z0-9-]{1,62}(?![\w-])", re.I)
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}(?:\.|\[\.\])){3}\d{1,3}(?!\d|\.\d|\[\.\])")  # a full stop may follow
_HASH = re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{128}|[0-9a-fA-F]{64}|[0-9a-fA-F]{40}|[0-9a-fA-F]{32})(?![0-9a-fA-F])")
_WORD = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_'.\-]*[A-Za-z0-9_])?")
_PATHS = [REGEX_BY_NAME[n].pattern for n in ("unix_path", "windows_path") if n in REGEX_BY_NAME]
_TLD_OK = re.compile(r"\.[a-z]{2,}$")


def refang(value: str) -> str:
    for rx, sub in _DEFANG:
        value = rx.sub(sub, value)
    return value


def ip_int(ip: str) -> int | None:
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return None
    a, b, c, d = (int(p) for p in parts)
    return (a << 24) | (b << 16) | (c << 8) | d


def tokens(text: str, max_words: int) -> list[tuple[int, int, str, str, int | None]]:
    """(start, end, normalised token, kind, ipv4 as int) for one paragraph."""
    out: dict[tuple[int, int, str], tuple[int, int, str, str, int | None]] = {}

    def add(s: int, e: int, norm: str, kind: str, ipn: int | None = None) -> None:
        if norm:
            out.setdefault((s, e, norm), (s, e, norm, kind, ipn))

    def host(s: int, value: str) -> None:
        v = refang(value).lower().rstrip(".")
        ipn = ip_int(v)
        if ipn is not None:
            add(s, s + len(value), v, "ip", ipn)
            return
        if not _TLD_OK.search(v):
            return
        add(s, s + len(value), v, "domain")
        parts = v.split(".")
        for i in range(1, len(parts) - 1):  # a.b.bad.example -> b.bad.example, bad.example
            add(s, s + len(value), ".".join(parts[i:]), "domain_parent")

    for m in _URL.finditer(text):
        raw = m.group(0).rstrip(".,;:!?")
        url = refang(raw).lower()
        add(m.start(), m.start() + len(raw), url.rstrip("/"), "url")
        hm = re.match(r"[a-z]+://(?:[^@/\s]+@)?(\[[^\]]+\]|[^:/?#\s]+)", url)
        if hm:  # the host inside the URL, with its own offsets
            h_raw = re.match(r"[^:]+(?::|\[:\])//(?:[^@/\s]+@)?([^:/?#\s]+)", raw, re.I)
            if h_raw:
                host(m.start() + h_raw.start(1), h_raw.group(1))
    for m in _EMAIL.finditer(text):
        add(m.start(), m.end(), refang(m.group(0)).lower(), "email")
        sep = re.search(r"@|\[@\]|\[at\]", m.group(0), re.I)
        if sep:  # the mail domain is a domain too
            host(m.start() + sep.end(), m.group(0)[sep.end():])
    for m in _IPV4.finditer(text):
        v = refang(m.group(0))
        ipn = ip_int(v)
        if ipn is not None:
            add(m.start(), m.end(), v, "ip", ipn)
    for m in _DOMAIN.finditer(text):
        host(m.start(), m.group(0))
    for m in _HASH.finditer(text):
        add(m.start(), m.end(), m.group(0).lower(), "hash")
    for rx in _PATHS:
        for m in rx.finditer(text):
            add(m.start(), m.end(), m.group(0).lower(), "path")
    words = [(m.start(), m.end(), m.group(0).lower()) for m in _WORD.finditer(text)]
    for i in range(len(words)):
        for n in range(1, max_words + 1):
            if i + n > len(words):
                break
            s, e = words[i][0], words[i + n - 1][1]
            if n > 1 and "\n" in text[s:e]:
                break  # phrases don't span lines
            add(s, e, " ".join(w[2] for w in words[i:i + n]), "word")
    return list(out.values())


@register
class IOCTokensModule(Module):
    name = "ioc_tokens"
    version = "2"
    label = "IOC tokens"
    description = "Cuts paragraphs into the tokens a list entry could equal (URLs, hosts, IPs, hashes, paths, word n-grams)."
    kind = "extraction"
    helper_of = "ioc"
    order = 31
    chunk_size = 5000
    tables = (table("x_ioc_tokens", "para_hash", ("start", "int"), ("end", "int"), "norm", "kind", ("ip", "bigint"),
                    index=["para_hash"], description="Candidate tokens for list matching"),)
    options = (
        Field("ioc_phrase_words", "Longest keyword phrase (words)", "int", 4, "parse", "",
              "Multi-word keywords up to this length can match. Higher = more tokens stored.", min=1, max=8),
    )

    def process(self, paras, deps):
        n = int(self.opt("ioc_phrase_words", 4))
        rows = []
        for p in paras:
            for s, e, norm, kind, ipn in tokens(p.text, n):
                rows.append((p.hash, s, e, norm, kind, ipn))
        return {"x_ioc_tokens": rows}


# -------------------------------------------------------------------- lists
LIST_SUFFIXES = (".txt", ".csv", ".tsv", ".list", ".ioc", ".lst")


def list_files(data_dir: Path | None, cfg: dict[str, Any]) -> list[Path]:
    """Every enabled list: uploads (minus the disabled ones) and server-side paths / globs."""
    files: list[Path] = []
    disabled = {x.strip() for x in str(cfg.get("ioc_disabled", "")).splitlines() if x.strip()}
    if data_dir is not None and (data_dir / "lists").is_dir():
        files += [p for p in sorted((data_dir / "lists").iterdir())
                  if p.is_file() and p.name not in disabled and not p.name.endswith(".part")]
    for line in str(cfg.get("ioc_paths", "")).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for hit in sorted(glob.glob(os.path.expanduser(line), recursive=True)):
            if os.path.isfile(hit):
                files.append(Path(hit))
    return files


_VALUE_COLS = ("value", "indicator", "ioc", "observable", "keyword", "term", "pattern", "ioc_value", "domain", "ip", "url", "hash")
_TYPE_COLS = ("type", "kind", "indicator_type", "ioc_type", "category")
_LABEL_COLS = ("label", "tag", "tags", "threat", "malware", "malware_printable", "description", "name", "source")
_SEV_COLS = ("severity", "priority", "risk", "level")


def list_label(path: Path) -> str:
    """Default label of a list's entries: its file name without extensions."""
    name = path.name.removesuffix(".gz")
    return name.rsplit(".", 1)[0] if "." in name else name


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def load_list(storage, path: Path, list_name: str, default_severity: str) -> int:
    """Append one list file to ``ioc_entries`` (normalised in SQL); returns entries added."""
    p = _sql_str(str(path))
    header_cols: list[str] = []
    if Path(path.name.lower().removesuffix(".gz")).suffix in (".csv", ".tsv"):  # DuckDB reads .gz itself
        try:
            header_cols = [r[0] for r in storage.query(f"DESCRIBE SELECT * FROM read_csv({p}, header=true, all_varchar=true, "
                                                       "sample_size=2000)")]
        except Exception:  # noqa: BLE001 - not really a CSV: read it as lines
            header_cols = []
    lower = {c.lower().strip(): c for c in header_cols}
    value_col = next((lower[c] for c in _VALUE_COLS if c in lower), header_cols[0] if header_cols else None)
    if value_col is not None and len(header_cols) > 1:
        def col(names):
            c = next((lower[x] for x in names if x in lower), None)
            return f'"{c}"' if c else "NULL"
        src = (f'SELECT "{value_col}" AS value, {col(_TYPE_COLS)} AS kind, {col(_LABEL_COLS)} AS label, '
               f"{col(_SEV_COLS)} AS severity FROM read_csv({p}, header=true, all_varchar=true, sample_size=2000, "
               "ignore_errors=true)")
    else:  # one value per line
        src = (f"SELECT column0 AS value, NULL AS kind, NULL AS label, NULL AS severity FROM read_csv({p}, header=false, "
               "columns={'column0': 'VARCHAR'}, delim='\x1f', quote='', escape='', ignore_errors=true, strict_mode=false)")
    ipv4 = r"^(\d{1,3}\.){3}\d{1,3}$"
    refang_sql = ("lower(trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(value, "
                  r"'\[\.\]|\(\.\)|\{\.\}|\[dot\]', '.', 'gi'), '\[:\]', ':', 'g'), '\[@\]|\[at\]', '@', 'gi'), "
                  r"'^hxxp', 'http', 'i'), '\s+', ' ', 'g')))")
    sev = ", ".join(_sql_str(s) for s in SEVERITIES)
    before = storage.count("ioc_entries")
    storage.execute(f"""
        INSERT INTO ioc_entries
        SELECT list, value, norm, kind, label, severity FROM (
            SELECT {_sql_str(list_name)} AS list, value,
                   CASE WHEN k = 'url' THEN rtrim(n, '/') WHEN k IN ('domain', 'keyword') THEN rtrim(n, '.') ELSE n END AS norm,
                   k AS kind, coalesce(NULLIF(trim(label), ''), {_sql_str(list_label(path))}) AS label,
                   CASE WHEN lower(trim(severity)) IN ({sev}) THEN lower(trim(severity)) ELSE {_sql_str(default_severity)} END AS severity
            FROM (
                SELECT value, n, label, severity,
                    CASE
                        WHEN lower(coalesce(kind, '')) IN ('ip', 'ipv4', 'ip-dst', 'ip-src', 'ip:port') AND regexp_full_match(n, '{ipv4}') THEN 'ip'
                        WHEN regexp_full_match(n, '{ipv4}') THEN 'ip'
                        WHEN regexp_full_match(n, '(\\d{{1,3}}\\.){{3}}\\d{{1,3}}/\\d{{1,2}}') THEN 'cidr'
                        WHEN regexp_matches(n, '^[a-z][a-z0-9+.-]*://') THEN 'url'
                        WHEN regexp_full_match(n, '[^@\\s]+@[^@\\s]+\\.[a-z]{{2,}}') THEN 'email'
                        WHEN regexp_full_match(n, '[0-9a-f]{{32}}|[0-9a-f]{{40}}|[0-9a-f]{{64}}|[0-9a-f]{{128}}') THEN 'hash'
                        WHEN regexp_matches(n, '^(/|~/|[a-z]:\\\\)') THEN 'path'
                        WHEN regexp_full_match(n, '([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z][a-z0-9-]+\\.?') THEN 'domain'
                        ELSE 'keyword'
                    END AS k
                FROM (SELECT value, {refang_sql} AS n, kind, label, severity FROM ({src})
                      WHERE value IS NOT NULL AND trim(value) <> '' AND NOT starts_with(trim(value), '#'))
            )
        ) WHERE norm <> ''
    """)
    return storage.count("ioc_entries") - before


@register
class IOCModule(Module):
    name = "ioc"
    label = "IOC & keyword lists"
    description = ("Matches every paragraph against your indicator and keyword lists (millions of lines are fine): "
                   "IPs and CIDR ranges, domains and their subdomains, URLs, emails, hashes, paths, keywords and phrases.")
    kind = "extraction"
    scope = "corpus"
    order = 30
    requires = ("ioc_tokens",)
    default_enabled = False
    tables = (
        span_table("x_ioc", "List hits as entity candidates (label mode)"),
        table("x_ioc_hits", "para_hash", ("start", "int"), ("end", "int"), "text", "value", "kind", "list", "label",
              "severity", index=["para_hash"], description="Every list hit with its entry's details"),
    )
    span_table = "x_ioc"
    span_mode = "label"
    version = "2"
    options = (
        Field("ioc_lists", "Lists", "lists", "", "parse", "",
              "Upload .txt (one value per line) or .csv / .tsv (value, type, label, severity columns)."),
        Field("ioc_paths", "List files on the server", "textarea", "", "parse", "",
              "Server-side files or globs, one per line – for very large lists you don't want to upload."),
        Field("ioc_disabled", "Disabled uploads", "hidden", "", "parse", ""),
        Field("ioc_subdomains", "Domains match subdomains", "bool", True, "parse", "",
              "A listed bad.example also matches cdn.bad.example."),
        Field("ioc_default_severity", "Default severity", "select", "medium", "parse", "",
              "For entries without a severity column.", options=SEVERITIES),
        Field("ioc_findings", "Raise security findings for hits", "bool", True, "parse", "",
              "Hits appear in the Security view, the timeline and as flagged nodes."),
    )

    def fingerprint_extra(self, ctx: Any) -> Any:
        out = []
        for f in list_files(getattr(ctx, "data_dir", None), self.cfg):
            try:
                st = f.stat()
                out.append((str(f), st.st_size, int(st.st_mtime)))
            except OSError:
                pass
        return out

    def run_corpus(self, ctx: Any) -> int:
        st = ctx.storage
        lists_key = repr((self.fingerprint_extra(ctx), self.opt("ioc_default_severity", "medium"), self.version))
        if st.get_state("ioc:lists") != lists_key or not {"ioc_entries", "ioc_cidrs"} <= set(st.tables()):
            self._load_lists(ctx)
            st.set_state("ioc:lists", lists_key)
        ctx.progress("Matching")
        return self._match(ctx)

    def _load_lists(self, ctx: Any) -> None:
        st = ctx.storage
        st.set_state("ioc:lists", None)
        st.drop("ioc_entries")
        st.execute("CREATE TABLE ioc_entries (list VARCHAR, value VARCHAR, norm VARCHAR, kind VARCHAR, label VARCHAR, "
                   "severity VARCHAR)")
        loaded = []
        files = list_files(ctx.data_dir, self.cfg)
        for i, f in enumerate(files):
            ctx.progress(f"Loading {f.name}", i / max(len(files), 1))
            n = load_list(st, f, f.name, self.opt("ioc_default_severity", "medium"))
            loaded.append({"file": f.name, "entries": n, "bytes": f.stat().st_size})
        kinds = dict(st.query("SELECT kind, count(*) FROM ioc_entries GROUP BY kind"))
        ctx.note("lists", loaded)
        ctx.note("kinds", kinds)
        st.drop("ioc_cidrs")
        st.execute(r"""
            CREATE TABLE ioc_cidrs AS
            SELECT list, value, label, severity,
                   (((a << 24) | (b << 16) | (c << 8) | d) & mask) AS lo,
                   (((a << 24) | (b << 16) | (c << 8) | d) & mask) + (CASE WHEN bits = 32 THEN 0 ELSE (1::BIGINT << (32 - bits)) - 1 END) AS hi
            FROM (SELECT list, value, label, severity,
                         CAST(split_part(ip, '.', 1) AS BIGINT) AS a, CAST(split_part(ip, '.', 2) AS BIGINT) AS b,
                         CAST(split_part(ip, '.', 3) AS BIGINT) AS c, CAST(split_part(ip, '.', 4) AS BIGINT) AS d,
                         CAST(bits AS INTEGER) AS bits,
                         CASE WHEN CAST(bits AS INTEGER) = 0 THEN 0
                              ELSE ((4294967295::BIGINT << (32 - CAST(bits AS INTEGER))) & 4294967295) END AS mask
                  FROM (SELECT list, value, label, severity, split_part(norm, '/', 1) AS ip, split_part(norm, '/', 2) AS bits
                        FROM ioc_entries WHERE kind = 'cidr' AND CAST(split_part(norm, '/', 2) AS INTEGER) BETWEEN 0 AND 32))
        """)

    def _match(self, ctx: Any) -> int:
        st = ctx.storage
        st.delete("x_ioc_hits")
        sub = "TRUE" if self.opt("ioc_subdomains", True) else "FALSE"
        st.execute(f"""
            INSERT INTO x_ioc_hits
            SELECT DISTINCT t.para_hash, t.start, t."end", t.norm, e.value, e.kind, e.list, coalesce(e.label, e.list), e.severity
            FROM x_ioc_tokens t JOIN ioc_entries e ON t.norm = e.norm
            WHERE e.kind <> 'cidr'
              AND (t.kind <> 'domain_parent' OR (e.kind = 'domain' AND {sub}))
              AND (e.kind = 'keyword' OR t.kind <> 'word' OR e.kind = 'hash')
        """)
        st.execute("""
            INSERT INTO x_ioc_hits
            SELECT DISTINCT t.para_hash, t.start, t."end", t.norm, c.value, 'cidr', c.list, coalesce(c.label, c.list), c.severity
            FROM x_ioc_tokens t JOIN ioc_cidrs c ON t.kind = 'ip' AND t.ip BETWEEN c.lo AND c.hi
        """)
        # entity candidates: one per hit span, labelled with the list label(s)
        st.delete("x_ioc")
        cases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in KIND_CATEGORY.items())
        st.execute(f"""
            INSERT INTO x_ioc
            SELECT para_hash, start, "end", any_value(text),
                   CASE any_value(kind) {cases} ELSE 'keyword' END,
                   'ioc:' || any_value(list), NULL,
                   any_value(kind) NOT IN ('keyword'),
                   90, CAST(row_number() OVER (PARTITION BY para_hash ORDER BY start, "end") AS INTEGER), NULL, NULL,
                   list(DISTINCT label ORDER BY label)
            FROM x_ioc_hits GROUP BY para_hash, start, "end"
        """)
        return st.count("x_ioc_hits")
