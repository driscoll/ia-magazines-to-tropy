"""Step 4: review and correct the records in a local web page.

    ia2tropy review byte                 # then open http://127.0.0.1:8000
    ia2tropy review byte --port 8001 --reviewer "Your Name"

Shows each cover next to a form with its record. Standard library only; plain HTML forms,
no JavaScript. Listens on 127.0.0.1 only. To use it from another computer, use an SSH
tunnel (see README), not a public address: the page has no login.

Model output (data/<profile>/descriptions.jsonl) is never modified. Each save appends a
row to data/<profile>/corrections.jsonl; the latest row for a cover wins. A save with no
edits is recorded as "approved", one with edits as "corrected". Each row also carries the
fields only a person enters (cover_artist, from the magazine's credits) and the
reviewer's name.
"""
import argparse, html, json, subprocess, threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .checks import problems as v2_problems
from .config import PROMPTS_DIR, load_profile

SCHEMA = json.loads((PROMPTS_DIR / "cover_schema.json").read_text(encoding="utf-8"))
_P = SCHEMA["properties"]
MEDIUMS = _P["artwork"]["properties"]["medium"]["enum"]
EVIDENCE = _P["named_entities"]["items"]["properties"]["evidence"]["enum"]
STATUSES = ["unreviewed", "approved", "corrected"]

CSS = """
body { font: 15px/1.4 system-ui, sans-serif; margin: 0 auto; padding: 16px; max-width: 1400px;
       color: #222; background: #fff; }
a { color: #0645ad; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #ddd; vertical-align: top; }
.cover { display: grid; grid-template-columns: minmax(300px, 40%) 1fr; gap: 24px; align-items: start; }
/* the image column stays in view while the form scrolls */
.cover > a { position: sticky; top: 16px; display: block; }
.cover img { display: block; width: 100%; max-height: calc(100vh - 32px); object-fit: contain;
       object-position: top; border: 1px solid #ccc; }
fieldset { border: 1px solid #ccc; margin: 0 0 12px; padding: 8px 12px; }
legend { font-weight: bold; }
label { display: block; margin: 6px 0 2px; font-size: 13px; color: #555; }
input[type=text], input[type=number], textarea, select { width: 100%; box-sizing: border-box;
       font: 14px/1.35 ui-monospace, monospace; padding: 4px; }
textarea { min-height: 4em; resize: vertical; }
.row { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 8px; }
.hint { font-size: 12px; color: #777; }
.problems { background: #fff4e5; border: 1px solid #f0b060; padding: 8px 12px; margin-bottom: 12px; }
.error { background: #fde8e8; border: 1px solid #e07070; padding: 8px 12px; margin-bottom: 12px; }
.saved { background: #e8f6e8; border: 1px solid #70b070; padding: 8px 12px; margin-bottom: 12px; }
.status-approved { color: #2a7a2a; } .status-corrected { color: #1a5aa8; } .status-unreviewed { color: #999; }
.buttons { position: sticky; bottom: 0; background: #fff; padding: 8px 0; border-top: 1px solid #ddd; }
button { font-size: 15px; padding: 6px 14px; margin-right: 8px; }
details pre { white-space: pre-wrap; font-size: 13px; background: #f6f6f6; padding: 8px; }
nav { margin-bottom: 12px; }
@media (max-width: 800px) { .cover { grid-template-columns: 1fr; } .cover > a { position: static; }
       .cover img { max-height: none; } }
"""


# ---------- data ----------

