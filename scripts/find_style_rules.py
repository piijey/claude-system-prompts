#!/usr/bin/env python3
"""
Scan claude_system_prompts_full.md (produced by extract_nextjs_rsc.py) for
specific "avoid this word/phrase/style" instructions, grouped by category,
with the exact matching sentence and which model+date it came from.

This exists so that "eyeballing the doc and spotting something interesting"
turns into an editable, rerunnable search instead of a one-off script each
time. To track a newly-noticed rule: add a (label, regex) pair to CATEGORIES
below and rerun.

Usage:
    python3 find_style_rules.py claude_system_prompts_full.md
    python3 find_style_rules.py claude_system_prompts_full.md -o report.md
"""
import argparse
import re
from datetime import datetime

# Each entry is (label, pattern) or (label, pattern, "sentence"). The
# pattern is matched (case-insensitive, re.search) against a unit of text —
# by default the whole source *paragraph* the match falls in (a "block":
# bounded by a blank line or a <tag>/</tag> marker, whichever is tighter),
# so a multi-sentence thought like "no bullets in prose... inside prose,
# lists read naturally as..." comes back together. Pass "sentence" as a
# third element to instead match (and report) just the single sentence —
# use that for simple one-liner bans where the surrounding paragraph would
# just be noise (e.g. a "concise" or "markdown" match buried in a long
# unrelated paragraph).

CATEGORIES = [
    ("応答冒頭の「Certainly」を避ける", r'Certainly'),
    ("肯定的な形容詞で応答を始めない", r'its response by saying'),
    ('「genuinely」「honestly」等の副詞を避ける', r'avoid(|s) (using|saying) "(genuinely|honestly)'),
    ("応答冒頭の「I'm sorry」「I apologize」を避ける", r'responses with "', 'sentence'),
    ("「I aim to...」などの不要な但し書きなし", r'unnecessary caveats like "I aim to'),

    # expressions
    ("決まり文句・繰り返し言うことを避ける", r'rote words or phrases'),
    ("詩で陳腐なイメージや比喩、予測可能な韻を避ける", r'hackneyed imagery'),
    ("アスタリスクでの感情表現を頼まれない限り使わない", r'emotes or actions inside asterisks'),
    ("ユーザーが使わない限り絵文字を使わない", r'emojis unless'),
    ("ユーザーが頼まない限り悪態をつかない", r'curses unless'),
    ("ユーザーが求めない限り「sweetheart」のような愛称や親愛の言葉を使用しない", r'pet names'),

    # bullets or B|bullet points
    ("太字・見出し・箇条書きの過剰使用を避ける", r'avoid(|s) over-formatting'),
    ("リストを使わない", r'(should not use|avoids writing) (lists|bullet points)'),
    ("タスクを断るとき箇条書きを使わない", r'never uses bullet points when declining'),
    ("箇条書きは1-2文以上(頼まれない限り)", r'at least 1-2 sentences? long', 'sentence'),

    # concise responses
    ("簡潔に応答する", r'concise'),

    # markdown
    ("コードにマークダウンを使用する", r'uses markdown for code'),

    #
    ("用語をこちらから訂正しない", r"does not correct the person's terminology"),
    ("自分の見解に執拗・偏重にならない", r'avoid(|s) being heavy-handed or repetitive'),
    ("過剰な謝罪・自己卑下をしない", r'When Claude makes mistakes'),
]


SENTENCE_PATTERN = r'(?<=[.!?])\s+(?=[A-Z"])'
MAX_BLOCK_SENTENCES = 3  # cap a block quote to the matching sentence + this many more


def _clean(s):
    # real newlines AND literal two-char "\n" (a leftover escaping artifact
    # in a few of the oldest Claude 3-era prompt entries) both collapse to a
    # single space, then runs of whitespace collapse to one.
    s = s.replace("\n", " ").replace("\\n", " ")
    return re.sub(r'\s+', ' ', s).strip()


