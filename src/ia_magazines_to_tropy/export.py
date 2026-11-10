"""Step 5: export the reviewed records as a CSV for Tropy's CSV import plugin.

    ia2tropy export byte
    ia2tropy export byte --include-unreviewed      # also covers nobody has reviewed yet
    ia2tropy export byte --with-ia                 # add the Internet Archive's own metadata

Writes data/<profile>/tropy-export/<profile>.csv and copies the original JP2 of each
exported cover into tropy-export/images/, so the folder can be moved to the computer
that runs Tropy. Photo paths in the CSV are relative to the CSV file.

The catalog fields come from our own records of the cover images, as reviewed. The item
link (dc:source) is built from the identifier in the manifest, so the export needs no
network access.

With --with-ia, selected Internet Archive metadata (title, description, language,
identifier, collection, subject, imagecount) is added in separate fields prefixed ia_,
for comparison. Those fields use a small custom vocabulary, written to
tropy-export/ia-vocabulary.ttl, which is imported into Tropy once. IA metadata is cached
in data/<profile>/ia-metadata.jsonl with the date it was fetched (--refresh-ia fetches
it again).

The model's reasoning and any mailing-label text are never exported.
"""
import argparse, csv, html, json, re, shutil
from datetime import date
from pathlib import Path

import internetarchive

from .config import latest_by_image, load_profile

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
DC, DCT, TROPY = "http://purl.org/dc/elements/1.1/", "http://purl.org/dc/terms/", "https://tropy.org/v1/tropy#"
COLUMNS = [  # (header URI, label for the template)
    (DC + "title", "Title"),
    (DC + "date", "Date"),
    (DC + "creator", "Creator (cover artist)"),
    (DC + "publisher", "Publisher"),
    (DCT + "abstract", "Abstract (short description)"),
    (DC + "description", "Description (artwork)"),
    (DCT + "medium", "Medium"),
    (DC + "subject", "Subject (topics and names)"),
    (DC + "identifier", "Identifier (Internet Archive item)"),
    (DC + "source", "Source"),
    (DCT + "provenance", "Provenance"),
]
PHOTO_COLUMNS = [(TROPY + "path", "photo path"), (TROPY + "note", "photo note (cover text)")]
# (our field name, IA metadata key, label). IA's page count is "imagecount", and few items have it.
IA_FIELDS = [
    ("ia_title", "title", "IA title"),
    ("ia_description", "description", "IA description"),
    ("ia_language", "language", "IA language"),
    ("ia_identifier", "identifier", "IA identifier"),
    ("ia_collection", "collection", "IA collection"),
    ("ia_subject", "subject", "IA subject"),
    ("ia_imagecount", "imagecount", "IA image count (pages)"),
]
# A name for the ia_ fields, not an address Tropy fetches. Tropy stores it with every item,
# so changing it later means removing and re-importing the vocabulary.
DEFAULT_IA_NS = "https://kevindriscoll.info/ns/ia-magazines-to-tropy#"
# Internet Archive documents its metadata fields here, one anchor per field. It publishes no
# vocabulary of its own, so ours points to these definitions.
IA_SCHEMA = "https://archive.org/developers/metadata-schema/index.html"
# Tropy registers each vocabulary under its prefix and allows only one per prefix,
# so use a distinctive one rather than a short "ia" that another vocabulary could claim.
VOCAB_PREFIX = "iamagtropy"


def ia_metadata(identifiers, cache_path, refresh=False):
    """IA metadata per item, under ia_ names, cached with the date it was fetched."""
    cache = {}
    if cache_path.exists() and not refresh:
        cache = {r["identifier"]: r for r in map(json.loads, cache_path.read_text(encoding="utf-8").splitlines()) if r}
    new = [i for i in identifiers if i not in cache]
    for ident in new:
        m = internetarchive.get_item(ident).metadata
        cache[ident] = {"identifier": ident, "ia_fetched": date.today().isoformat(),
                        **{ours: m.get(key) for ours, key, _ in IA_FIELDS}}
    if new or refresh:
        cache_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in cache.values()),
                              encoding="utf-8")
    return cache