class Store:
    def __init__(self, records_path, corrections_path, reviewer=None):
        self.reviewer = reviewer
        self.records_path = Path(records_path)
        self.corrections_path = Path(corrections_path)
        self.lock = threading.Lock()

    def records(self):
        rows = {}
        for line in self.records_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if isinstance(r.get("output"), dict):
                    rows[r["image"]] = r
        return dict(sorted(rows.items()))

    def corrections(self):
        latest = {}
        if self.corrections_path.exists():
            for line in self.corrections_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    c = json.loads(line)
                    latest[c["image"]] = c
        return latest

    def save(self, image, record, status, source, cover_artist=None):
        row = {"image": image, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "reviewer": self.reviewer, "status": status,
               "source_prompt": source.get("prompt"), "source_prompt_sha256": source.get("prompt_sha256"),
               "source_model": source.get("model"), "source_model_digest": source.get("model_digest"),
               "cover_artist": cover_artist, "record": record}
        with self.lock:
            self.corrections_path.parent.mkdir(parents=True, exist_ok=True)
            with self.corrections_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------- form <-> record ----------
# Lists are one item per line. Lists of objects are one item per line with " | " between parts.

def lines(v):
    return "\n".join(v or [])


def opt(v):
    return "" if v is None else str(v)


def to_form(o):
    d, v, a, m = o["issue_date"], o["volume_number"], o["artwork"], o["non_original_marks"]
    return {
        "cover_text_reading_order": lines(o["cover_text_reading_order"]),
        "masthead_title": opt(o["masthead_title"]), "tagline": opt(o["tagline"]),
        "date_as_printed": opt(d["as_printed"]), "date_year": opt(d["year"]), "date_month": opt(d["month"]),
        "vol_as_printed": opt(v["as_printed"]), "vol_volume": opt(v["volume"]), "vol_number": opt(v["number"]),
        "prices": lines(" | ".join([p["as_printed"], opt(p["amount"]), opt(p["currency"]), opt(p["region"])])
                        for p in o["prices"]),
        "publisher_as_printed": opt(o["publisher_as_printed"]),
        "special_or_theme_issue": opt(o["special_or_theme_issue"]),
        "named_entities": lines(" | ".join([e["name_as_printed"], e["evidence"]])
                                for e in o["named_entities"]),
        "topics": lines(o["topics"]),
        "medium": a["medium"],
        "art_description": a["description"],
        "short_description": o["short_description"],
        "mailing_label_text": opt(m["mailing_label_text"]),
        "stamps_or_handwriting_text": opt(m["stamps_or_handwriting_text"]),
        "condition_or_scan_issues": lines(m["condition_or_scan_issues"]),
        "uncertainty_notes": lines(f"{u['field']} | {u['note']}" for u in o["uncertainty_notes"]),
    }


def from_form(f):
    """Parse submitted form fields into a v2 record. Returns (record, errors)."""
    errors = []
    g = lambda k: f.get(k, "").replace("\r\n", "\n")
    text = lambda k: g(k).strip() or None
    items = lambda k: [s.strip() for s in g(k).split("\n") if s.strip()]

    def num(k, kind, label):
        s = g(k).strip()
        if not s:
            return None
        try:
            return kind(s)
        except ValueError:
            errors.append(f"{label}: '{s}' is not a number")
            return None

    month = num("date_month", int, "Month")
    if month is not None and not 1 <= month <= 12:
        errors.append("Month must be 1-12")

    prices = []
    for i, ln in enumerate(items("prices"), 1):
        parts = [p.strip() for p in ln.rsplit("|", 3)]
        if len(parts) != 4:
            errors.append(f"Prices line {i}: need 'as printed | amount | currency | region'")
            continue
        amt = None
        if parts[1]:
            try:
                amt = float(parts[1])
            except ValueError:
                errors.append(f"Prices line {i}: amount '{parts[1]}' is not a number")
        prices.append({"as_printed": parts[0], "amount": amt,
                       "currency": parts[2] or None, "region": parts[3] or None})

    entities = []
    for i, ln in enumerate(items("named_entities"), 1):
        parts = [p.strip() for p in ln.rsplit("|", 1)]
        if len(parts) != 2:
            errors.append(f"Named entities line {i}: need 'name | evidence'")
            continue
        if parts[1] not in EVIDENCE:
            errors.append(f"Named entities line {i}: evidence must be one of {', '.join(EVIDENCE)}")
        entities.append({"name_as_printed": parts[0], "evidence": parts[1]})

    notes = []
    for i, ln in enumerate(items("uncertainty_notes"), 1):
        parts = [p.strip() for p in ln.split("|", 1)]
        if len(parts) != 2:
            errors.append(f"Uncertainty notes line {i}: need 'field | note'")
            continue
        notes.append({"field": parts[0], "note": parts[1]})

    medium = g("medium")
    if medium not in MEDIUMS:
        errors.append("Medium is required")

    record = {
        "cover_text_reading_order": items("cover_text_reading_order"),
        "masthead_title": text("masthead_title"),
        "tagline": text("tagline"),
        "issue_date": {"as_printed": text("date_as_printed"),
                       "year": num("date_year", int, "Year"), "month": month},
        "volume_number": {"as_printed": text("vol_as_printed"),
                          "volume": num("vol_volume", int, "Volume"),
                          "number": num("vol_number", int, "Number")},
        "prices": prices,
        "publisher_as_printed": text("publisher_as_printed"),
        "special_or_theme_issue": text("special_or_theme_issue"),
        "named_entities": entities,
        "topics": items("topics"),
        "artwork": {"medium": medium,
                    "description": g("art_description").strip()},
        "short_description": g("short_description").strip(),
        "non_original_marks": {"mailing_label_text": text("mailing_label_text"),
                               "stamps_or_handwriting_text": text("stamps_or_handwriting_text"),
                               "condition_or_scan_issues": items("condition_or_scan_issues")},
        "uncertainty_notes": notes,
    }
    return record, errors


