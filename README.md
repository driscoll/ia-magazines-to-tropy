# ia-magazines-to-tropy

Turn a collection of magazines on the [Internet Archive](https://archive.org) into a
collection in [Tropy](https://tropy.org) with rich, searchable metadata about every cover.

Magazine covers mix text and images, often with stylized lettering that ordinary OCR
handles badly. This tool downloads the cover of each issue, has a vision-language model
describe it (a transcription of the cover text, the date, prices, names, topics, and a
description of the artwork), lets a person review and correct every record in a local web
page, and exports the result for Tropy.

```
Internet Archive collection
1. fetch     download the cover (first page image) of each issue
2. prepare   make smaller copies for the model
3. describe  a model writes a record for each cover
4. review    you check and correct each record
5. export    CSV + images for Tropy's CSV import plugin
Tropy collection
```

It was built for [BYTE magazine](https://archive.org/details/byte-magazine) and works
with other magazine collections through a short profile file. The method, the
reasoning behind its settings, and a comparison of three models are in
[docs/method.md](docs/method.md).

## What you need

- **[uv](https://docs.astral.sh/uv/getting-started/installation/)**, which installs
Python and the other dependencies for you.
- **A model**, one of:
- **Local, with [Ollama](https://ollama.com/download)**: the covers never leave your
computer, and the model's full reasoning is kept with each record.
- **Hosted, with an [Anthropic API key](https://console.anthropic.com/)** (Claude):
fast, and runs on any computer, including Windows machines without a suitable GPU.
The covers are sent to Anthropic, and each cover costs a few cents.
- **[Tropy](https://tropy.org)** and its CSV import plugin, for the final step.
- **Disk space**: about 6 MB per cover for the originals and the model's copies, plus a
copy of the originals in each Tropy export.

### Choosing a model

Measured on 10 BYTE covers (1975-1997) with the same prompt. Local times are from one
workstation (AMD Strix Halo, 128 GB unified memory); expect a laptop to be slower.

| Model | Runs on | Time per cover (median) | 350 covers | Cost |
|---|---|---|---|---|
| `qwen3-vl:8b` (local) | Mac with Apple silicon and 16 GB memory or more; PC with a recent GPU | about 2.3 min | about 15 h | none |
| `qwen3-vl:32b` (local, the default) | 48 GB memory or more | about 7 min | about 2 days | none |
| `claude-opus-5-5` (hosted) | any computer with internet | about 15 s | about 1.5 h | about $0.06 per cover |

All three produced a record for every test cover. They differ in how often they break the
prompt's rules and how often they report their own uncertainty; see
[docs/method.md](docs/method.md#comparing-three-models). Every record is reviewed by a
person either way.

## Install

```sh
git clone https://github.com/driscoll/ia-magazines-to-tropy.git
cd ia-magazines-to-tropy
uv sync                        # local models only
uv sync --extra anthropic      # also the hosted (Claude) option
```

For a local model, install Ollama, start it, and download a model once:

```sh
ollama pull qwen3-vl:32b       # or qwen3-vl:8b on a smaller machine
```

Run every command from the `ia-magazines-to-tropy` folder. Each step takes a **profile**
name: `byte` for BYTE, or your own (see [A new collection](#a-new-collection)).

## Quick start: five BYTE covers

`--limit 5` keeps the download to the first five issues; without it, `fetch` downloads
the cover of every issue in the collection (350 for BYTE).

```sh
uv run ia2tropy fetch byte --limit 5
uv run ia2tropy prepare byte
uv run ia2tropy describe byte --model qwen3-vl:8b
uv run ia2tropy review byte          # open http://127.0.0.1:8000, review, then Ctrl-C
uv run ia2tropy export byte
```

Everything a run produces goes in `data/byte/`, which git ignores.

## Step by step

### 1. Fetch the covers

```sh
uv run ia2tropy fetch byte
```

Downloads the first page image (normally the cover) of each item in the profile's
Internet Archive collection, as a JPEG 2000 file, into `data/byte/originals/`. Only that
one image is downloaded per item, not the whole scan. `data/byte/manifest.tsv` records
which file came from which item. Items without a page-image archive are reported and
skipped. Re-running skips covers you already have, so you can start small and add more.

**By default it fetches the whole collection.** To work on a subset:

| Option | Fetches | Example |
|---|---|---|
| `--limit N` | the first N items, sorted by identifier | `--limit 5` |
| `--items ID ...` | exactly these items | `--items byte-magazine-1987-05 byte-magazine-1993-06` |
| `--query "..."` | items in the collection that match an [Internet Archive search](https://archive.org/advancedsearch.php) | `--query "year:1985"` or `--query "year:[1980 TO 1984]"` |

`--limit` also works with `--query`, e.g. `--query "year:1985" --limit 3`. An item's
identifier is the last part of its URL: `https://archive.org/details/<identifier>`.

The later steps work on whatever has been fetched, so a subset carries through the
whole pipeline. `describe` also has `--limit N` and `--images NAME ...` to describe only
some of the fetched covers.

### 2. Prepare images for the model

```sh
uv run ia2tropy prepare byte
```

Writes PNG copies no larger than 2048 pixels on the long edge to `data/byte/model-input/`.
Smaller images saved little time in testing and lost small print on dense covers. The
originals are kept for Tropy.

### 3. Describe the covers

Local model:

```sh
uv run ia2tropy describe byte                         # qwen3-vl:32b
uv run ia2tropy describe byte --model qwen3-vl:8b     # smaller and faster
```

Hosted model (Claude), after [storing your API key](#using-claude-anthropic-api):

```sh
uv run ia2tropy describe byte --backend anthropic --api-key-file ~/.config/anthropic-key
```

Each record is appended to `data/byte/descriptions.jsonl` as soon as it is done, together
with the model's reasoning, timings, and [provenance](#what-is-recorded). Progress is
logged to `data/byte/describe.log`. You can stop the run and start it again: covers
already described with the same model and prompt are skipped. A local run that runs out
of tokens on a cover retries it once automatically; `--retry-failed` reruns any that
still have no record. Try `--limit 3` first to see how long a cover takes on your machine.

A long local run should survive a closed terminal. On macOS or Linux, use `tmux`, or:

```sh
nohup uv run ia2tropy describe byte > data/byte/describe.out 2>&1 &
```

### 4. Review and correct

```sh
uv run ia2tropy review byte
```

Open <http://127.0.0.1:8000>. The list shows every cover, its review status (filter by
unreviewed, approved or corrected), and how many flags it has. Each cover page shows the image, which
stays in view while you scroll, next to a form with its record:

- **Flags** at the top are automatic consistency checks: a name missing from the
  transcription, a date or price that disagrees with the printed text, text from the
  artwork listed as cover text, cut-off text without a `[?]` marker. A flag is something
  to look at, not always an error.
- **Lists** are one item per line; lists of parts use ` | ` between them, e.g.
  `$2.50 | 2.5 | USD | ` for a price.
- **Cover artist**: enter it from the magazine's own credits. The model is told to ignore
  signatures, because it misreads them and cannot know who painted a cover.
- **Model reasoning** (for local models) is in a collapsible section; it often shows
  where the model was unsure.
- **Save** keeps you on the cover; **Save and next** moves on, and returns to the list
  after the last cover. A save without edits is recorded as *approved*, with edits as
  *corrected*.

The model's output is never changed. Your saves go to `data/byte/corrections.jsonl`,
and the latest save for a cover wins. Your name comes from `git config user.name`, or
pass `--reviewer "Your Name"`.

The page has no login and listens only on your own computer. To review on a computer
other than the one running the tool, use an SSH tunnel rather than opening it to the
network. On the computer you are sitting at:

```sh
ssh -N -L 8000:127.0.0.1:8000 you@the-other-computer
```

then open <http://localhost:8000>.

### 5. Export to Tropy

```sh
uv run ia2tropy export byte
```

Writes `data/byte/tropy-export/`, containing `byte.csv` and the original images in
`images/`. Only reviewed covers are exported
(`--include-unreviewed` exports all). Keep the folder together; the CSV refers to the
images by relative path. Copy the whole folder if Tropy runs on another computer.

| Tropy field | Comes from |
|---|---|
| `dc:title` | masthead and date from the record, e.g. "BYTE, May 1987" |
| `dc:date` | year and month from the record, e.g. `1987-05` |
| `dc:creator` | the cover artist you entered |
| `dc:publisher` | publisher as printed |
| `dcterms:abstract` | one-sentence description |
| `dc:description` | description of the artwork |
| `dcterms:medium` | painting, illustration, photograph, ... |
| `dc:subject` | topics and names, joined with "; " |
| `dc:identifier`, `dc:source` | the Internet Archive item and its link |
| `dcterms:provenance` | model, prompt version, review status, reviewer, date |
| photo note | the cover text, one piece per line |

The model's reasoning and any mailing-label text stay out of the export.

**Importing into Tropy** (once per Tropy installation, except the last step):

1. **Install the CSV plugin.** Download the `tropy-plugin-csv` ZIP from its
   [releases page](https://github.com/tropy/tropy-plugin-csv/releases/latest). In Tropy,
   open **Preferences > Plugins** (Windows: **Edit > Preferences > Plugins**), click
   **Install Plugin**, choose the ZIP without unzipping it, then click **Enable**.
2. **Create an item template.** In **Preferences > Templates**, create a new template
   (for example "Magazine cover") and add one field for each property printed at the end
   of the export command: Dublin Core title, date, creator, publisher, description,
   subject, identifier and source; and DC Terms abstract, medium and provenance.
3. **Set up the plugin.** In **Preferences > Plugins**, open the plugin's **Settings**:
   choose a name (for example "Magazine CSV"), your item template, any photo template,
   and a comma as the delimiter.
4. **Import.** Choose **File > Import > Magazine CSV** (the name you chose) and select
   the CSV file.

**Optional: the Internet Archive's own metadata.** Every item links to its Internet
Archive page (`dc:source`), where the Archive's metadata can be read. To keep a copy of
it in Tropy as well, for comparison with your records, export with `--with-ia`:

```sh
uv run ia2tropy export byte --with-ia
```

This fetches each item's metadata from the Archive (cached in
`data/byte/ia-metadata.jsonl`; `--refresh-ia` fetches it again) and adds seven fields:
`ia_title`, `ia_description`, `ia_language`, `ia_identifier`, `ia_collection`,
`ia_subject` and `ia_imagecount`. Tropy needs a small vocabulary that defines them,
which the export writes to `ia-vocabulary.ttl`. Two additions to the steps above:

- **Before creating the template, add the vocabulary.** In **Preferences >
  Vocabularies**, click **+** and choose `ia-vocabulary.ttl` from the export folder.
  This is needed once per Tropy installation; later exports use the same vocabulary. If
  Tropy reports `UNIQUE constraint failed: vocabularies.prefix`, a vocabulary with the
  same prefix is already installed (often from an earlier import of this file): remove
  it in **Preferences > Vocabularies** first, or skip this step if it is the same one.
- **Add the seven `ia_` properties to the item template.**

The vocabulary is this project's own, not the Internet Archive's; each field links to
its definition in the Archive's
[metadata schema](https://archive.org/developers/metadata-schema/index.html).

## Using Claude (Anthropic API)

1. Create an API key in the [Anthropic Console](https://console.anthropic.com/).
2. Save it in a file **outside this folder**, readable only by you:

   macOS / Linux:

   ```sh
   mkdir -p ~/.config
   nano ~/.config/anthropic-key        # paste the key, save, exit
   chmod 600 ~/.config/anthropic-key
   ```

   Windows (PowerShell):

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.config" | Out-Null
   notepad "$env:USERPROFILE\.config\anthropic-key"     # paste the key, save
   ```

3. Point the tool at the file:

   ```sh
   uv run ia2tropy describe byte --backend anthropic --api-key-file ~/.config/anthropic-key
   ```

   On Windows: `--api-key-file "$env:USERPROFILE\.config\anthropic-key"`. The tool also
   reads the `ANTHROPIC_API_KEY` environment variable if you prefer to set that.

> **Keep credentials out of the repository.** Never put an API key in a file inside this
> folder, in a profile, or in a commit, and don't type it on the command line, where it
> is saved in your shell history. The tool refuses a key file located inside the project
> folder, and `.gitignore` excludes common key file names, but that is a safety net, not
> a guarantee. If a key is ever exposed, revoke it in the Anthropic Console and create a
> new one.

With the hosted model the covers are sent to Anthropic for processing, and the model
decides for itself how much to reason; when it does, the API returns a summary of its
reasoning rather than the full text. Use `--effort medium` to make it cheaper and faster.
Each raw API response is also saved in `data/<profile>/anthropic-raw/`.

## A new collection

Copy `profiles/byte.toml` to `profiles/<name>.toml` and edit it:

- `name`: the folder name under `data/`.
- `collection`: the Internet Archive collection identifier, from its URL
  (`https://archive.org/details/<collection>`).
- `title` and `description`: how the prompt introduces the magazine.
- `[examples]`: a masthead, tagline and publisher line, names, generic terms and topics
  typical of the covers. Leave an example empty (`""`) if you are not sure; a wrong
  example steers the model more than a missing one.
- `[prices]`: the currency to assume for a "$" price with no country stated, or `""`.

`profiles/compute.toml` (for COMPUTE! magazine) is a second example. Before a full run,
fetch and describe two or three of the hardest covers (dense text, unusual layout), read
the records and the reasoning, and adjust the examples. Browse the collection on the
Internet Archive to pick them, then fetch just those with `--items`:

```sh
uv run ia2tropy fetch compute --items 1983-01-compute-magazine 1983-02-compute-magazine
uv run ia2tropy prepare compute
uv run ia2tropy describe compute --model qwen3-vl:8b
```

The rules in `prompts/cover_prompt.txt` apply to any magazine cover. If you change the
prompt or schema, increase `PROMPT_VERSION` in `src/ia_magazines_to_tropy/config.py`.

## What is recorded

Each record in `descriptions.jsonl` keeps: the model and its digest, the prompt version
and a SHA-256 hash of the exact prompt and schema, the sampling settings, token counts
and timings, the model's reasoning, any failed attempts, and the environment (this
repository's git commit and whether it had uncommitted changes, the tool, Ollama and
Python versions, the computer's name and operating system, and the start time). Each
correction keeps the reviewer, the time, approved or corrected, the source model and
prompt, and the full corrected record. Together with the manifest, that documents how
every catalog record was made.

## Cautions

- **The first page image is assumed to be the cover.** Check covers that look wrong;
  some scans start with a blank page or a back cover.
- **Mailing labels** on library or subscriber copies can carry names and addresses. They
  are transcribed into the record (so you can see them) but never exported.
- **Rights**: this tool does not change the rights status of the images. Check the terms
  under which each collection is made available before republishing covers.
- **Be polite to the Internet Archive**: the fetch step downloads one file per item with
  a few connections at a time. Don't raise that.
- **Every record needs review.** The models misread stylized text, sometimes include text
  from the artwork or skip a rule, and rarely report their own uncertainty.

## License

MIT; see [LICENSE](LICENSE).
