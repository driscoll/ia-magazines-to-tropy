# ia-magazines-to-tropy

Create a project on [Tropy](https://tropy.org/) based on the covers of magazines on the [Internet Archive](https://archive.org) with structured, searchable metadata about each item.

Magazine covers mix text and images, often with stylized lettering that ordinary OCR
handles badly. This tool downloads the cover of each issue and uses a vision-language model to describe the artwork and extract metadata including themes, topics, dates, and prices. Next, these metadata are run through a round of human review and the corrected results are written to CSV for import into Tropy. 

The project involves five steps:
1. **fetch**: download the cover (first page image) of each issue
2. **prepare**: make smaller copies for the model
3. **describe**: a model writes a record for each cover
4. **review**: you check and correct each record
5. **export**: CSV + images for Tropy's [CSV import plugin](https://github.com/tropy/tropy-plugin-csv) 

The initial development was tested on [BYTE](https://archive.org/details/byte-magazine) and [COMPUTE!](https://archive.org/details/compute-magazine). Details on the methodology,testing, and reasoning behind the default settings are in [docs/method.md](docs/method.md).

## Setting up your system 

You will need access to a **vision-language model (VLM)**. There are two options:
- **Local model (with [Ollama](https://ollama.com/download))**: the images never leave your computer and the environment is reproducible, but performance will vary according to your computer's hardware 
- **Claude (with an [Anthropic API key](https://console.anthropic.com/))**: the images are sent to Anthropic's servers, you need to pay a few cents per image, but it will work from any computer

Other software:
- Python 3.11 or higher
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** installs Python and the other dependencies for you.
- **[Ollama](https://ollama.com/download)** to load the vision-language model
- **[Tropy](https://tropy.org)** and its [CSV import plugin](https://github.com/tropy/tropy-plugin-csv/) to browse the collection

### Choosing a model

Measured on 10 BYTE covers (1975-1997) with the same [prompt](prompts/cover_prompt.txt). The 8B local model was tested on a Macbook with an M5 chip and 24 GB of memory. The 32B model was tested on a workstation with an AMD Strix Halo chip and 128 GB of memory.

| Model | Runs on | Time per cover (median) | 350 covers | Cost |
|---|---|---|---|---|
| `qwen3-vl:8b` (local) | Macbook with Apple Silicon and 16 GB memory or more; PC with a recent GPU | about 6.4 min | about 1.6 days | none |
| `qwen3-vl:32b` (local) | 48 GB memory or more | about 7 min | about 2 days | none |
| `claude-opus-5-5` (hosted) | any computer with internet | about 15 s | about 1.5 h | about $0.06 per cover |

The models differed in the detail and accuracy of their descriptions, how often they broke the prompt's rules and how often they reported their own uncertainty. For more detail on the comparison, see [docs/method.md](docs/method.md#comparing-three-models). Every record is reviewed by a person for consistency.

## Install ia-magazines-to-tropy

```sh
git clone https://github.com/driscoll/ia-magazines-to-tropy.git
cd ia-magazines-to-tropy
uv sync                        # local models only
uv sync --extra anthropic      # local OR hosted model 
```

Retrieve a local model with Ollama:
```sh
ollama pull qwen3-vl:8b       # or qwen3-vl:32 on a machine with lots of memory
```

## Quick start: five BYTE covers

`--limit 5` keeps the download to the first five issues; without it, `fetch` downloads
the cover of every issue in the collection (350 images for the Byte magazine collection).

```sh
uv run ia2tropy fetch byte --limit 5
uv run ia2tropy prepare byte
uv run ia2tropy describe byte --model qwen3-vl:8b
uv run ia2tropy review byte          # open http://127.0.0.1:8000, review, then Ctrl-C
uv run ia2tropy export byte
```

Everything a run produces goes in `data/byte/`, which git ignores.

## Step by step

Note: every command runs with a collection name (e.g. "byte".) This refers to a configuration file in the [profiles](profiles/) folder. See below for details on creating a new configuration. 

### 1. Fetch the covers

```sh
uv run ia2tropy fetch byte
```

Downloads the first page image (normally the cover) of each item in the profile's Internet Archive collection as a JPEG 2000 file and stores it in `data/byte/originals/`. Only that one image is downloaded per item, not the whole scan. `data/byte/manifest.tsv` records which file came from which item. Items without a page-image archive are reported and skipped. Re-running the script will skip covers you already have, so you can start small and add more.

If you don't want to download the whole collection at once, use these flags to fetch a subset:

| Option | Fetches | Example |
|---|---|---|
| `--limit N` | the first N items, sorted by identifier | `--limit 5` |
| `--items ID ...` | exactly these items | `--items byte-magazine-1987-05 byte-magazine-1993-06` |
| `--query "..."` | items in the collection that match an [Internet Archive search](https://archive.org/advancedsearch.php) | `--query "year:1985"` or `--query "year:[1980 TO 1984]"` |

`--limit` also works with `--query`, e.g. `--query "year:1985" --limit 3`. An item's
identifier is the last part of its URL: `https://archive.org/details/<identifier>`.

The later steps work on whatever has been fetched, so a subset carries through the
whole pipeline. `describe` also has `--limit N` and `--images NAME ...` to describe only some of the fetched covers.

### 2. Prepare images for the model

```sh
uv run ia2tropy prepare byte
```

Creates PNG copies that are limited to 2048 pixels on the long edge. Shrinking the images reduces the number of image tokens used by the model. In testing, the models struggled to extract text from some smaller images, especially on dense covers with lots of small lettering. The standardized files are saved to `data/byte/model-input/`. The originals are left untouched until they are transferred into Tropy.

### 3. Describe the covers

Local model:
```sh
uv run ia2tropy describe byte                         # default, qwen3-vl:32b
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

Running the local model on a laptop may take many hours (overnight is a good plan!). To avoid losing work-in-progress, use `tmux` or `screen` (Linux or macOS) to keep your terminal session alive and change your power settings so that your laptop will not go to sleep.

### 4. Review and correct

When the model is done with its analysis, it is time for your manual review:
```sh
uv run ia2tropy review byte
```

The above command will start a temporary server. Open <http://127.0.0.1:8000> in your browser to get started. 

The home page shows a list of every item's name and its status. Click on the first item's name to bring up the review interface. Compare the metadata on the right to the image on the left. After making your corrections, click Save and move on to the next image.

The corrections are credited to your name. The app gets your name from the command-line argument (`--reviewer "Your Name"`), your local git configuration, or your local username.

### 5. Export to Tropy

When you're done with the corrections:
```sh
uv run ia2tropy export byte
```

This command creates a CSV file with all of the metadata and a folder with copies of the original images. Only images that have been reviewed in the previous steps are included (`--include-unreviewed` exports all). Keep these files together because the CSV refers to the images by relative paths.

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
| photo notes | two notes: the description of the artwork (the same text as `dc:description`, easier to read as a note), and the cover text, one piece per line |

**Optional: copy the Internet Archive metadata into the Tropy record.** Every item already includes a link to its Internet Archive page (`dc:source`) but you may wish to keep a copy of it in Tropy as well. Add the "--with-ia" flag to your export command: 

```sh
uv run ia2tropy export byte --with-ia
```

This fetches each item's metadata from the Archive (cached in
`data/byte/ia-metadata.jsonl`; `--refresh-ia` fetches it again) and adds seven fields:
`ia_title`, `ia_description`, `ia_language`, `ia_identifier`, `ia_collection`,
`ia_subject` and `ia_imagecount`. Tropy needs a small vocabulary that defines them,
which the export writes to `ia-vocabulary.ttl`. 

When you import data into Tropy with the Internet Archive metadata, you need to first add the vocabulary file. In **Preferences > Vocabularies**, click **+** and choose `ia-vocabulary.ttl` from the export folder. This is needed once per Tropy installation; later exports use the same vocabulary. If Tropy reports `UNIQUE constraint failed: vocabularies.prefix`, a vocabulary with the same prefix is already installed. Remove it and re-add from the new TTL file.

The vocabulary is this project's own, not the Internet Archive's; each field links to its definition in the Archive's [metadata schema](https://archive.org/developers/metadata-schema/index.html).

## Documentation and reproducibility 

Together with the manifest (`manifest.tsv`), these files document how every catalog record was made. All of them are in `data/<profile>/`.

| File | Item | Content |
|---|---|---|
| `prompts/` (two files per run) | Prompt and schema | the exact prompt and schema of each run, in files named by the first 12 characters of the hash |
| `descriptions.jsonl` (one record per cover, per run) | Model | the model's name and its digest |
| | Prompt | the prompt version and a SHA-256 hash of the exact prompt and schema |
| | Settings | the sampling settings |
| | Performance | token counts and timings |
| | Reasoning | the model's reasoning |
| | Failed attempts | any attempts that did not produce a record |
| | Environment | this repository's git commit and whether it had uncommitted changes; the tool, Ollama and Python versions; the computer's name and operating system; the start time |
| `corrections.jsonl` (one record per save) | Reviewer | the reviewer's name |
| | Time | when the save was made |
| | Status | approved or corrected |
| | Source | the model and prompt that produced the record under review |
| | Record | the full corrected record |

## Using Claude (Anthropic API) instead of the local model

1. Create an API key in the [Anthropic Console](https://console.anthropic.com/).
2. Save it in a plain text file **outside this folder**, readable only by you, such as `~/.config/anthropic-key` (on macOS/Linux with `chmod 600`) or `$env:USERPROFILE\.config\anthropic-key` on Windows.
3. When you run the description task in step 3 above, use this command:

   ```sh
   # macOS/Linux
   uv run ia2tropy describe byte --backend anthropic --api-key-file ~/.config/anthropic-key

   # Windows
   uv run ia2tropy describe byte --backend anthropic --api-key-file "$env:USERPROFILE\.config\anthropic-key" 
   ```

> **Keep credentials out of the repository.** Never put an API key in a file inside this
> folder, in a profile, or in a commit, and don't type it on the command line, where it
> is saved in your shell history. The tool refuses a key file located inside the project
> folder, and `.gitignore` excludes common key file names, but that is a safety net, not
> a guarantee. If a key is ever exposed, revoke it in the Anthropic Console and create a
> new one.

With the hosted model the covers are sent to Anthropic for processing, and the model
decides for itself how much to reason; when it does, the API returns a summary of its
reasoning rather than the full text. Use `--effort medium` to make it cheaper and faster. Each raw API response is also saved in `data/<profile>/anthropic-raw/`.

In our testing, each image cost under ten cents. You must load these credits in advance from a credit card in your Anthropic account.

## How to create a new collection

Ready to move beyond my BYTE example? 

Select a name for your collection (e.g., "byte", "compute", "critical-inquiry").

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

The [prompt](prompts/cover_prompt.txt) should apply to any magazine cover but it was tested with English-language computer magazines from the late 20th century. To adapt it to other periodicals:

- **Try the profile first.** The examples in `profiles/<name>.toml` steer the model strongly, and most adjustments for a new magazine belong there. If that is not enough, edit the prompt text itself.
- **Settle the profile and the prompt before the full run.** Records are matched to the exact prompt, which includes the profile's values, so after any edit to either one `describe` starts over on every cover.

You don't need to keep notes on your changes: each `describe` run saves the exact prompt and schema it used in `data/<name>/prompts/`.

## Cautions

- **The first page image is assumed to be the cover.** Check covers that look wrong;
  some scans start with a blank page or a back cover.
- **Every record needs review.** The models misread stylized text, sometimes include text
  from the artwork or skip a rule, and rarely report their own uncertainty.
- **Mailing labels** on library or subscriber copies can carry names and addresses. They
  are transcribed into the record (so you can see them) but never exported.