# ---------- HTML ----------

e = lambda s: html.escape(str(s), quote=True)


def page(title, body):
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width, initial-scale=1'>"
            f"<title>{e(title)}</title><link rel=stylesheet href=/style.css></head>"
            f"<body>{body}</body></html>")


def index_page(store, status_filter):
    records, corr = store.records(), store.corrections()
    counts = {s: 0 for s in STATUSES}
    rows = []
    for img, r in records.items():
        status = corr[img]["status"] if img in corr else "unreviewed"
        counts[status] += 1
        if status_filter and status != status_filter:
            continue
        o = corr[img]["record"] if img in corr else r["output"]
        probs = len(v2_problems(o)) + len(o.get("uncertainty_notes", []))
        rows.append(f"<tr><td><a href='/cover/{quote(img)}'>{e(img)}</a></td>"
                    f"<td class=status-{status}>{status}</td><td>{probs or ''}</td>"
                    f"<td>{e(o.get('short_description', ''))}</td></tr>")
    links = " · ".join([f"<a href='/'>all ({len(records)})</a>"] +
                       [f"<a href='/?status={s}'>{s} ({counts[s]})</a>" for s in STATUSES])
    return page("Cover review", f"""
<h1>Cover review</h1>
<nav>{links}</nav>
<table><tr><th>Cover</th><th>Status</th><th>Flags</th><th>Short description</th></tr>
{''.join(rows)}</table>
<p class=hint>Flags = automatic consistency problems + the model's own uncertainty notes.</p>""")


def field(name, label, value, kind="text", hint=""):
    h = f"<div class=hint>{e(hint)}</div>" if hint else ""
    if kind == "area":
        rows = max(3, min(20, value.count("\n") + 2))
        return f"<label for={name}>{e(label)}</label><textarea id={name} name={name} rows={rows}>{e(value)}</textarea>{h}"
    return f"<label for={name}>{e(label)}</label><input type={kind} id={name} name={name} value='{e(value)}'>{h}"


