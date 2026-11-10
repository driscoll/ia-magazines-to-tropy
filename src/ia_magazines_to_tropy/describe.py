"""Step 3: describe every cover with a vision-language model.

    ia2tropy describe byte                              # local: qwen3-vl:32b via Ollama
    ia2tropy describe byte --model qwen3-vl:8b          # local, smaller model (16 GB Mac)
    ia2tropy describe byte --backend anthropic --api-key-file ~/.config/anthropic-key
    ia2tropy describe byte --limit 3                    # a quick check on 3 covers
    ia2tropy describe byte --retry-failed               # rerun covers left without a record

Appends one row per cover to data/<profile>/descriptions.jsonl as each cover finishes:
the model's record, its reasoning (full text locally; a summary, when there is one,
from the Anthropic API), timings, and provenance (git commit, software versions, model,
prompt version and hash). The file is never edited; the review step writes corrections
to a separate file. The exact prompt and schema of each run are kept in
data/<profile>/prompts/, named by that hash.

Safe to stop and restart: covers already described with the same model and prompt are
skipped. Locally, a cover that runs out of tokens or returns invalid JSON is retried once
with the next seed and a larger token cap; the failed attempt is kept in the row.
"""
import argparse, base64, io, json, os, platform, subprocess, sys, time, urllib.request
from pathlib import Path

from PIL import Image

from . import __version__
from .config import (PROMPT_VERSION, ROOT, append_jsonl, load_profile, load_prompt, read_jsonl, save_prompt,
                     strip_descriptions)

NS = 1e9
# Qwen's recommended sampling for thinking models. Greedy decoding (temperature 0)
# made the reasoning loop on hard covers in testing.
QWEN_SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
ANTHROPIC_MODEL = "claude-opus-5-5"
ANTHROPIC_PRICES = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}  # USD per M tokens
ANTHROPIC_MAX_IMAGE_B64 = 9_500_000   # the API limit is 10 MB base64 per image


def provenance(backend):
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    prov = {"tool_version": __version__, "git_commit": git("rev-parse", "HEAD"),
            # uncommitted changes mean the commit alone doesn't describe the code
            "git_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
            "host": platform.node(), "os": platform.platform(), "python": platform.python_version(),
            "run_started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if backend == "ollama":
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=5) as r:
                prov["ollama_version"] = json.load(r).get("version")
        except OSError:
            prov["ollama_version"] = None
    else:
        import anthropic
        prov["anthropic_sdk"] = anthropic.__version__
    return prov


def png_bytes(path):
    with Image.open(path) as im:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), im.size


# ---------- local models (Ollama) ----------

def ollama_attempt(client, model, img, dims, name, prompt, num_ctx, num_predict, seed):
    text, schema, sha = prompt
    t0 = time.time()
    r = client.chat(model=model, messages=[{"role": "user", "content": text, "images": [img]}],
                    format=schema, think=True, keep_alive="30m",
                    options={**QWEN_SAMPLING, "seed": seed, "num_ctx": num_ctx, "num_predict": num_predict})
    wall = time.time() - t0
    content = r.message.content or ""
    try:
        output, valid = json.loads(content), True
    except json.JSONDecodeError:
        output, valid = content, False
    return {"backend": "ollama", "model": model, "image": name, "dims": dims, "seed": seed,
            "num_ctx": num_ctx, "num_predict": num_predict, "done_reason": r.done_reason,
            "wall_s": round(wall, 2), "total_s": r.total_duration / NS,
            "prompt_tokens": r.prompt_eval_count, "prompt_s": (r.prompt_eval_duration or 0) / NS,
            "gen_tokens": r.eval_count, "gen_s": (r.eval_duration or 0) / NS,
            "thinking": r.message.thinking or "", "valid_json": valid, "output": output}


def describe_ollama(client, model, path, prompt, a, say):
    img, dims = png_bytes(path)
    failed = []
    for n_try in range(a.retries + 1):
        seed = a.seed + n_try
        cap = a.num_predict if n_try == 0 else a.retry_num_predict
        row = ollama_attempt(client, model, img, dims, path.stem, prompt, a.num_ctx, cap, seed)
        if row["done_reason"] != "length" and row["valid_json"]:
            break
        if n_try < a.retries:
            failed.append(row)
            say(f"  {path.stem}: {'ran out of tokens' if row['done_reason'] == 'length' else 'invalid JSON'}; "
                f"retrying with seed {seed + 1} and a {a.retry_num_predict}-token cap")
    return {**row, "seed": a.seed, "final_seed": seed, "failed_attempts": failed,
            "total_s_incl_retries": sum(x["total_s"] for x in failed) + row["total_s"]}


