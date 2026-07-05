#!/usr/bin/env python3
"""
Extract full page text from a curl'd Next.js App Router page (Mintlify docs, etc.)
that looks empty/"Loading..." when tag-stripped, because the real content ships as
a React Server Components (RSC) "flight" stream embedded in <script> tags, not as
plain HTML.

Usage:
    curl https://example.com/some/doc/page > page.html
    python3 extract_nextjs_rsc.py page.html -o page.md

How it works
------------
Next.js streams RSC payloads via repeated:
    self.__next_f.push([1,"<id>:<json-or-text-chunk>\n..."])
calls embedded in <script> tags. Concatenating and parsing all of these
reconstructs a `store` of id -> value, where values reference each other via
special string markers:
    "$Lxx"  -> reference to another id's value (element/array/object)
    "$xx"   -> reference to another id's value (raw text chunk, pushed as
               "xx:Txxx,<exact-byte-length-of-text>")
Walking the tree from the root id (the one containing the page's
`id="content-container"` article element) and resolving these references in
order reconstructs the full visual document text, including content that a
naive tag-stripped curl (or a small "read this URL" fetch) would just show as
"Loading..." placeholders.

Gotchas this script specifically works around (each cost real debugging time):
1. Chunk boundaries do NOT always align with "\\n"-separated flight records.
   Splitting the concatenated stream on literal newlines silently merges two
   adjacent records into one and loses the second id entirely. Fix: scan
   sequentially for "<hex-id>:" markers and bracket/quote-balance-parse the
   value that follows, instead of using str.split("\\n").
2. Some records are NOT JSON — they use a special text-chunk encoding
   "<id>:T<hex-length>,<raw utf-8 bytes>" (no quotes, no escaping). Must be
   parsed on the raw bytes (length is a byte count, not a char count) and
   stored as a plain string, or those ids silently fail to parse and any
   content that references them (via the "$xx" no-"L" form) goes missing.
3. Reference markers come in two forms — "$Lxx" (element/array) and "$xx"
   (plain text) — and both need to be tried when resolving a string node.
"""
import argparse
import json
import re
import sys


def extract_push_chunks(html: str) -> str:
    """Find every self.__next_f.push([1,"..."]) call and concatenate the
    decoded string arguments in document order. Uses manual escape-aware
    scanning rather than a regex, since a naive `".*?"` regex can terminate
    early on a `")` sequence that happens to appear inside the payload."""
    marker = "self.__next_f.push([1,"
    chunks = []
    i = 0
    while True:
        i = html.find(marker, i)
        if i == -1:
            break
        j = i + len(marker)
        assert html[j] == '"'
        k = j + 1
        while True:
            c = html[k]
            if c == '\\':
                k += 2
                continue
            if c == '"':
                break
            k += 1
        chunks.append(json.loads(html[j:k + 1]))
        i = k + 1
    return "".join(chunks)


def _scan_json_value(b: bytes, i: int):
    """Return (start, end) byte offsets of the JSON value starting at/after i."""
    while i < len(b) and b[i:i + 1] in b" \t\r\n":
        i += 1
    start = i
    if b[i:i + 1] in b"[{":
        stack = [b[i:i + 1]]
        i += 1
        in_str = False
        while stack:
            c = b[i:i + 1]
            if in_str:
                if c == b'\\':
                    i += 2
                    continue
                if c == b'"':
                    in_str = False
            else:
                if c == b'"':
                    in_str = True
                elif c in (b'[', b'{'):
                    stack.append(c)
                elif c in (b']', b'}'):
                    stack.pop()
            i += 1
        return start, i
    elif b[i:i + 1] == b'"':
        i += 1
        while True:
            c = b[i:i + 1]
            if c == b'\\':
                i += 2
                continue
            if c == b'"':
                i += 1
                break
            i += 1
        return start, i
    else:
        m = re.match(rb'[-\w.]+', b[i:])
        return start, i + m.end()


def build_store(full: str) -> dict:
    """Parse the concatenated flight stream into {id: value}. Handles both
    ordinary JSON records ("id:{...}" / "id:[...]") and raw text-chunk
    records ("id:Txxx,<text>"). Resyncs past anything unparseable instead of
    aborting, since a handful of unrelated bootstrap/import lines are
    expected to fail and are harmless to skip."""
    fb = full.encode("utf-8")
    store = {}
    i, n = 0, len(fb)
    id_re = re.compile(rb'([0-9a-fA-F]+):')
    while i < n:
        m = id_re.match(fb, i)
        if not m:
            nm = id_re.search(fb, i)
            if not nm:
                break
            i, m = nm.start(), nm
        _id = m.group(1).decode()
        j = m.end()
        if fb[j:j + 1] == b'T':
            cm = re.match(rb'T([0-9a-fA-F]+),', fb[j:])
            if cm:
                length = int(cm.group(1), 16)
                tstart = j + cm.end()
                try:
                    store[_id] = fb[tstart:tstart + length].decode("utf-8")
                except Exception:
                    pass
                i = tstart + length
                continue
        try:
            vstart, vend = _scan_json_value(fb, j)
            store[_id] = json.loads(fb[vstart:vend].decode("utf-8"))
            i = vend
        except Exception:
            i = j  # skip and resync on the next "<id>:" match
    return store


