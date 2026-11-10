"""Step 1: download the first page image (the cover) of every item in a collection.

    ia2tropy fetch byte                              # every item in the profile's collection
    ia2tropy fetch byte --limit 5                    # the first 5 items, by identifier
    ia2tropy fetch byte --items byte-magazine-1987-05 byte-magazine-1993-06   # these items
    ia2tropy fetch byte --query "year:1985"          # items matching an IA search, in the collection
    ia2tropy fetch byte --query "year:[1980 TO 1984]" --limit 10

Selecting a subset: --items names items directly; --query adds Internet Archive search
terms to the collection search (see https://archive.org/advancedsearch.php for fields);
--limit keeps the first N of the selected items, sorted by identifier. Later steps work
on whatever has been fetched, so a subset carries through the whole pipeline.

For each item, finds its page-image archive (*_jp2.zip or *_jp2.tar), picks the
lowest-numbered .jp2 inside (normally *_0000.jp2), and saves it as
data/<profile>/originals/<identifier>.jp2. Only that one file is downloaded, using
archive.org's archive-browsing URLs; if that fails for a zip, the file is read directly
out of the remote zip with HTTP range requests. The full archive is never downloaded.

Existing files are skipped, so the step can be re-run to fill gaps. A manifest
(data/<profile>/manifest.tsv) records each item's identifier, local file, and the
source file inside the item.
"""
import argparse, csv, io, re, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote

from internetarchive import get_session

from .config import load_profile

USER_AGENT_SUFFIX = "ia-magazines-to-tropy (fetching magazine cover pages)"
WORKERS = 4
ARCHIVE_SUFFIXES = ("_jp2.zip", "_jp2.tar")
PAGE_NUM = re.compile(r"(\d+)\.jp2$", re.IGNORECASE)

session = None


def get(url, **kw):
    """GET with retries on rate limiting and transient server errors."""
    for attempt in range(5):
        r = session.get(url, timeout=120, **kw)
        if r.status_code in (429, 502, 503, 504) and attempt < 4:
            time.sleep(int(r.headers.get("Retry-After", 2 ** attempt * 5)))
            continue
        r.raise_for_status()
        return r


def first_page(names):
    """The lowest-numbered .jp2 in a list of file names."""
    jp2s = [n for n in names if n.lower().endswith(".jp2")]
    if not jp2s:
        return None

    def page(n):
        m = PAGE_NUM.search(n)
        return (int(m.group(1)) if m else 1 << 30, n)
    return min(jp2s, key=page)


def save_stream(resp, dest):
    tmp = dest.with_suffix(".part")
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(1 << 16):
            fh.write(chunk)
    tmp.replace(dest)


class HTTPRangeFile(io.RawIOBase):
    """Minimal seekable read-only file over HTTP range requests (for zipfile)."""

    def __init__(self, url):
        r = session.head(url, allow_redirects=True, timeout=60)
        r.raise_for_status()
        self.url, self.size, self.pos = r.url, int(r.headers["Content-Length"]), 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        data = get(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"}).content
        self.pos += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def fetch_via_listing(identifier, archive, dest):
    base = f"https://archive.org/download/{quote(identifier)}/{quote(archive)}/"
    listing = get(base).text
    prefix = re.escape(f"//archive.org/download/{quote(identifier)}/{quote(archive)}/")
    members = re.findall(rf'href="{prefix}([^"&]+\.jp2)"', listing, re.IGNORECASE)
    member = first_page([unquote(m) for m in members])
    if member is None:
        raise LookupError("no .jp2 inside archive")
    with get(base + quote(member), stream=True) as r:
        save_stream(r, dest)
    return member


def fetch_via_range(identifier, archive, dest):
    url = f"https://archive.org/download/{quote(identifier)}/{quote(archive)}"
    with zipfile.ZipFile(io.BufferedReader(HTTPRangeFile(url), 1 << 20)) as zf:
        member = first_page(zf.namelist())
        if member is None:
            raise LookupError("no .jp2 inside zip")
        tmp = dest.with_suffix(".part")
        with zf.open(member) as src, open(tmp, "wb") as fh:
            while chunk := src.read(1 << 20):
                fh.write(chunk)
        tmp.replace(dest)
    return member


def fetch_one(identifier, outdir):
    dest = outdir / f"{identifier}.jp2"
    if dest.exists():
        return identifier, "skip", None
    item = session.get_item(identifier)
    archives = [f["name"] for f in item.files if f["name"].endswith(ARCHIVE_SUFFIXES)]
    if not archives:
        return identifier, "fail", "no _jp2.zip or _jp2.tar in item"
    archive = sorted(archives, key=lambda a: not a.endswith(".zip"))[0]
    try:
        member = fetch_via_listing(identifier, archive, dest)
    except Exception as e:
        if not archive.endswith(".zip"):
            raise
        print(f"  {identifier}: listing/extract failed ({e!r}); trying range read", flush=True)
        member = fetch_via_range(identifier, archive, dest)
    return identifier, "ok", f"{archive}/{member}"


def load_manifest(path):
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {row["identifier"]: row for row in csv.DictReader(fh, delimiter="\t")}


def write_manifest(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["identifier", "local_file", "original_member"], delimiter="\t")
        w.writeheader()
        for ident in sorted(rows):
            w.writerow(rows[ident])


def main(argv=None):
    global session
    p = argparse.ArgumentParser(prog="ia2tropy fetch", description=__doc__.split("\n")[0])
    p.add_argument("profile", help="profile name (profiles/<name>.toml) or path")
    p.add_argument("--items", nargs="+", metavar="ID", help="fetch only these item identifiers")
    p.add_argument("--query", help='extra Internet Archive search terms, e.g. "year:1985"')
    p.add_argument("--limit", type=int, help="only the first N selected items, sorted by identifier")
    a = p.parse_args(argv)
    profile = load_profile(a.profile)
    outdir = profile.originals
    outdir.mkdir(parents=True, exist_ok=True)
    session = get_session(config={"general": {"user_agent_suffix": USER_AGENT_SUFFIX}})

    if a.items:
        ids = sorted(set(a.items))
        print(f"{len(ids)} items named on the command line", flush=True)
    else:
        search = f"collection:{profile.collection}" + (f" AND ({a.query})" if a.query else "")
        ids = sorted(r["identifier"] for r in session.search_items(search))
        print(f"{len(ids)} items matching {search}", flush=True)
        if not ids:
            raise SystemExit("No items found; check the profile's collection and any --query")
    if a.limit:
        ids = ids[:a.limit]
    manifest = load_manifest(profile.manifest)
    counts = {"ok": 0, "skip": 0, "fail": 0}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch_one, i, outdir): i for i in ids}
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                ident, status, info = fut.result()
            except Exception as e:
                ident, status, info = futs[fut], "fail", repr(e)
            counts[status] += 1
            if status == "ok":
                manifest[ident] = {"identifier": ident, "local_file": f"{ident}.jp2",
                                   "original_member": info}
            if status != "skip" or n % 50 == 0:
                print(f"[{n}/{len(ids)}] {status} {ident}: {info or ''}", flush=True)
    write_manifest(profile.manifest, manifest)
    print(counts)