# ---------- Anthropic API ----------

def api_schema(node):
    """Adapt the schema for Claude structured outputs: drop keywords the API doesn't
    support (the list limits are stated in the prompt), turn ["string", "null"] type
    lists into anyOf, and add additionalProperties: false to every object."""
    if isinstance(node, list):
        return [api_schema(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: api_schema(v) for k, v in node.items() if k not in ("minimum", "maximum", "maxItems", "minItems")}
    if isinstance(out.get("type"), list):
        out["anyOf"] = [{"type": t} for t in out.pop("type")]
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def describe_anthropic(client, model, path, prompt, a, raw_dir):
    text, schema, sha = prompt
    data, media = path.read_bytes(), "image/png"
    if len(data) * 4 / 3 > ANTHROPIC_MAX_IMAGE_B64:
        buf = io.BytesIO()
        with Image.open(path) as im:
            im.convert("RGB").save(buf, format="JPEG", quality=92)
        data, media = buf.getvalue(), "image/jpeg"
    with Image.open(path) as im:
        dims = im.size
    t0 = time.time()
    with client.beta.messages.stream(
        model=model, max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",          # if a safety filter declines, rerun on another Claude model
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": a.effort,
                       "format": {"type": "json_schema", "schema": api_schema(strip_descriptions(schema))}},
        messages=[{"role": "user", "content": [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},   # same for every cover
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.standard_b64encode(data).decode()}},
        ]}],
    ) as stream:
        msg = stream.get_final_message()
    wall = time.time() - t0
    # keep the raw response first, so a problem below can't lose a paid result
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{path.stem}-{msg.id}.json").write_text(json.dumps(msg.to_dict(), ensure_ascii=False),
                                                         encoding="utf-8")
    thinking = "\n\n".join(b.thinking for b in msg.content if b.type == "thinking")
    out_text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        output, valid = json.loads(out_text), True
    except json.JSONDecodeError:
        output, valid = out_text, False
    u = msg.usage
    cw, cr = u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0
    cost = (u.input_tokens * ANTHROPIC_PRICES["input"] + cw * ANTHROPIC_PRICES["cache_write"]
            + cr * ANTHROPIC_PRICES["cache_read"] + u.output_tokens * ANTHROPIC_PRICES["output"]) / 1e6
    fell_back = any(getattr(i, "type", "") == "fallback_message" for i in (getattr(u, "iterations", None) or []))
    return {"backend": "anthropic", "model": msg.model, "message_id": msg.id, "image": path.stem,
            "dims": dims, "image_media_type": media, "effort": a.effort, "done_reason": msg.stop_reason,
            "wall_s": round(wall, 2), "total_s": wall, "total_s_incl_retries": wall,
            "prompt_tokens": u.input_tokens + cw + cr, "gen_tokens": u.output_tokens,
            "cost_usd": round(cost, 4), "fell_back": fell_back,
            "thinking": thinking, "valid_json": valid, "output": output}


def anthropic_client(key_file):
    try:
        import anthropic
    except ImportError:
        raise SystemExit("The Anthropic backend needs the optional extra: uv sync --extra anthropic")
    if key_file:
        path = Path(key_file).expanduser().resolve()
        if ROOT in path.parents:
            raise SystemExit(f"{path} is inside the project folder. Keep the key file outside the "
                             "repository so it can never be committed.")
        return anthropic.Anthropic(api_key=path.read_text(encoding="utf-8").strip())
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("No API key: pass --api-key-file PATH or set ANTHROPIC_API_KEY (see README).")
    return anthropic.Anthropic()


# ---------- run ----------

