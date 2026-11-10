"""Consistency checks on a cover record. Each problem is something for a reviewer to look
at, not necessarily an error."""
import json
import re

MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}


def norm(s):
    return " ".join(str(s).split()).lower()


def problems(o):
    """Return a list of problem descriptions for one record."""
    probs = []
    pieces = o.get("cover_text_reading_order", [])
    text = norm(" ".join(pieces))

    # names and labels that should appear in the transcribed text
    for e in o.get("named_entities", []):
        if e.get("evidence") == "cover_text" and norm(e["name_as_printed"]) not in text:
            probs.append(f"entity not in cover text: {e['name_as_printed']}")
    if o.get("special_or_theme_issue") and norm(o["special_or_theme_issue"]) not in text:
        probs.append("special_or_theme_issue not in cover text")

    # parsed values that should agree with the printed text
    d = o.get("issue_date") or {}
    printed = norm(d.get("as_printed") or "")
    if d.get("year") and str(d["year"]) not in printed:
        probs.append("issue_date.year not in as_printed")
    named = [MONTHS[w] for w in re.findall(r"[a-z]+", printed) if w in MONTHS]
    if d.get("month") and named != [d["month"]]:
        probs.append("issue_date.month disagrees with as_printed")
    for pr in o.get("prices", []):
        nums = re.findall(r"\d+(?:\.\d+)?", pr.get("as_printed", ""))
        if pr.get("amount") is not None and (not nums or float(nums[0]) != pr["amount"]):
            probs.append(f"price amount disagrees with as_printed: {pr.get('as_printed')}")

    # text that is part of the artwork belongs in artwork.description
    repeated = sorted({p for p in pieces if pieces.count(p) > 1})
    if repeated:
        probs.append("repeated cover text, possibly artwork text: " + "; ".join(repeated))
    quoted = " | ".join(norm(q) for q in re.findall(r"['\"“‘]([^'\"”’]{4,})['\"”’]",
                                                    (o.get("artwork") or {}).get("description", "")))
    in_art = [p for p in dict.fromkeys(pieces) if len(p) >= 6 and norm(p) in quoted]
    if in_art:
        probs.append("cover text also quoted in artwork description: " + "; ".join(in_art))

    # cut-off or partly legible text should be marked and noted
    marked = any("[?]" in p for p in pieces)
    if marked and not o.get("uncertainty_notes"):
        probs.append("[?] in cover text but no uncertainty note")
    issues = " ".join((o.get("non_original_marks") or {}).get("condition_or_scan_issues", [])).lower()
    if re.search(r"\b(torn|tear|cut off|cropped|crop|missing|trimmed)", issues) and not marked:
        probs.append(f"scan issue reported but no [?] in cover text: {issues}")

    for k in ("topics", "named_entities", "prices"):
        items = [json.dumps(x, sort_keys=True) for x in o.get(k, [])]
        if len(items) != len(set(items)):
            probs.append(f"duplicate items in {k}")
    return probs
