"""Collection profiles, file locations, and the prompt."""
import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = ROOT / "profiles"
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = ROOT / "data"

# Bump when prompts/cover_prompt.txt or cover_schema.json changes. Every record also
# stores a SHA-256 hash of the exact prompt and schema it was made with.
PROMPT_VERSION = "v7"


@dataclass
class Profile:
    name: str
    collection: str
    title: str
    description: str
    examples: dict
    prices: dict
    path: Path

    @property
    def data(self):
        return DATA_DIR / self.name

    @property
    def originals(self):
        return self.data / "originals"          # first-page JP2s from the Internet Archive

    @property
    def model_input(self):
        return self.data / "model-input"        # PNGs, 2048 px on the long edge

    @property
    def manifest(self):
        return self.data / "manifest.tsv"

    @property
    def descriptions(self):
        return self.data / "descriptions.jsonl"  # model output, never edited

    @property
    def corrections(self):
        return self.data / "corrections.jsonl"   # review saves, append-only

    @property
    def ia_cache(self):
        return self.data / "ia-metadata.jsonl"

    @property
    def tropy_export(self):
        return self.data / "tropy-export"


def load_profile(name_or_path):
    """Load profiles/<name>.toml, or a path to a .toml file."""
    p = Path(name_or_path)
    if not p.suffix:
        p = PROFILES_DIR / f"{name_or_path}.toml"
    if not p.exists():
        available = ", ".join(sorted(x.stem for x in PROFILES_DIR.glob("*.toml")))
        raise SystemExit(f"No profile {p}. Available: {available or 'none'}")
    d = tomllib.loads(p.read_text(encoding="utf-8"))
    return Profile(name=d["name"], collection=d["collection"], title=d["title"],
                   description=d["description"], examples=d.get("examples", {}),
                   prices=d.get("prices", {}), path=p)


def _quoted(items, last_join=", "):
    q = [f'"{x}"' for x in items]
    return ", ".join(q[:-1]) + last_join + q[-1] if len(q) > 1 else "".join(q)


def strip_descriptions(node):
    """Copy of a JSON schema without its string annotations (description, title, $schema).

    A field that is itself named "description" (artwork.description) is kept.
    """
    if isinstance(node, dict):
        return {k: strip_descriptions(v) for k, v in node.items()
                if not (k in ("description", "title", "$schema") and isinstance(v, str))}
    if isinstance(node, list):
        return [strip_descriptions(v) for v in node]
    return node


def load_prompt(profile):
    """Return (prompt text, schema dict, sha256 of prompt + schema) for a profile.

    The schema goes to the model twice: appended to the prompt without its
    descriptions (the prompt is the single source of rules), and as the output format.
    """
    ex, prices = profile.examples, profile.prices
    dollar = prices.get("unmarked_dollar", "")
    values = {
        "title": profile.title,
        "description": profile.description,
        # an empty example drops its "e.g." clause rather than suggesting a wrong value
        **{f"{k}_example": f', e.g. "{ex[k]}"' if ex.get(k) else "" for k in ("masthead", "tagline", "publisher")},
        "unmarked_dollar_rule": f'; a "$" with no country stated is {dollar}' if dollar else "",
        "entity_examples": _quoted(ex.get("entities", [])),
        "entity_variant_examples": _quoted(ex.get("entity_variants", []), " and "),
        "generic_term_examples": _quoted(ex.get("generic_terms", []), " or "),
        "topic_examples": _quoted(ex.get("topics", [])),
    }
    text = (PROMPTS_DIR / "cover_prompt.txt").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise SystemExit("Unfilled placeholder in the prompt: check the profile's [examples]")
    schema = json.loads((PROMPTS_DIR / "cover_schema.json").read_text(encoding="utf-8"))
    text += "\n" + json.dumps(strip_descriptions(schema), separators=(",", ":"), ensure_ascii=False)
    sha = hashlib.sha256((text + json.dumps(schema, sort_keys=True)).encode()).hexdigest()
    return text, schema, sha


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_by_image(path):
    """The last row for each image in a JSONL file (later rows win)."""
    return {r["image"]: r for r in read_jsonl(path)}


def append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