def main(argv=None):
    p = argparse.ArgumentParser(prog="ia2tropy describe", description=__doc__.split("\n")[0])
    p.add_argument("profile")
    p.add_argument("--backend", choices=["ollama", "anthropic"], default="ollama")
    p.add_argument("--model", help="default: qwen3-vl:32b (ollama) or claude-opus-5-5 (anthropic)")
    p.add_argument("--images", nargs="*", help="only these covers (file names without .png)")
    p.add_argument("--limit", type=int, help="stop after this many new covers")
    p.add_argument("--retry-failed", action="store_true", help="rerun covers whose last row has no record")
    p.add_argument("--api-key-file", help="file holding an Anthropic API key, outside this folder")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"],
                   help="Anthropic only: how much the model may reason")
    p.add_argument("--seed", type=int, default=42, help="Ollama only")
    p.add_argument("--num-ctx", type=int, default=32768, help="Ollama only")
    p.add_argument("--num-predict", type=int, default=12000, help="Ollama only: token cap")
    p.add_argument("--retries", type=int, default=1, help="Ollama only")
    p.add_argument("--retry-num-predict", type=int, default=20000, help="Ollama only")
    a = p.parse_args(argv)

    profile = load_profile(a.profile)
    model = a.model or ("qwen3-vl:32b" if a.backend == "ollama" else ANTHROPIC_MODEL)
    prompt = load_prompt(profile)
    out, log_path = profile.descriptions, profile.data / "describe.log"
    profile.data.mkdir(parents=True, exist_ok=True)

    def say(msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    done = {}
    for r in read_jsonl(out):
        if r.get("prompt_sha256") == prompt[2] and r.get("model_requested", r["model"]) == model:
            done[r["image"]] = r["valid_json"]
    covers = sorted(profile.model_input.glob("*.png"))
    if a.images:
        covers = [c for c in covers if c.stem in set(a.images)]
    if not covers:
        raise SystemExit(f"No images in {profile.model_input}; run 'ia2tropy prepare {a.profile}' first")
    todo = [c for c in covers if c.stem not in done or (a.retry_failed and not done[c.stem])]
    if a.limit:
        todo = todo[:a.limit]
    say(f"{len(covers)} covers, {len(covers) - len(todo)} already done, {len(todo)} to describe with "
        f"{model} ({a.backend}), prompt {PROMPT_VERSION} ({prompt[2][:12]})")
    if not todo:
        return
    say(f"Prompt and schema kept in {save_prompt(profile, prompt)}")

    prov = provenance(a.backend)
    if prov["git_dirty"]:
        say("Note: uncommitted changes to tracked files; the recorded git commit won't fully describe the code")
    if a.backend == "ollama":
        import ollama
        client = ollama.Client()
        try:
            digest = next((m.digest for m in client.list().models if m.model == model), None)
        except Exception as e:
            raise SystemExit(f"Can't reach Ollama ({e}). Is it running? See README.")
        if digest is None:
            raise SystemExit(f"Model {model} isn't installed. Run: ollama pull {model}")
        warm, _ = png_bytes(todo[0])
        client.chat(model=model, messages=[{"role": "user", "content": "Hi", "images": [warm]}],
                    think=True, keep_alive="30m", options={"num_predict": 8, "num_ctx": a.num_ctx})
    else:
        client, digest = anthropic_client(a.api_key_file), None

    times, failed, cost = [], [], 0.0
    for n, path in enumerate(todo, 1):
        if a.backend == "ollama":
            row = describe_ollama(client, model, path, prompt, a, say)
        else:
            row = describe_anthropic(client, model, path, prompt, a, profile.data / "anthropic-raw")
            cost += row["cost_usd"]
        row = {"image": row.pop("image"), "model": row.pop("model"), "model_requested": model,
               "model_digest": digest, "prompt": PROMPT_VERSION, "prompt_sha256": prompt[2],
               "profile": profile.name, **row, "thinking_chars": len(row["thinking"]), "provenance": prov}
        append_jsonl(out, row)
        times.append(row["total_s_incl_retries"])
        if not row["valid_json"]:
            failed.append(path.stem)
        left = sum(times) / len(times) * (len(todo) - n) / 3600
        say(f"[{n}/{len(todo)}] {path.stem}: {'ok' if row['valid_json'] else 'NO RECORD'}, "
            f"{row['total_s_incl_retries']:.0f} s" + (f", ${row['cost_usd']:.3f}" if "cost_usd" in row else "")
            + f"; about {left:.1f} h left")
    say(f"Finished {len(todo)} covers; {len(failed)} without a record"
        + (f": {', '.join(failed)} (rerun with --retry-failed)" if failed else "")
        + (f". Estimated API cost ${cost:.2f}" if a.backend == "anthropic" else ""))
