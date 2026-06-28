#!/usr/bin/env python3
"""
vtt_to_nvivo.py
================
Convert Microsoft Teams transcripts (.vtt / WebVTT) into plain-text files
suitable for importing as NVivo 15 transcripts.

The default output is the f4/f5 inline format: one paragraph per turn, with the
timestamp at the END of each turn as #hh:mm:ss-t#. NVivo reads each timestamp as
the end of that turn and the start of the next, so the timeline chains with no
gaps -- which means no blank "silence" rows after import.

    Tom Smith: Thanks so much for agreeing to interview. #00:00:06-9#
    Tom Smith: And this is for an example interview #00:00:18-0#

Use --format tsv for the alternative tab-delimited column output (see below).

Times are reduced to tenths of a second (00:00:03.417 -> 00:00:03.4 -> "...-4").

Speaker handling
----------------
Teams auto-generates speaker labels like "Smith, Tom (Org)". You can leave them
as-is, or remap them in any of four ways (highest precedence first):

  1. Explicit mapping   --map "Smith, Tom (Org)=Tom Smith"   (repeatable)
                        --mapping-file mapping.json|mapping.csv
  2. Interactive prompt  --interactive   (asks you per speaker, per file)
  3. Anonymise          --anonymise      ("Speaker 1", "Speaker 2", ... in
                                          order of first appearance)
  4. Auto reformat      --auto-name      ("Last, First (Org)" -> "First Last")

If none are given, the original Teams label is kept verbatim.

Cleaning short responses
------------------------
Backchannel interjections ("yeah", "mm", "hmm", "okay") clutter transcripts.
Two independent filters remove them:

  --drop-fillers     drop utterances made up ONLY of filler/backchannel words
                     (e.g. "yeah, hmm" or "mm-hmm"). Substantive turns that
                     merely contain a filler are kept. "yes"/"no" are NOT
                     treated as filler by default (they are often meaningful
                     answers); customise with --filler-words.
  --min-words N      drop any utterance shorter than N words. Blunter; use with
                     care as it can remove short but meaningful answers.

Filters run before --merge, so removing an interjection can let the turns either
side of it merge into one clean block.

Rows whose content has no words -- empty cues, or punctuation-only fragments
like ".", "..." or "-" that Teams sometimes emits -- are removed by default.
Pass --keep-empty to retain them. Non-Latin scripts (e.g. Mandarin) count as
words and are kept.

Timeline gaps
-------------
NVivo builds a media transcript from start times: each entry runs from its start
to the next entry's start, so any gap left by a removed cue becomes a blank
entry. By default each entry's end time is therefore stretched to the next
entry's start, leaving a continuous timeline and no blank rows in NVivo. Pass
--keep-gaps to keep each cue's true end time instead.

AI / provenance disclosure
--------------------------
* Teams .vtt transcripts are produced by automated speech recognition (a machine
  learning model). They contain recognition errors and should be checked against
  the source audio before analysis. This tool does not correct transcription; it
  only reformats and cleans.
* This tool was written with the assistance of an AI coding model (Anthropic's
  Claude) and reviewed/tested by its maintainer. See the README for details.

Examples
--------
    # single file, reformat names automatically
    python vtt_to_nvivo.py interview.vtt --auto-name

    # explicit names
    python vtt_to_nvivo.py interview.vtt \
        --map "Smith, Tom (Org)=Tom Smith" \
        --map "Jones, Asha (Org)=Asha Jones"

    # anonymise to Speaker 1 / Speaker 2 ...
    python vtt_to_nvivo.py interview.vtt --anonymise

    # be asked for each speaker
    python vtt_to_nvivo.py interview.vtt --interactive

    # batch a whole folder into ./nvivo
    python vtt_to_nvivo.py ./transcripts --outdir ./nvivo --auto-name

NVivo column order
------------------
NVivo's transcript importer maps ONE field to "Timespan" (a single START-END
range) and ONE field to "Content" -- it does not take separate start/end
columns. For NVivo, use:
    --columns timespan,dialogue        ->  "00:00:03.4 - 00:00:06.9" | "Tom Smith: ..."
Then in NVivo map column 1 to Timespan and column 2 to Content.
(--timespan-sep changes the "-" separator; NVivo also accepts "/".)

Other layouts available with --columns (start,end,timespan,speaker,content,
dialogue), e.g. start,end,speaker,content to inspect in a text editor.

IMPORTANT: do NOT open the .txt in Excel/Numbers/Sheets before importing. They
reformat "00:00:03.4" to "00:00:03" (dropping the tenths) or to a serial number,
which causes NVivo parse errors. Import the raw .txt, or edit in a plain-text
editor only.
"""
from __future__ import annotations