def load_units(path):
    """Returns (sentences, blocks): both are lists of (model, date, text).
    `sentences` is one entry per sentence; `blocks` is one entry per source
    paragraph — bounded by a blank line (extract_nextjs_rsc.py now emits one
    per original <p>) OR by a <tag>/</tag> marker, whichever is tighter.
    Both boundaries are needed: most paragraphs are blank-line separated,
    but a few older entries pack a section transition like
    "...blow.\n</lists_and_bullets>\n<user_wellbeing>\nClaude uses..." into
    a single <p> with only single newlines, which a blank-line split alone
    would not catch."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # Must normalize the literal two-char "\n" artifact (a few of the oldest
    # Claude 3-era entries use it as their own internal paragraph break)
    # *before* splitting: the splitters' \s+ only recognizes actual
    # whitespace. Convert to a *real* newline rather than a space — these
    # almost always come doubled ("\\n\\n"), which becomes a genuine blank
    # line and is picked up by para_pattern below as a paragraph boundary,
    # same as everywhere else. Collapsing it to a space instead would erase
    # the only paragraph-break signal those entries have, leaving each one
    # a single giant block.
    text = text.replace("\\n", "\n")
    model_headers = [(m.start(), m.group(1).strip()) for m in re.finditer(r'^## (.+)$', text, flags=re.M)]
    date_headers = [(m.start(), m.group(1).strip()) for m in re.finditer(r'^### (.+)$', text, flags=re.M)]

    def lookup(headers, offset):
        best = None
        for off, name in headers:
            if off <= offset:
                best = name
            else:
                break
        return best

    tag_pattern = r'(</?[a-zA-Z0-9_]+>)'
    para_pattern = r'\n\s*\n+'
    sentence_pattern = SENTENCE_PATTERN
    sentences, blocks, offset = [], [], 0
    for chunk in re.split(tag_pattern, text):
        if re.fullmatch(tag_pattern, chunk):
            continue
        for para in re.split(para_pattern, chunk):
            idx = text.find(para, offset)
            if idx == -1:
                idx = offset
            block_text = _clean(para)
            if block_text:
                blocks.append((lookup(model_headers, idx), lookup(date_headers, idx), block_text))
            for sent in re.split(sentence_pattern, para):
                sidx = text.find(sent, offset)
                if sidx == -1:
                    sidx = offset
                offset = sidx + len(sent)
                stripped = _clean(sent)
                if not stripped:
                    continue
                sentences.append((lookup(model_headers, sidx), lookup(date_headers, sidx), stripped))
    return sentences, blocks


def parse_date(d):
    try:
        return datetime.strptime(d, "%B %d, %Y")
    except Exception:
        return datetime.max


def truncate_block(block_text, rx, max_sentences=MAX_BLOCK_SENTENCES):
    """For block-mode matches: a block is a whole source paragraph, which
    for a few of the oldest (Claude 3-era) entries can run to the length of
    the entire prompt, since those didn't break into many small paragraphs
    the way later prompts do. Report only the matching sentence plus up to
    `max_sentences - 1` sentences after it, instead of the whole block."""
    sents = re.split(SENTENCE_PATTERN, block_text)
    for i, s in enumerate(sents):
        if rx.search(s):
            return " ".join(sents[i:i + max_sentences]).strip()
    return block_text  # shouldn't happen: caller already confirmed a match


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md_file")
    ap.add_argument("-o", "--output", help="write report here instead of stdout")
    args = ap.parse_args()

    sentences, blocks = load_units(args.md_file)
    lines = []
    for entry in CATEGORIES:
        label, pattern = entry[0], entry[1]
        mode = entry[2] if len(entry) > 2 else "block"
        corpus = sentences if mode == "sentence" else blocks
        rx = re.compile(pattern, re.IGNORECASE)
        matches = [
            (model, date, unit if mode == "sentence" else truncate_block(unit, rx))
            for model, date, unit in corpus if rx.search(unit)
        ]
        # dedupe identical (model, date, sentence) triples
        seen, uniq = set(), []
        for m in matches:
            if m not in seen:
                seen.add(m)
                uniq.append(m)

        lines.append(f"\n## {label}")
        lines.append(f"検索パターン: `{pattern}`")
        if not uniq:
            lines.append("(ヒットなし)")
            continue

        # group identical wording together: one shared list of (model, date)
        # occurrences followed by a single quoted original sentence, instead
        # of repeating the same quote once per model/date.
        groups = {}
        for model, date, sent in uniq:
            groups.setdefault(sent, []).append((model, date))
        for occs in groups.values():
            occs.sort(key=lambda md: parse_date(md[1]))
        ordered = sorted(groups.items(), key=lambda kv: parse_date(kv[1][0][1]))

        first_model, first_date = min(
            (md for occs in groups.values() for md in occs), key=lambda md: parse_date(md[1])
        )
        lines.append(f"初出: {first_date} ({first_model})  |  ヒット数: {len(uniq)}  |  表現の種類: {len(groups)}")
        for sent, occs in ordered:
            lines.append("")
            for model, date in occs:
                lines.append(f"- **{model} ({date})**")
            lines.append(f"  > {sent}")

    report = "\n".join(lines)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"wrote {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