REF_RE = re.compile(r'^\$L?([0-9a-fA-F]+)$')  # "$Lxx" (element) or "$xx" (text)
DATE_RE = re.compile(r'^[A-Z][a-z]+ \d{1,2}, \d{4}$')  # tweak/remove for non-changelog pages


def render_markdown(store: dict, root_id: str) -> str:
    """Walk the resolved tree from root_id, emitting h1/h2 for headingLevel
    elements, Mintlify Accordion `title` props (dated changelog entries),
    one block per source <p> (blank-line separated, matching how <p></p>
    maps to a Markdown paragraph break), and "- " list items for <li>.
    Adjust the heading/title/tag heuristics below if the target page isn't
    a Mintlify changelog-style doc.

    Each block is tagged with a kind ("heading" | "para" | "listitem") so
    the final join can put a blank line between ordinary blocks but only a
    single newline between consecutive list items — otherwise every <li>
    in a list would be wrongly torn apart into its own paragraph."""
    output, buf = [], []

    def flush(kind="para", prefix=""):
        if buf:
            text = " ".join(buf).strip()
            if text:
                output.append((kind, prefix + text))
            buf.clear()

    def walk(node, seen):
        if isinstance(node, str):
            m = REF_RE.match(node)
            if m:
                rid = m.group(1)
                if rid in store and rid not in seen:
                    walk(store[rid], seen | {rid})
                return
            if node == "$undefined" or node.startswith("$"):
                return
            if node.strip():
                buf.append(node)
            return
        if isinstance(node, list):
            if node[:1] == ["$"]:
                tag = node[1] if len(node) >= 2 else None
                props = node[3] if len(node) >= 4 and isinstance(node[3], dict) else {}
                heading_level = props.get("headingLevel")
                title = props.get("title")
                if heading_level and "children" in props:
                    flush()
                    saved = buf[:]
                    buf.clear()
                    walk(props["children"], seen)
                    text = " ".join(buf).strip()
                    buf.clear()
                    buf.extend(saved)
                    output.append(("heading", f"{'#' * int(heading_level)} {text}"))
                    return  # heading text is fully captured above
                elif isinstance(title, str) and DATE_RE.match(title):
                    flush()
                    output.append(("heading", f"### {title}"))
                    # no return: children here is the accordion's actual
                    # body content, not more title text — must walk it
                elif tag == "p":
                    flush()  # seal whatever preceded this paragraph
                    if "children" in props:
                        walk(props["children"], seen)
                    flush("para")
                    return
                elif tag == "li":
                    flush()
                    if "children" in props:
                        walk(props["children"], seen)
                    flush("listitem", prefix="- ")
                    return
                if "children" in props:
                    walk(props["children"], seen)
                return
            for item in node:
                walk(item, seen)
            return
        if isinstance(node, dict) and "children" in node:
            walk(node["children"], seen)

    walk(store[root_id], set())
    flush()

    parts = []
    for i, (kind, text) in enumerate(output):
        if i > 0:
            prev_kind = output[i - 1][0]
            parts.append("\n" if kind == prev_kind == "listitem" else "\n\n")
        parts.append(text)
    return "".join(parts)


def find_root_id(store: dict) -> str:
    """Heuristic: the page's root content container is usually the id whose
    value mentions id="content-container" (Mintlify's article wrapper)."""
    for _id, val in store.items():
        if '"content-container"' in json.dumps(val)[:2000]:
            return _id
    raise SystemExit(
        "Couldn't auto-detect the root id (looked for a \"content-container\" "
        "element). Pass --root-id explicitly after inspecting the store."
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html_file", help="Path to HTML saved via `curl <url> > file.html`")
    ap.add_argument("-o", "--output", help="Output markdown path (default: stdout)")
    ap.add_argument("--root-id", help="Flight stream id to start walking from (auto-detected if omitted)")
    args = ap.parse_args()

    with open(args.html_file, encoding="utf-8") as f:
        html = f.read()

    full = extract_push_chunks(html)
    store = build_store(full)
    root_id = args.root_id or find_root_id(store)
    result = render_markdown(store, root_id)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Wrote {len(result)} chars to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