def cover_page(store, image, form=None, errors=None, saved=False):
    records, corr = store.records(), store.corrections()
    r = records[image]
    c = corr.get(image)
    record = c["record"] if c else r["output"]
    f = form if form is not None else to_form(record)
    names = list(records)
    i = names.index(image)
    prev_link = f"<a href='/cover/{quote(names[i - 1])}'>&larr; previous</a>" if i > 0 else ""
    next_link = f"<a href='/cover/{quote(names[i + 1])}'>next &rarr;</a>" if i + 1 < len(names) else ""
    status = c["status"] if c else "unreviewed"
    when = f" ({e(c['time'])})" if c else ""

    msgs = ""
    if saved:
        msgs += "<div class=saved>Saved.</div>"
    if errors:
        msgs += "<div class=error><b>Not saved:</b><ul>" + "".join(f"<li>{e(x)}</li>" for x in errors) + "</ul></div>"
    probs = v2_problems(record)
    if probs:
        msgs += "<div class=problems><b>Consistency checks:</b><ul>" + "".join(f"<li>{e(p)}</li>" for p in probs) + "</ul></div>"

    artist = form.get("cover_artist", "") if form is not None else opt((c or {}).get("cover_artist"))
    medium_opts = "".join(f"<option{' selected' if m == f['medium'] else ''}>{e(m)}</option>" for m in MEDIUMS)
    thinking = r.get("thinking") or ""
    reasoning = (f"<details><summary>Model reasoning ({len(thinking):,} characters)</summary>"
                 f"<pre>{e(thinking)}</pre></details>") if thinking else ""

    return page(image, f"""
<nav><a href='/'>all covers</a> · {prev_link} {'·' if prev_link and next_link else ''} {next_link}</nav>
<h1>{e(image)}</h1>
<p>Status: <span class=status-{status}>{status}</span>{when} · model: {e(r.get('model', '?'))}</p>
<div class=cover>
<a href='/image/{quote(image)}' target=_blank><img src='/image/{quote(image)}' alt='Cover of {e(image)}'></a>
<form method=post action='/cover/{quote(image)}'>
{msgs}
<fieldset><legend>Entered by you</legend>
{field("cover_artist", "Cover artist (from the magazine's credits)", artist, hint="Not filled in by the model.")}
</fieldset>
<fieldset><legend>Summary</legend>
{field("short_description", "Short description", f["short_description"], "area")}
</fieldset>
<fieldset><legend>Cover text</legend>
{field("cover_text_reading_order", "Cover text in reading order", f["cover_text_reading_order"], "area", "One piece of text per line.")}
{field("masthead_title", "Masthead title", f["masthead_title"])}
{field("tagline", "Tagline", f["tagline"])}
{field("publisher_as_printed", "Publisher as printed", f["publisher_as_printed"])}
{field("special_or_theme_issue", "Special or theme issue label", f["special_or_theme_issue"])}
</fieldset>
<fieldset><legend>Date, volume, price</legend>
<div class=row><div>{field("date_as_printed", "Date as printed", f["date_as_printed"])}</div>
<div>{field("date_year", "Year", f["date_year"], "number")}</div>
<div>{field("date_month", "Month (1-12)", f["date_month"], "number")}</div></div>
<div class=row><div>{field("vol_as_printed", "Volume/number as printed", f["vol_as_printed"])}</div>
<div>{field("vol_volume", "Volume", f["vol_volume"], "number")}</div>
<div>{field("vol_number", "Number", f["vol_number"], "number")}</div></div>
{field("prices", "Prices", f["prices"], "area", "One per line: as printed | amount | currency | region")}
</fieldset>
<fieldset><legend>Names and topics</legend>
{field("named_entities", "Named entities", f["named_entities"], "area",
       "One per line: name | evidence. Evidence: " + ", ".join(EVIDENCE) + ".")}
{field("topics", "Topics", f["topics"], "area", "One per line.")}
</fieldset>
<fieldset><legend>Artwork</legend>
<label for=medium>Medium</label><select id=medium name=medium><option value=''></option>{medium_opts}</select>
{field("art_description", "Description", f["art_description"], "area")}
</fieldset>
<fieldset><legend>Non-original marks</legend>
{field("mailing_label_text", "Mailing label text", f["mailing_label_text"], "area")}
{field("stamps_or_handwriting_text", "Stamps or handwriting", f["stamps_or_handwriting_text"], "area")}
{field("condition_or_scan_issues", "Condition or scan issues", f["condition_or_scan_issues"], "area", "One per line.")}
</fieldset>
<fieldset><legend>Uncertainty</legend>
{field("uncertainty_notes", "Uncertainty notes", f["uncertainty_notes"], "area", "One per line: field | note")}
</fieldset>
{reasoning}
<div class=buttons>
<button name=action value=save>Save</button>
<button name=action value=save_next>Save and next</button>
<span class=hint>Saved unchanged = approved; saved with edits = corrected.</span>
</div>
</form>
</div>""")


