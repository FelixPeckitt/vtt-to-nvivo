# vtt-to-nvivo

Convert Microsoft Teams meeting transcripts (`.vtt` / WebVTT) into tidy,
tab-delimited plain-text files ready to import as **NVivo 15** transcripts.

It handles the awkward bits of Teams `.vtt` output for you: voice tags, multi-line
wrapping, cue identifiers, `NOTE` blocks, byte-order marks and HTML entities. It
reformats speaker labels, optionally strips backchannel filler, and writes a
clean transcript in the column layout NVivo expects.

- **No dependencies** — pure Python standard library.
- **Python 3.8+**
- Single file, easy to drop into a repo or a researcher's toolkit.

---

## What it does

The default output is the **f4/f5 inline format** — one paragraph per turn, with
the timestamp at the *end* of each turn as `#hh:mm:ss-t#`:

```
Tom Smith: Thanks so much for agreeing to interview. #00:00:06-9#
Speaker 1: And this is for an example interview #00:00:18-0#
```

NVivo reads each trailing timestamp as the end of that turn *and* the start of
the next, so the timeline chains with no gaps — which means **no blank "silence"
rows** after import, and the lead-in before the first word folds into the first
entry automatically. This is the format to use for NVivo.

A tab-delimited columnar output is also available with `--format tsv` (see
[Column layouts](#column-layouts)).

Timestamps are reduced to tenths of a second (`00:00:03.417` → `…-4`), truncated
by default (`--round` to round instead).

### Importing the f4 output into NVivo 15

1. Open the audio/video source in **edit mode** → **Import Transcript Entries**.
2. For **Create one transcript row for each**, choose **Timestamp**.
3. NVivo detects speakers from the `Name:` prefix and times from the `#…#`
   stamps; no field mapping or header is needed.
4. If accented characters look wrong, re-export with `--encoding utf-8-sig`.

---

## Install

```bash
git clone https://github.com/<you>/vtt-to-nvivo.git
cd vtt-to-nvivo
# nothing to install — it's standard library only
python vtt_to_nvivo.py --help
```

Optionally make it executable:

```bash
chmod +x vtt_to_nvivo.py
./vtt_to_nvivo.py interview.vtt --auto-name
```

---

## Quick start

```bash
# Reformat "Smith, Tom (Org)" -> "Tom Smith"
python vtt_to_nvivo.py interview.vtt --auto-name

# Anonymise speakers to "Speaker 1", "Speaker 2", ...
python vtt_to_nvivo.py interview.vtt --anonymise

# Remove backchannel filler ("yeah", "mm-hmm") for a cleaner read
python vtt_to_nvivo.py interview.vtt --auto-name --drop-fillers

# Convert a whole folder of .vtt files into ./nvivo
python vtt_to_nvivo.py ./transcripts --outdir ./nvivo --auto-name
```

Output is written next to each input as `<name>.txt`, or into `--outdir`.

---

## Speaker labels

Teams produces labels like `Smith, Tom (Org)`. Choose how they should appear
(listed highest precedence first):

| Option | Effect |
|--------|--------|
| `--map "Smith, Tom (Org)=Tom Smith"` | Explicit rename (repeatable). |
| `--mapping-file map.json` / `map.csv` | Bulk renames from a file. |
| `--interactive` | Prompts you for each detected speaker, per file. |
| `--anonymise` | `Speaker 1`, `Speaker 2`, … in order of first appearance. |
| `--auto-name` | `Last, First (Org)` → `First Last`. |

With none of these, the original Teams label is kept verbatim.

A mapping file is either a JSON object:

```json
{ "Smith, Tom (Org)": "Tom Smith", "Jones, Asha (Org)": "Asha Jones" }
```

or a two-column CSV/TSV (`raw,label`, header optional):

```csv
raw,label
"Smith, Tom (Org)",Tom Smith
"Jones, Asha (Org)",Asha Jones
```

---

## Cleaning short responses

Backchannel interjections clutter qualitative transcripts. Two independent
filters remove them:

| Option | Effect |
|--------|--------|
| `--drop-fillers` | Drops utterances made up **only** of filler/backchannel words (e.g. `yeah, hmm`, `mm-hmm`). A turn that merely *contains* a filler is kept. |
| `--min-words N` | Drops any utterance shorter than `N` words. Blunter — can remove short but meaningful answers. |
| `--filler-words "yeah,hmm,mm,right"` | Replace the built-in filler list with your own. |

**`yes` and `no` are not treated as filler by default**, because in interviews
they are frequently the answer. Add them via `--filler-words` if your data
warrants it.

Filters run **before** `--merge`, so removing an interjection lets the turns on
either side of it merge into one clean block:

```bash
python vtt_to_nvivo.py interview.vtt --auto-name --drop-fillers --merge
```

Built-in filler set: `uh, um, er, erm, ah, oh, hmm, mm, mmm, mhm, mm-hmm,
uh-huh, huh, yeah, yep, yup, nah, okay, ok, right` (and common spelling
variants).

**Timeline gaps (blank rows in NVivo).** NVivo builds a media transcript from
start times — each entry runs from its start to the *next* entry's start — so a
gap left by a removed cue becomes a blank entry on import. By default each
entry's end time is stretched to the next entry's start, leaving a continuous
timeline and no blank rows. Pass `--keep-gaps` to keep each cue's true end time
instead (you'll then likely see blank entries in NVivo wherever rows were
removed).

**Empty rows.** Cues with no words — empty cues, or punctuation-only fragments
like `.`, `...` or `—` that Teams occasionally emits — are removed by default so
you don't get blank entries in NVivo. Pass `--keep-empty` to retain them.
Non-Latin scripts (e.g. Mandarin) count as words and are always kept.

**Trailing separators.** Content is never allowed to end in a comma or
semicolon — a trailing separator collides with NVivo's delimited-transcript
parser and makes it read the *next* line's timestamp as part of this entry's
content (a common, confusing import error). Trailing commas (often just
speech-recognition artefacts) are stripped by default; internal commas and
terminal `. ? !` are untouched. Pass `--keep-trailing-comma` to keep them
verbatim.

---

## Column layouts

*(These apply only to `--format tsv`. The default f4 output above is
recommended for NVivo.)*

NVivo's transcript importer maps **one field to `Timespan`** (a single
`start - end` range) and **one field to `Content`** — it does *not* accept
separate "Start time"/"End time" columns. So for NVivo, use:

```bash
python vtt_to_nvivo.py interview.vtt --auto-name --columns timespan,dialogue
```

which produces:

| Timespan                  | Content                                            |
|---------------------------|----------------------------------------------------|
| 00:00:03.4 - 00:00:06.9   | Tom Smith: Thanks so much for agreeing to interview.|

Override the layout with `--columns` using any of `start`, `end`, `timespan`,
`speaker`, `content`, `dialogue`:

| `--columns` value | Result |
|-------------------|--------|
| `timespan,dialogue` *(recommended for NVivo)* | `00:00:03.4 - 00:00:06.9`, `Speaker: text` |
| `timespan,speaker,content` | Timespan, plus speaker and text in separate fields |
| `start,end,dialogue` *(default)* | Separate start/end (handy for reading, not for NVivo import) |

`--timespan-sep` changes the `" - "` separator (NVivo also accepts `-` or `/`).
`--speaker-sep` changes the `": "` in the combined column. `--no-header` omits
the header row.

### Importing into NVivo 15

1. Open the audio/video source in **edit mode** → **Import Transcript Entries**
   (or import the `.txt` via the transcript import dialog).
2. For **Create one transcript row for each**, choose **Tab Delimited Line**
   (not *Comma Delimited Line* — interview content is full of commas, which
   would break comma parsing).
3. If you exported with a header row, tick **File includes header row**.
4. In **Transcript Field Mappings**, map the `Timespan` column to **Timespan**
   and the content column to **Content** (type `Speaker` to create a speaker
   field if you used a separate speaker column).
5. If accented characters look wrong, re-export with `--encoding utf-8-sig`.

> ⚠️ **Do not open the exported `.txt` in Excel, Numbers or Google Sheets before
> importing.** They reinterpret `00:00:03.4` as a clock time, display it as
> `00:00:03`, and on save drop the tenths — or convert cells to serial numbers —
> which causes NVivo parse errors. Import the raw `.txt`, or inspect/edit it in a
> plain-text editor (Notepad, VS Code) only. If you must use Excel, import via
> **Data → From Text** and set every column's type to **Text**.

---

## All options

```text
positional:
  inputs                 .vtt file(s), glob(s), or folder(s)

output:
  -o, --output PATH      output path (single input only)
  --outdir DIR           output directory for batch mode
  --suffix STR           append to output stem, e.g. "_nvivo"
  --encoding ENC         output encoding (try "utf-8-sig" if NVivo garbles accents)

speakers:
  --map RAW=LABEL        explicit mapping (repeatable)
  --mapping-file PATH    JSON object or CSV/TSV of raw,label
  --interactive          prompt for each speaker, per file
  --anonymise            replace names with Speaker 1, 2, ...
  --auto-name            "Last, First (Org)" -> "First Last"

cleaning:
  --drop-fillers         drop utterances that are only backchannel filler
  --min-words N          drop utterances shorter than N words
  --filler-words LIST    comma-separated filler list (overrides the default)
  --merge                merge consecutive cues from the same speaker
  --keep-gaps            keep true cue end times (default closes timeline gaps
                         so NVivo does not insert blank entries)
  --keep-trailing-comma  keep trailing commas/semicolons on content (kept off
                         by default; trailing separators break NVivo import)

layout:
  --format {f4,tsv}      output format (default f4; tsv = columns below)
  --columns LIST         [tsv] start,end,timespan,speaker,content,dialogue
  --speaker-sep SEP      separator in the combined column (default ": ")
  --timespan-sep SEP     separator in the timespan column (default " - "; or "-" "/")
  --no-header            omit the header row
  --round                round timestamps instead of truncating
  --decimals N           decimal places for timestamps (default 1)
  --keep-empty           keep cues with no text

misc:
  --version
  -h, --help
```

---

## Transparency & AI disclosure

This project takes a deliberately transparent stance on AI, for two reasons.

**1. The transcripts are machine-generated.**
Microsoft Teams `.vtt` transcripts are produced by automated speech recognition
(an AI/ML model). They contain recognition errors, mis-attributed speakers, and
omissions. **This tool only reformats and cleans — it does not correct
transcription.** For research use, transcripts should be checked against the
source audio before analysis, and any cleaning applied here (e.g.
`--drop-fillers`) should be documented in your methods.

**2. The tool itself was built with AI assistance.**
This utility was written with the assistance of an AI coding model (Anthropic's
Claude) and reviewed and tested by the maintainer. It is provided as-is; please
review the code before using it on sensitive data.

*Maintainers: edit this section to name yourself and your review process, and
keep it accurate for your repository.*

---

## Contributing

Issues and pull requests welcome. The code is a single file with no runtime
dependencies; the parsing, cleaning, speaker-mapping and output stages are
separated into small functions that are straightforward to test.

---

## License

MIT — see [LICENSE](LICENSE).
