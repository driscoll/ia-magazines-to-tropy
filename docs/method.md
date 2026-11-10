# Method

How the pipeline was designed and tested, and what the tests showed. The development
work was done on 350 BYTE magazine covers (1975-1998) in a separate working repository;
the benchmark scripts and raw results are not part of this one.

## Approach

- **Describe what is on the cover, nothing more.** The model transcribes the cover's
  printed text exactly, in its original language and script, and describes the artwork
  in neutral terms. It is told not to use outside knowledge of the magazine, not to
  identify people from their appearance, and not to infer anyone's race or ethnicity.
  It may infer gender, age and professional identity from what is visible.
- **A person reviews every record.** The model's output is a draft. Automatic
  consistency checks point the reviewer to likely problems, and corrections are stored
  separately so the original output is never lost.
- **Local first.** An open-weights model running locally keeps the images on your own
  machine, keeps the model's full reasoning with each record, and can be pinned to an
  exact version. A hosted model (Claude) is an option for machines that can't run one.
- **Record how each record was made.** Every record carries the model, prompt, settings
  and software versions; every correction carries the reviewer and date.
- **The final product is the corrected records.** The process is documented so that it
  can be understood and repeated on other collections, not so that a run can be
  reproduced exactly; model output varies from run to run.

## The record

For each cover the model fills a fixed JSON schema (`prompts/cover_schema.json`):
the cover's printed text in reading order; masthead, tagline and publisher; issue date
and volume/number, each as printed and parsed; prices, each as printed with amount,
currency and region; a special or theme issue label, if the cover itself has one; named
entities (proper names only), each marked as from the cover text or a visible logo;
topics; the artwork's medium and a 3-6 sentence description; a one-sentence summary;
non-original marks (mailing labels, stamps, handwriting, damage); and uncertainty notes,
each naming the field affected.

The cover artist is not part of the model's task. Models misread signatures and can't
know who made a cover; the reviewer enters the artist from the magazine's own credits.

## Model and settings

- **Model:** Qwen3-VL, a "thinking" vision-language model that reasons before
  answering, served by [Ollama](https://ollama.com). The 32-billion-parameter version
  (`qwen3-vl:32b`, Q4_K_M quantization) is the default; the 8-billion version runs on a
  16 GB Mac.
- **Reasoning stays on.** On Ollama 0.40, `think=False` has no effect on these model
  tags; separate `-instruct` tags turn reasoning off and run 2-3 times faster. Reasoning
  was kept for quality and for the record it leaves.
- **Images at 2048 px** on the long edge. Testing 2048, 1536 and 1024 px showed that
  smaller images save only the time spent reading the image (about 15 s at 2048 px,
  4 s at 1024 px), since most of the time goes to generating, and that 1024 px begins to
  lose small type on dense 1990s covers.
- **Sampling:** Qwen's recommended settings for thinking models (temperature 0.6,
  top_p 0.95, top_k 20) with seed 42. Greedy decoding (temperature 0) made the
  reasoning loop on hard covers.
- **Output:** the schema is passed as Ollama's `format`, so the output is always valid
  JSON with the expected fields. A copy without field descriptions is appended to the
  prompt so the model sees the field names, while every rule is stated once, in the
  prompt.
- **Limits:** a 32,768-token context and a 12,000-token cap on reasoning plus answer. A
  cover that hits the cap or returns invalid JSON is retried once with the next seed and
  a 20,000-token cap.

## Developing the prompt

Timings are per cover with the 32b model at 2048 px.

| Version | Change | Result |
|---|---|---|
| v1 | One-line request, three-field schema | About 2 min; free-form output |
| v2 | Detailed cataloging prompt and full schema | 11 min on the simplest cover; on two others the reasoning looped on ambiguous rules until it hit the token cap, producing no record |
| v3 | Resolved the ambiguities that caused loops (what counts as one piece of text, cut-off text, typographic characters, proper vs. generic names); told the model not to re-check its work; stated each rule once | All test covers finished; about 6 min average |
| v4 | Text that is part of the artwork moved out of the transcription; the named-entity type dropped | Valid records on all 10 test covers |
| v5 | Artist signature task removed | Valid records on 9 of 10; one ran out of tokens |
| v6 | Uncertainty notes required in specific situations (cut-off text, two readings, inferred values); closing reminders for the rules that failed most | Valid records on all 10; the first version whose local runs wrote uncertainty notes |
| v7 | No uncertainty notes just because a field is not on the cover | Used for the model comparison below |
| v8 | Describing people: gender, age and professional identity may be inferred from what is visible; race and ethnicity may not (v7 allowed none of these) | Current version |