def ia_cell(value):
    """IA values for a CSV cell: lists joined with "; ", HTML markup turned into plain text."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(ia_cell(v) for v in value)
    text = re.sub(r"<br\s*/?>|</?(?:div|p)\b[^>]*>", "\n", str(value), flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def vocabulary(ns):
    """Turtle vocabulary defining the ia_ properties, for import into Tropy."""
    lines = ["@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
             "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
             "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
             "@prefix dcterms: <http://purl.org/dc/terms/> .",
             f"@prefix {VOCAB_PREFIX}: <{ns}> .", "",
             f"<{ns}> a owl:Ontology ;",
             '    dcterms:title "Internet Archive metadata (ia-magazines-to-tropy)"@en ;',
             '    dcterms:description "Metadata copied from Internet Archive items, kept separate from our own '
             'catalog fields for comparison. An unofficial vocabulary of the ia-magazines-to-tropy project, '
             'not published by the Internet Archive, whose metadata schema defines the fields."@en ;',
             f"    rdfs:seeAlso <{IA_SCHEMA}> .", ""]
    for ours, key, label in IA_FIELDS:
        lines += [f"{VOCAB_PREFIX}:{ours} a rdf:Property ;",
                  f'    rdfs:label "{label}"@en ;',
                  f'    rdfs:comment "The Internet Archive item\'s {key} metadata field, copied as found."@en ;',
                  f"    rdfs:seeAlso <{IA_SCHEMA}#{key}> ;",
                  f"    rdfs:isDefinedBy <{ns}> .", ""]
    return "\n".join(lines)


def provenance(src, corr):
    model = src.get("model", "unknown model")
    digest = f" ({src['model_digest'][:12]})" if src.get("model_digest") else ""
    prompt = src.get("prompt", "?")
    made = f"Description generated by {model}{digest} with prompt {prompt}"
    if src.get("prompt_sha256"):
        made += f" ({src['prompt_sha256'][:12]})"
    if not corr:
        return made + "; not reviewed."
    how = "reviewed and corrected" if corr["status"] == "corrected" else "reviewed without changes"
    who = corr.get("reviewer") or "unknown reviewer"
    return f"{made}; {how} by {who}, {corr['time'][:10]}."


def tidy(text):
    """Punctuation cleanup for the constructed catalog title (the record keeps the exact
    printed text): drop periods and commas that end a word, before a space or at the end.
    Initials such as "A.B.C." keep theirs."""
    return re.sub(r"(?<=\w\w)[.,;:]+(?=\s|$)", "", text or "").strip()


def row_for(image, src, corr, identifier, img_rel, ia, fallback_title):
    """One CSV row. ia is the item's IA metadata, or None when the ia_ fields are left out."""
    o = corr["record"] if corr else src["output"]
    d = o.get("issue_date") or {}
    year, month = d.get("year"), d.get("month")
    if year and month:
        when, label = f"{year:04d}-{month:02d}", f"{MONTHS[month - 1]} {year}"
    elif year:
        when, label = f"{year:04d}", d.get("as_printed") or str(year)
    else:
        when, label = "", d.get("as_printed") or ""
    title = ", ".join(x for x in (tidy(o.get("masthead_title") or fallback_title), tidy(label)) if x)
    subjects = list(dict.fromkeys(o.get("topics", []) + [e["name_as_printed"] for e in o.get("named_entities", [])]))
    art = o.get("artwork") or {}
    values = [
        title,
        when,
        (corr or {}).get("cover_artist") or "",
        o.get("publisher_as_printed") or "",
        o.get("short_description") or "",
        art.get("description") or "",
        art.get("medium") or "",
        "; ".join(subjects),
        identifier,
        f"https://www.archive.org/details/{identifier}",
        provenance(src, corr),
        *([] if ia is None else [ia_cell(ia.get(ours)) for ours, _, _ in IA_FIELDS]),
        img_rel,
        "\n".join(o.get("cover_text_reading_order", [])),
    ]
    return values


def main(argv=None):
    p = argparse.ArgumentParser(prog="ia2tropy export", description=__doc__.split("\n")[0])
    p.add_argument("profile")
    p.add_argument("--include-unreviewed", action="store_true")
    p.add_argument("--no-copy", action="store_true", help="don't copy images (CSV paths still point to images/)")
    p.add_argument("--with-ia", action="store_true",
                   help="add the Internet Archive's own item metadata in separate ia_ fields "
                        "(contacts IA; needs ia-vocabulary.ttl imported into Tropy)")
    p.add_argument("--refresh-ia", action="store_true",
                   help="with --with-ia: fetch IA metadata again instead of using the cache")
    p.add_argument("--ia-namespace", default=DEFAULT_IA_NS,
                   help="with --with-ia: namespace URI for the ia_ properties")
    a = p.parse_args(argv)
    if (a.refresh_ia or a.ia_namespace != DEFAULT_IA_NS) and not a.with_ia:
        p.error("--refresh-ia and --ia-namespace only apply with --with-ia")
    profile = load_profile(a.profile)

    records, corrections = latest_by_image(profile.descriptions), latest_by_image(profile.corrections)
    records = {k: v for k, v in records.items() if v.get("valid_json")}
    images = [i for i in sorted(records) if a.include_unreviewed or i in corrections]
    if not images:
        raise SystemExit("Nothing to export: no reviewed covers (use --include-unreviewed to export anyway)")
    with open(profile.manifest, newline="", encoding="utf-8") as fh:
        manifest = {r["local_file"].rsplit(".", 1)[0]: r for r in csv.DictReader(fh, delimiter="\t")}
    ids = [manifest[i]["identifier"] for i in images]
    ia = ia_metadata(ids, profile.ia_cache, a.refresh_ia) if a.with_ia else None

    out = profile.tropy_export
    (out / "images").mkdir(parents=True, exist_ok=True)
    rows = []
    for image in images:
        m = manifest[image]
        rel = f"images/{m['local_file']}"
        if not a.no_copy:
            shutil.copy2(profile.originals / m["local_file"], out / rel)
        rows.append(row_for(image, records[image], corrections.get(image), m["identifier"], rel,
                            ia and ia.get(m["identifier"], {}), profile.title))

    csv_path = out / f"{profile.name}.csv"
    ia_columns = [(a.ia_namespace + ours, label) for ours, _, label in IA_FIELDS] if a.with_ia else []
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow([uri for uri, _ in COLUMNS + ia_columns + PHOTO_COLUMNS])
        w.writerows(rows)
    print(f"Wrote {len(rows)} covers to {csv_path}" + ("" if a.no_copy else f", with images in {out / 'images'}"))
    vocab_path = out / "ia-vocabulary.ttl"
    if a.with_ia:
        vocab_path.write_text(vocabulary(a.ia_namespace), encoding="utf-8")
        print(f"Wrote the ia_ vocabulary to {vocab_path}")
    else:
        vocab_path.unlink(missing_ok=True)  # left by an earlier --with-ia export; this CSV has no ia_ fields
    print("Fields for the Tropy item template (property URI, label):")
    for uri, label in COLUMNS + ia_columns:
        print(f"  {uri}  {label}")