import argparse
import glob as globlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional

__version__ = "1.0.0"

# Non-lexical hesitations and backchannel tokens that are rarely substantive.
# NOTE: "yes"/"no" are deliberately excluded -- in interviews they are often
# meaningful answers. Add them with --filler-words if your data warrants it.
DEFAULT_FILLERS = {
    "uh", "uhh", "um", "umm", "uhm", "er", "err", "erm", "ah", "ahh", "aha",
    "oh", "ooh", "hmm", "hmmm", "hm", "mm", "mmm", "mhm", "mmhm", "mmhmm",
    "mhmm", "uh-huh", "mm-hmm", "huh", "yeah", "yep", "yup", "ya", "nah",
    "okay", "ok", "kay", "right", "mkay", "yup",
}

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #
_TIME = r'(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3}'
CUE_RE = re.compile(rf'^\s*({_TIME})\s*-->\s*({_TIME})')
VOICE_CLOSED_RE = re.compile(r'<v(?:\.[^\s>]+)*\s+([^>]*)>(.*?)</v>', re.DOTALL | re.IGNORECASE)
VOICE_OPEN_RE = re.compile(r'<v(?:\.[^\s>]+)*\s+([^>]*)>(.*)', re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r'</?[^>]+>')
# Teams cue id, e.g. 5d836bf9-e1e5-4313-95d1-5cf660a491f9/5-0
TEAMS_ID_RE = re.compile(r'^[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*/\d+-\d+$')

COLUMN_HEADERS = {
    'start': 'Start time',
    'end': 'End time',
    'timespan': 'Timespan',
    'speaker': 'Speaker',          # speaker on its own
    'content': 'Content',          # text on its own
    'dialogue': 'Content',         # "Speaker: text" in one column (NVivo default)
}


@dataclass
class Cue:
    start: str
    end: str
    speaker: Optional[str]
    content: str


# --------------------------------------------------------------------------- #
# Time handling
# --------------------------------------------------------------------------- #
def to_seconds(ts: str) -> Decimal:
    parts = ts.split(':')
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = '0', parts[0], parts[1]
    else:
        h, m, s = '0', '0', parts[0]
    return Decimal(h) * 3600 + Decimal(m) * 60 + Decimal(s)


def fmt_time(ts: str, mode: str = 'trunc', dp: int = 1) -> str:
    """Format a timestamp to `dp` decimal places, keeping HH:MM:SS.s layout.

    mode='trunc' (default) truncates (10.297 -> 10.2); mode='round' rounds.
    """
    total = to_seconds(ts)
    quantum = Decimal(1).scaleb(-dp)  # dp=1 -> 0.1
    rounding = ROUND_HALF_UP if mode == 'round' else ROUND_DOWN
    total = total.quantize(quantum, rounding=rounding)
    secs_int = int(total)
    h = secs_int // 3600
    m = (secs_int - h * 3600) // 60
    s = total - Decimal(h * 3600 + m * 60)
    width = dp + 3 if dp > 0 else 2  # 2 int digits + '.' + dp frac digits
    return f"{h:02d}:{m:02d}:{s:0{width}.{dp}f}"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_vtt(text: str) -> list[Cue]:
    text = text.lstrip('\ufeff')  # strip BOM if present
    lines = text.splitlines()
    ts_idx = [i for i, ln in enumerate(lines) if CUE_RE.match(ln)]
    cues: list[Cue] = []
    for n, t in enumerate(ts_idx):
        m = CUE_RE.match(lines[t])
        start_raw, end_raw = m.group(1), m.group(2)
        end_block = ts_idx[n + 1] if n + 1 < len(ts_idx) else len(lines)
        block_lines = lines[t + 1:end_block]
        # Drop Teams cue identifiers that may sit between cues (no blank line).
        block_lines = [b for b in block_lines if not TEAMS_ID_RE.match(b.strip())]
        block = "\n".join(block_lines).strip()
        speaker, content = _extract_voice(block)
        cues.append(Cue(start_raw, end_raw, speaker, content))
    return cues