# ---------- server ----------

class Handler(BaseHTTPRequestHandler):
    store = None
    image_dir = None

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def image_name(self, prefix):
        name = unquote(self.path_only[len(prefix):])
        return name if name in self.store.records() else None

    def do_GET(self):
        u = urlparse(self.path)
        self.path_only = u.path
        q = parse_qs(u.query)
        if u.path == "/":
            status = q.get("status", [""])[0]
            return self.send(200, index_page(self.store, status if status in STATUSES else ""))
        if u.path == "/style.css":
            return self.send(200, CSS, "text/css; charset=utf-8")
        if u.path.startswith("/image/"):
            name = self.image_name("/image/")
            path = self.image_dir / f"{name}.png" if name else None
            if not path or not path.exists():
                return self.send(404, "No such image")
            return self.send(200, path.read_bytes(), "image/png")
        if u.path.startswith("/cover/"):
            name = self.image_name("/cover/")
            if not name:
                return self.send(404, "No such cover")
            return self.send(200, cover_page(self.store, name, saved="saved" in q))
        self.send(404, "Not found")

    def do_POST(self):
        u = urlparse(self.path)
        self.path_only = u.path
        # refuse form posts from other web pages
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.send(403, "Forbidden")
        name = self.image_name("/cover/") if u.path.startswith("/cover/") else None
        if not name:
            return self.send(404, "No such cover")
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode(), keep_blank_values=True).items()}
        record, errors = from_form(form)
        if errors:
            return self.send(400, cover_page(self.store, name, form=form, errors=errors))
        original = self.store.records()[name]
        # compare against the original after the same form round trip, so whitespace or
        # ""-vs-null differences the form normalizes away don't count as corrections
        baseline, _ = from_form(to_form(original["output"]))
        status = "approved" if record == baseline else "corrected"
        self.store.save(name, record, status, original, form.get("cover_artist", "").strip() or None)
        names = list(self.store.records())
        i = names.index(name)
        if form.get("action") == "save_next":
            # after the last cover, go back to the list of all covers
            return self.redirect(f"/cover/{quote(names[i + 1])}" if i + 1 < len(names) else "/")
        self.redirect(f"/cover/{quote(name)}?saved=1")

    def log_message(self, fmt, *args):
        pass


def main(argv=None):
    p = argparse.ArgumentParser(prog="ia2tropy review", description=__doc__.split("\n")[0])
    p.add_argument("profile")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reviewer", help="name recorded on each save (default: git user.name)")
    p.add_argument("--records", help="model output to review (default: data/<profile>/descriptions.jsonl)")
    p.add_argument("--corrections", help="where saves go (default: data/<profile>/corrections.jsonl)")
    a = p.parse_args(argv)
    profile = load_profile(a.profile)
    records = Path(a.records) if a.records else profile.descriptions
    if not records.exists():
        raise SystemExit(f"No records in {records}; run 'ia2tropy describe {a.profile}' first")
    try:
        git_name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
    except OSError:
        git_name = ""
    reviewer = a.reviewer or git_name or None
    Handler.store = Store(records, a.corrections or profile.corrections, reviewer)
    Handler.image_dir = profile.model_input
    print(f"Reviewing {len(Handler.store.records())} covers at http://127.0.0.1:{a.port} as {reviewer}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()