What this taught:

- **Reasoning time goes to ambiguity.** The model spent most of its reasoning on rules
  that were unclear or seemed to conflict, coming back to the same question again and
  again. Each improvement was a clearer rule, not a shorter prompt. Reading the
  reasoning is the quickest way to see where a prompt is unclear.
- **Rules about the model's own judgment work badly.** "Note anything you hesitated
  over" produced almost no notes; listing the situations that always need a note worked
  better.
- **Run-to-run variation matters as much as the prompt.** With the same prompt, a cover
  could be clean on one run and break a rule on the next, and the time for a cover
  varied about twofold between runs. Retries and the review-stage checks handle this;
  further prompt changes could not remove it.
- **Some rules still fail sometimes.** All three models occasionally list text from the
  artwork as cover text, or transcribe a word cut off at the edge without the `[?]`
  marker. The checks flag both for the reviewer.

## Comparing three models

The same prompt (v7) on the same 10 BYTE covers, 1975-1997, one run per model. The local
models ran on one workstation (AMD Strix Halo, 128 GB unified memory, Ollama 0.40.15);
Claude Opus 5.5 ran through the Anthropic API with adaptive thinking at `effort: high`.

| | Qwen3-VL 8B (local) | Qwen3-VL 32B (local) | Claude Opus 5.5 (hosted) |
|---|---|---|---|
| Records produced | 10 of 10 | 10 of 10 | 10 of 10 |
| Retries needed | 0 | 0 | 0 |
| Time per cover, median (range) | 2.3 min (1.5-4.6) | 6.8 min (4.0-17.6) | 15 s (8-31) |
| Covers with consistency flags | 4 (6 flags) | 4 (5 flags) | 2 (2 flags) |
| Uncertainty notes written | 2 | 2 | 26 |
| Reasoning kept | full text, every cover | full text, every cover | a summary, on 6 of 10 covers |
| Cost for the 10 covers | none | none | $0.56 |
| Images leave the computer | no | no | yes |

What the records showed:

- **The 8B model made the clearest errors.** It recorded the artist's signature as a
  named entity despite the instruction to ignore signatures, misparsed a price, and on
  the Arabic-language edition repeated text and listed artwork text as cover text.
- **The 32B model's flags were mostly about artwork text**, including on a cover it had
  handled cleanly in earlier runs; this looks like run-to-run variation.
- **Claude followed the rules most closely and reported its own uncertainty**; the
  local models rarely do. It decides for itself whether to reason, and reasoned on only
  6 of the 10 covers.
- **All three missed the `[?]` marker for cut-off text on one cover each.**
- **Time and cost:** the 8B model was about three times faster than the 32B locally;
  Claude was about 25 times faster than the 32B, at about $0.06 per cover.

With one run per model and 10 covers, these differences are indicative, not measured
precisely. Every model's output still needs review; the local models need more of it,
especially for the rules about what to leave out.

## Review

The review page (`ia2tropy review`) shows the cover beside a form with every field of
the record. The cover stays in view while the form scrolls. The checks shown with each
record are:

- a named entity marked as cover text that doesn't appear in the transcription;
- a theme-issue label that doesn't appear in the transcription;
- a year, month or price amount that disagrees with the printed text;
- repeated cover text, or cover text also quoted as part of the artwork;
- `[?]` in the transcription with no uncertainty note, or a reported tear or crop
  with no `[?]`;
- duplicate topics, names or prices.

A save with no edits is recorded as "approved" and one with edits as "corrected"; the
model's output is never changed.

## Export and the Internet Archive's metadata

The Tropy export uses the reviewed records for all catalog fields. The Internet Archive
is the source of the images and the item link. Its own item metadata (title,
description, language, identifier, collection, subject, page count) is left out by
default, since every item links to its Internet Archive page. Exporting with `--with-ia`
adds it in separate fields prefixed `ia_`, never mixed with the catalog fields: much of
it was entered by the people who scanned the magazines and it is useful for comparison.
It is inconsistent across items: titles follow different patterns, some items have no
date, and some subject lists look machine-generated from OCR.