def _extract_voice(block: str) -> tuple[Optional[str], str]:
    m = VOICE_CLOSED_RE.search(block)
    if m:
        speaker, inner = m.group(1), m.group(2)
    else:
        m = VOICE_OPEN_RE.search(block)
        if m:
            speaker, inner = m.group(1), m.group(2)
        else:
            # Generic VTT without a <v> tag: take up to the first blank line.
            speaker, inner = None, block.split('\n\n', 1)[0]
    inner = TAG_RE.sub(' ', inner)          # strip <v ...>, </v>, <i>, timestamps
    inner = html.unescape(inner)            # &amp; -> & etc.
    inner = re.sub(r'\s+', ' ', inner).strip()
    inner = re.sub(r'\s+([.,;:!?])', r'\1', inner)  # tidy spacing left by tag removal
    speaker = speaker.strip() if speaker else None
    return speaker, inner


# --------------------------------------------------------------------------- #
# Cleaning: short / filler responses
# --------------------------------------------------------------------------- #
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
# Any word character in ANY script (letters/digits), so non-Latin transcripts
# (e.g. Mandarin) are not mistaken for empty.
WORD_RE = re.compile(r"\w", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def has_text(text: str) -> bool:
    """True if the string contains at least one real word character (any script).
    Empty strings and punctuation-only strings ('.', '—', '...') return False."""
    return bool(WORD_RE.search(text))


def is_filler_only(text: str, fillers) -> bool:
    """True if the utterance is only filler/backchannel words, or has no text at
    all. Non-Latin text with no ASCII tokens is NOT treated as filler."""
    if not has_text(text):
        return True
    toks = _tokens(text)
    if not toks:
        return False  # has text (e.g. CJK) but no ASCII tokens -> keep
    return all(t in fillers for t in toks)


def filter_cues(cues, min_words=0, drop_fillers=False, fillers=None):
    fillers = DEFAULT_FILLERS if fillers is None else fillers
    out = []
    for c in cues:
        if min_words and len(_tokens(c.content)) < min_words:
            continue
        if drop_fillers and is_filler_only(c.content, fillers):
            continue
        out.append(c)
    return out


# --------------------------------------------------------------------------- #
# Speaker remapping
# --------------------------------------------------------------------------- #
def auto_name(raw: str) -> str:
    """ 'Smith, Tom (Org)' -> 'Tom Smith'.  'Last, First Middle' -> 'First Middle Last'. """
    s = re.sub(r'\s*\([^)]*\)\s*$', '', raw).strip()  # drop trailing (Org)
    if ',' in s:
        last, first = s.split(',', 1)
        return f"{first.strip()} {last.strip()}".strip()
    return s


def detect_speakers(cues: list[Cue]) -> list[str]:
    order: list[str] = []
    for c in cues:
        if c.speaker and c.speaker not in order:
            order.append(c.speaker)
    return order


def resolve_speakers(cues, mapping=None, auto=False, anonymise=False) -> dict[str, str]:
    mapping = dict(mapping or {})
    order = detect_speakers(cues)
    resolved: dict[str, str] = {}
    counter = 1
    for spk in order:
        if spk in mapping:
            resolved[spk] = mapping[spk]
        elif anonymise:
            resolved[spk] = f"Speaker {counter}"
            counter += 1
        elif auto:
            resolved[spk] = auto_name(spk)
        else:
            resolved[spk] = spk
    return resolved


def interactive_map(speakers, defaults=None) -> dict[str, str]:
    defaults = defaults or {}
    mapping = {}
    print("\nDetected speakers (press Enter to accept the suggestion):", file=sys.stderr)
    for i, spk in enumerate(speakers, 1):
        suggestion = defaults.get(spk) or auto_name(spk)
        try:
            ans = input(f"  [{i}] {spk!r}  ->  [{suggestion}]: ").strip()
        except EOFError:
            ans = ''
        mapping[spk] = ans if ans else suggestion
    return mapping


# --------------------------------------------------------------------------- #
# Row building / output
# --------------------------------------------------------------------------- #
def merge_cues(cues: list[Cue]) -> list[Cue]:
    """Merge consecutive cues spoken by the same person into one row."""
    merged: list[Cue] = []
    for c in cues:
        if merged and c.speaker is not None and merged[-1].speaker == c.speaker:
            prev = merged[-1]
            merged[-1] = Cue(prev.start, c.end, prev.speaker,
                             (prev.content + ' ' + c.content).strip())
        else:
            merged.append(c)
    return merged


def make_contiguous(cues: list[Cue]) -> list[Cue]:
    """Close gaps in the timeline by setting each entry's end time to the next
    entry's start time. Prevents NVivo from inserting blank entries to cover
    untranscribed gaps (e.g. where filler rows were removed). The final entry
    keeps its own end time."""
    for i in range(len(cues) - 1):
        cues[i].end = cues[i + 1].start
    return cues


def _clean_cell(value: str) -> str:
    # Fields are single-line by construction; guard against stray tabs/newlines.
    return value.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')


# A trailing comma/semicolon on the content field collides with NVivo's
# delimited-transcript parser, which can read the next line's timestamp as part
# of this entry's content. Strip trailing separators (keep . ? ! etc.).
_TRAILING_SEP_RE = re.compile(r'[\s,;]+$')


def _strip_trailing_sep(text: str) -> str:
    return _TRAILING_SEP_RE.sub('', text)


def build_rows(cues, resolved, columns, mode='trunc', dp=1, keep_empty=False,
               speaker_sep=': ', timespan_sep=' - ', strip_trailing_sep=True):
    rows = []
    for c in cues:
        content = _strip_trailing_sep(c.content) if strip_trailing_sep else c.content
        if not has_text(content) and not keep_empty:
            continue
        start = fmt_time(c.start, mode, dp)
        end = fmt_time(c.end, mode, dp)
        spk = (resolved.get(c.speaker, c.speaker) if c.speaker else '') or ''
        dialogue = f"{spk}{speaker_sep}{content}" if spk else content
        cell = {
            'start': start,
            'end': end,
            'timespan': f"{start}{timespan_sep}{end}",
            'speaker': spk,
            'content': content,
            'dialogue': dialogue,
        }
        rows.append([_clean_cell(cell[col]) for col in columns])
    return rows


def write_tsv(path, columns, rows, header=True, encoding='utf-8'):
    lines = []
    if header:
        lines.append('\t'.join(COLUMN_HEADERS[c] for c in columns))
    lines.extend('\t'.join(r) for r in rows)
    # Join with newlines but do NOT leave a trailing empty line: NVivo treats a
    # blank final line as a transcript row with no content (and an inconsistent
    # tab count), which shows up as an empty entry after import.
    with open(path, 'w', encoding=encoding, newline='') as f:
        f.write('\n'.join(lines))
        if lines:
            f.write('\n')  # single terminating newline, no blank line after it


def _f4_timestamp(ts_str: str) -> str:
    """'00:00:11.2' -> '#00:00:11-2#' (f4/f5 inline timestamp)."""
    return f"#{ts_str.replace('.', '-')}#"


def build_f4_lines(cues, resolved, mode='trunc', dp=1, speaker_sep=': ',
                   strip_trailing_sep=True, keep_empty=False):
    """f4/f5 format: one paragraph per turn, 'Speaker: text #hh:mm:ss-t#'.
    The trailing timestamp is the turn's END, which NVivo reads as the start of
    the next turn -- so the timeline chains with no gaps and no blank rows."""
    lines = []
    for c in cues:
        content = _strip_trailing_sep(c.content) if strip_trailing_sep else c.content
        if not has_text(content) and not keep_empty:
            continue
        spk = (resolved.get(c.speaker, c.speaker) if c.speaker else '') or ''
        prefix = f"{spk}{speaker_sep}" if spk else ""
        stamp = _f4_timestamp(fmt_time(c.end, mode, dp))
        lines.append(_clean_cell(f"{prefix}{content} {stamp}"))
    return lines


def write_text(path, lines, encoding='utf-8'):
    with open(path, 'w', encoding=encoding, newline='') as f:
        f.write('\n'.join(lines))
        if lines:
            f.write('\n')


# --------------------------------------------------------------------------- #
# Mapping inputs
# --------------------------------------------------------------------------- #
def load_mapping_file(path: str) -> dict[str, str]:
    if path.lower().endswith('.json'):
        with open(path, encoding='utf-8') as f:
            return {str(k): str(v) for k, v in json.load(f).items()}
    # CSV / TSV: two columns, raw,label  (header optional)
    import csv
    mapping = {}
    with open(path, encoding='utf-8', newline='') as f:
        sniff = f.read(2048)
        f.seek(0)
        delim = '\t' if '\t' in sniff and ',' not in sniff.split('\n', 1)[0] else ','
        for row in csv.reader(f, delimiter=delim):
            if len(row) < 2:
                continue
            if row[0].strip().lower() in {'raw', 'speaker', 'original'}:
                continue  # header
            mapping[row[0].strip()] = row[1].strip()
    return mapping


def parse_map_args(map_args) -> dict[str, str]:
    mapping = {}
    for item in map_args:
        if '=' not in item:
            raise ValueError(f"--map expects RAW=LABEL, got: {item!r}")
        raw, label = item.split('=', 1)
        mapping[raw.strip()] = label.strip()
    return mapping


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #
def expand_inputs(inputs) -> list[str]:
    files: list[str] = []
    for item in inputs:
        if os.path.isdir(item):
            files.extend(sorted(globlib.glob(os.path.join(item, '*.vtt'))))
        elif any(ch in item for ch in '*?['):
            files.extend(sorted(globlib.glob(item)))
        else:
            files.append(item)
    # de-duplicate, preserve order
    seen, out = set(), []
    for fpath in files:
        if fpath not in seen:
            seen.add(fpath)
            out.append(fpath)
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def convert_file(in_path, out_path, *, columns, header, mode, dp, keep_empty,
                 base_mapping, auto, anonymise, interactive, merge, encoding,
                 speaker_sep=': ', min_words=0, drop_fillers=False, fillers=None,
                 timespan_sep=' - ', strip_trailing_sep=True, contiguous=True,
                 out_format='f4'):
    with open(in_path, encoding='utf-8-sig') as f:
        text = f.read()
    cues = parse_vtt(text)
    if not keep_empty:
        cues = [c for c in cues if has_text(c.content)]
    cues = filter_cues(cues, min_words=min_words, drop_fillers=drop_fillers,
                       fillers=fillers)
    if merge:
        cues = merge_cues(cues)
    # The f4 format chains turns via end-timestamps, so it needs no gap closing.
    if contiguous and out_format == 'tsv':
        cues = make_contiguous(cues)

    if interactive:
        speakers = detect_speakers(cues)
        mapping = interactive_map(speakers, defaults=base_mapping)
        resolved = resolve_speakers(cues, mapping=mapping)
    else:
        resolved = resolve_speakers(cues, mapping=base_mapping,
                                    auto=auto, anonymise=anonymise)

    if out_format == 'f4':
        lines = build_f4_lines(cues, resolved, mode=mode, dp=dp,
                               speaker_sep=speaker_sep,
                               strip_trailing_sep=strip_trailing_sep,
                               keep_empty=keep_empty)
        write_text(out_path, lines, encoding=encoding)
        return len(lines)

    rows = build_rows(cues, resolved, columns, mode=mode, dp=dp,
                      keep_empty=keep_empty, speaker_sep=speaker_sep,
                      timespan_sep=timespan_sep, strip_trailing_sep=strip_trailing_sep)
    write_tsv(out_path, columns, rows, header=header, encoding=encoding)
    return len(rows)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Convert Teams .vtt transcripts to NVivo 15 tab-delimited text.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('inputs', nargs='+', help='.vtt file(s), glob(s), or folder(s)')
    p.add_argument('-o', '--output', help='output path (single input only)')
    p.add_argument('--outdir', help='output directory for batch mode')
    p.add_argument('--suffix', default='', help="append to output stem, e.g. '_nvivo'")
    p.add_argument('--format', choices=['f4', 'tsv'], default='f4',
                   help="output format: 'f4' (default) = f4/f5 inline plain text "
                        "'Speaker: text #hh:mm:ss-t#', no header, no gaps; "
                        "'tsv' = tab-delimited columns (uses --columns etc.)")
    p.add_argument('--columns', default='start,end,dialogue',
                   help='[tsv only] comma list of: start,end,timespan,speaker,'
                        'content,dialogue (dialogue = "Speaker: text")')
    p.add_argument('--speaker-sep', default=': ',
                   help="separator between speaker and text in the dialogue "
                        "column (default ': ')")
    p.add_argument('--timespan-sep', default=' - ',
                   help="separator between start and end in the timespan column "
                        "(default ' - '; NVivo also accepts '-' or '/')")
    p.add_argument('--keep-gaps', action='store_true',
                   help="don't close timeline gaps; keep each cue's true end "
                        "time (by default ends are stretched to the next start "
                        "so NVivo doesn't insert blank entries for the gaps)")
    p.add_argument('--keep-trailing-comma', action='store_true',
                   help="keep trailing commas/semicolons on content (off by "
                        "default; trailing separators can break NVivo import)")
    p.add_argument('--no-header', action='store_true', help='omit the header row')
    p.add_argument('--auto-name', action='store_true',
                   help="reformat 'Last, First (Org)' -> 'First Last'")
    p.add_argument('--anonymise', '--anonymize', dest='anonymise',
                   action='store_true', help='replace names with Speaker 1, 2, ...')
    p.add_argument('--map', action='append', default=[], metavar='RAW=LABEL',
                   help='explicit speaker mapping (repeatable)')
    p.add_argument('--mapping-file', help='JSON object or CSV/TSV of raw,label')
    p.add_argument('--interactive', action='store_true',
                   help='prompt for each speaker label, per file')
    p.add_argument('--merge', action='store_true',
                   help='merge consecutive cues from the same speaker')
    p.add_argument('--drop-fillers', action='store_true',
                   help='drop utterances made up only of filler/backchannel '
                        'words (e.g. "yeah, hmm")')
    p.add_argument('--min-words', type=int, default=0, metavar='N',
                   help='drop utterances with fewer than N words (0 = keep all)')
    p.add_argument('--filler-words', metavar='LIST',
                   help='comma-separated filler list to use instead of the '
                        'default (e.g. "yeah,hmm,mm,right,yes,no")')
    p.add_argument('--version', action='version',
                   version=f'%(prog)s {__version__}')
    p.add_argument('--round', dest='round_mode', action='store_true',
                   help='round times instead of truncating')
    p.add_argument('--decimals', type=int, default=1, help='decimal places (default 1)')
    p.add_argument('--keep-empty', action='store_true',
                   help='keep cues with no words (empty or punctuation-only); '
                        'by default these rows are removed')
    p.add_argument('--encoding', default='utf-8',
                   help="output encoding (use 'utf-8-sig' if NVivo garbles accents)")
    args = p.parse_args(argv)

    columns = [c.strip() for c in args.columns.split(',') if c.strip()]
    bad = [c for c in columns if c not in COLUMN_HEADERS]
    if bad:
        p.error(f"unknown column(s): {', '.join(bad)}. "
                f"Choose from: {', '.join(COLUMN_HEADERS)}")

    try:
        base_mapping = parse_map_args(args.map)
    except ValueError as e:
        p.error(str(e))
    if args.mapping_file:
        base_mapping.update(load_mapping_file(args.mapping_file))

    files = expand_inputs(args.inputs)
    if not files:
        p.error("no input files found")
    if args.output and len(files) > 1:
        p.error("--output works with a single input; use --outdir for batches")

    mode = 'round' if args.round_mode else 'trunc'
    fillers = DEFAULT_FILLERS
    if args.filler_words:
        fillers = {w.strip().lower() for w in args.filler_words.split(',') if w.strip()}
    total_rows = 0
    for in_path in files:
        if args.output:
            out_path = args.output
        else:
            stem = os.path.splitext(os.path.basename(in_path))[0] + args.suffix + '.txt'
            outdir = args.outdir or os.path.dirname(os.path.abspath(in_path))
            os.makedirs(outdir, exist_ok=True)
            out_path = os.path.join(outdir, stem)

        n = convert_file(
            in_path, out_path,
            columns=columns, header=not args.no_header, mode=mode,
            dp=args.decimals, keep_empty=args.keep_empty,
            base_mapping=base_mapping, auto=args.auto_name,
            anonymise=args.anonymise, interactive=args.interactive,
            merge=args.merge, encoding=args.encoding, speaker_sep=args.speaker_sep,
            min_words=args.min_words, drop_fillers=args.drop_fillers, fillers=fillers,
            timespan_sep=args.timespan_sep,
            strip_trailing_sep=not args.keep_trailing_comma,
            contiguous=not args.keep_gaps,
            out_format=args.format,
        )
        total_rows += n
        print(f"{in_path}  ->  {out_path}  ({n} rows)", file=sys.stderr)

    print(f"Done: {len(files)} file(s), {total_rows} rows.", file=sys.stderr)


if __name__ == '__main__':
    main()
