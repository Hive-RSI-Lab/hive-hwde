"""paperslib - the research-to-checks pipeline's logic (scripts/papers.py).

A standing loop that takes current EE research to something hwde can run:

  watch.yaml   the topics hwde follows, each a few literature queries, the
               EE area it serves, how often it is due, when it last ran
  papers.yaml  the ledger: every paper hwde has seen (kept, dropped or a
               candidate), how it can be read (an open copy, or paywalled:
               on the person's list), its trust signals, and its claims
  claims       a paper's checkable statements, each with a verdict hwde
               reached ITSELF (reproduced / partial / refuted / untested /
               not_checkable) and the evidence that reached it: a test
               function that names the claim id, or a script / sim
  guidelines   a claim's design guideline, allowed only once hwde's own
               evidence reproduced it ("don't blindly trust": a paper is a
               lead, not a fact)

Sources are legitimate only: the open-access copy OpenAlex, Unpaywall or
Semantic Scholar point to, or the publisher's page. Shadow libraries (Sci-Hub,
LibGen, Z-Library, Anna's Archive and their mirrors) are refused wherever a
URL enters the ledger. A paper with no open copy goes on the person's list
(docs/research/paywalled.md): they may have institutional access.

Network calls are kept to fetch_json(); everything else is pure and takes
the parsed JSON, so the tests run offline on recorded responses.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH = ROOT / "reference" / "research"
WATCH = RESEARCH / "watch.yaml"
LEDGER = RESEARCH / "papers.yaml"
DOCS = ROOT / "docs" / "research"

OPENALEX = "https://api.openalex.org"
UNPAYWALL = "https://api.unpaywall.org/v2"
S2 = "https://api.semanticscholar.org/graph/v1"
# OpenAlex's and Unpaywall's polite pool want a contact address.
MAILTO = os.environ.get("HWDE_RESEARCH_MAILTO", "")

STATUSES = ("candidate", "kept", "dropped")
ACCESS = ("open", "paywalled", "person_supplied", "unknown")
VERDICTS = ("untested", "reproduced", "partial", "refuted", "not_checkable")
# A verdict hwde reached itself needs evidence; these are the ones that do.
CHECKED = ("reproduced", "partial", "refuted")

# Shadow libraries: never a source, whatever a search turns up. Matched as
# the host or any subdomain, and as a label anywhere in the host (mirrors
# rotate TLDs: sci-hub.se, sci-hub.ru, libgen.rs, libgen.is ...).
SHADOW = ("sci-hub", "scihub", "libgen", "library.lol", "z-lib", "zlib",
          "z-library", "1lib", "b-ok", "booksc", "annas-archive",
          "anna's-archive", "libstc", "nexus-stc", "freefullpdf", "scidb")


def shadow_host(url: str) -> str | None:
    """The shadow-library name a URL's host matches, else None."""
    if not url:
        return None
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for name in SHADOW:
        if name in host:
            return name
    return None


# ---------------------------------------------------------------- files

def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def dump_yaml(path: Path, data: dict, header: str = "") -> None:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=False,
                          width=88, default_flow_style=False)
    path.write_text(header + body)


def header_of(path: Path) -> str:
    """The comment block a YAML file starts with (kept across rewrites)."""
    out = []
    for ln in path.read_text().splitlines(keepends=True):
        if ln.startswith("#") or not ln.strip():
            out.append(ln)
        else:
            break
    return "".join(out)


def today() -> str:
    return dt.date.today().isoformat()


# ---------------------------------------------------------------- ids

def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "doi:", "https://dx.doi.org/"):
        if d.startswith(p):
            d = d[len(p):]
    return d or None


def paper_key(p: dict) -> str:
    """One key per work: the DOI, else the OpenAlex id, else the arXiv id."""
    return norm_doi(p.get("doi")) or p.get("openalex") or p.get("arxiv") or p["id"]


def slug(title: str, year) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", (title or "").lower())
             if w not in {"a", "an", "the", "of", "for", "and", "in", "on",
                          "with", "to", "by", "using", "via"}]
    return "-".join(words[:5] + [str(year or "")]).strip("-")


# ---------------------------------------------------------------- OpenAlex

def fetch_json(url: str, timeout: float = 30.0) -> dict:
    """GET a JSON API (the one network call). A shadow host is refused."""
    if shadow_host(url):
        raise ValueError(f"refused: {url} is a shadow library")
    req = urllib.request.Request(url, headers={
        "User-Agent": "hive-hwde research pipeline"
        + (f" (mailto:{MAILTO})" if MAILTO else "")})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def openalex_search_url(query: str, since: str | None, per_page: int = 10,
                        types: tuple = ("article", "preprint", "review")) -> str:
    filt = ["type:" + "|".join(types)]
    if since:
        filt.append(f"from_publication_date:{since}")
    q = {"search": query, "filter": ",".join(filt), "per-page": per_page,
         "sort": "relevance_score:desc"}
    if MAILTO:
        q["mailto"] = MAILTO
    return f"{OPENALEX}/works?" + urllib.parse.urlencode(q)


def _abstract(inv: dict | None) -> str:
    """OpenAlex ships abstracts as an inverted index; put the words back."""
    if not inv:
        return ""
    pos = {}
    for word, places in inv.items():
        for i in places:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def open_copies(work: dict) -> list[dict]:
    """Every legitimate open copy OpenAlex lists for a work, best first."""
    out, seen = [], set()
    locs = [work.get("best_oa_location")] + list(work.get("locations") or [])
    for loc in locs:
        if not loc or not loc.get("is_oa"):
            continue
        url = loc.get("pdf_url") or loc.get("landing_page_url")
        if not url or url in seen or shadow_host(url):
            continue
        seen.add(url)
        src = (loc.get("source") or {})
        out.append({"url": url, "pdf": bool(loc.get("pdf_url")),
                    "host": src.get("display_name") or urllib.parse.urlparse(url).hostname,
                    "version": loc.get("version"), "license": loc.get("license")})
    return out


def from_openalex(work: dict) -> dict:
    """A ledger entry (status candidate) from one OpenAlex work."""
    ids = work.get("ids") or {}
    arxiv = None
    for loc in work.get("locations") or []:
        u = (loc or {}).get("landing_page_url") or ""
        m = re.search(r"arxiv\.org/abs/([0-9.]+)", u)
        if m:
            arxiv = m.group(1)
    src = ((work.get("primary_location") or {}).get("source") or {})
    copies = open_copies(work)
    title = work.get("display_name") or work.get("title") or ""
    year = work.get("publication_year")
    entry = {
        "id": slug(title, year),
        "title": title,
        "year": year,
        "doi": norm_doi(work.get("doi") or ids.get("doi")),
        "openalex": (work.get("id") or "").rsplit("/", 1)[-1] or None,
        "arxiv": arxiv,
        "venue": src.get("display_name"),
        "status": "candidate",
        "access": {"state": "open" if copies else "paywalled",
                   "copies": copies[:3]},
        "trust": trust_signals(work),
        "abstract": _abstract(work.get("abstract_inverted_index"))[:1200],
    }
    return {k: v for k, v in entry.items() if v not in (None, "", [])}


def trust_signals(work: dict) -> dict:
    """What says how far to lean on a paper before hwde checks it: retracted,
    peer reviewed (a journal or conference, not a repository), citations."""
    src = ((work.get("primary_location") or {}).get("source") or {})
    stype = src.get("type")
    return {
        "retracted": bool(work.get("is_retracted")),
        "peer_reviewed": stype in ("journal", "conference")
        and work.get("type") != "preprint",
        "source_type": stype or "unknown",
        "cited_by": int(work.get("cited_by_count") or 0),
    }


def unpaywall_copies(doi: str, email: str) -> list[dict]:
    """Unpaywall's open copies of a DOI (it needs a real contact email)."""
    d = fetch_json(f"{UNPAYWALL}/{urllib.parse.quote(doi)}?"
                   + urllib.parse.urlencode({"email": email}))
    out = []
    for loc in d.get("oa_locations") or []:
        url = loc.get("url_for_pdf") or loc.get("url")
        if url and not shadow_host(url):
            out.append({"url": url, "pdf": bool(loc.get("url_for_pdf")),
                        "host": loc.get("host_type"), "version": loc.get("version"),
                        "license": loc.get("license")})
    return out


def s2_copies(doi: str) -> list[dict]:
    d = fetch_json(f"{S2}/paper/DOI:{urllib.parse.quote(doi)}?fields=openAccessPdf")
    pdf = (d.get("openAccessPdf") or {}).get("url")
    return [{"url": pdf, "pdf": True, "host": "semanticscholar"}] \
        if pdf and not shadow_host(pdf) else []


def cache_dir() -> Path:
    """Where fetched copies are kept: outside the repo (they're the
    publishers' and authors', not the module's)."""
    return Path(os.environ.get("HWDE_PAPERS_CACHE")
                or Path.home() / ".cache" / "hwde-papers")


def fetch_copy(url: str, dest: Path, timeout: float = 60.0) -> dict:
    """Download one open copy to dest. A host that answers with anything but
    a PDF (a bot wall: Anubis, Akamai, Cloudflare; a login page) or an HTTP
    error is reported as blocked, never worked around."""
    if shadow_host(url):
        return {"url": url, "ok": False, "blocked": "shadow library: refused"}
    req = urllib.request.Request(url, headers={
        "User-Agent": "hive-hwde research pipeline"
        + (f" (mailto:{MAILTO})" if MAILTO else ""),
        "Accept": "application/pdf"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            final = r.geturl()
    except urllib.error.HTTPError as exc:
        return {"url": url, "ok": False, "blocked": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"url": url, "ok": False, "blocked": f"unreachable: {exc}"}
    if shadow_host(final):
        return {"url": url, "ok": False, "blocked": f"redirected to a shadow library ({final})"}
    if not body.startswith(b"%PDF"):
        return {"url": url, "ok": False,
                "blocked": "not a PDF (a bot wall or a landing page)"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    import hashlib
    return {"url": url, "ok": True, "path": str(dest), "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}


# ---------------------------------------------------------------- watch

def due_topics(watch: dict, on: str | None = None) -> list[dict]:
    """The topics whose interval has passed since they last ran."""
    day = dt.date.fromisoformat(on or today())
    out = []
    for t in watch.get("topics") or []:
        last = t.get("last_scanned")
        if not last or (day - dt.date.fromisoformat(str(last))).days \
                >= int(t.get("every_days", 30)):
            out.append(t)
    return out


def merge_candidates(ledger: dict, found: list[dict], topic: str) -> list[dict]:
    """Add the works the ledger hasn't seen (kept, dropped or candidate) as
    candidates under a topic; returns the ones added."""
    papers = ledger.setdefault("papers", [])
    seen = {paper_key(p) for p in papers}
    ids = {p["id"] for p in papers}
    added = []
    for p in found:
        k = paper_key(p)
        if k in seen:
            continue
        pid, n = p["id"], 2
        while pid in ids:
            pid, n = f"{p['id']}-{n}", n + 1
        p = {**p, "id": pid, "topics": [topic], "found": today()}
        papers.append(p)
        seen.add(k)
        ids.add(pid)
        added.append(p)
    return added


# ---------------------------------------------------------------- lint

def _test_functions(path: Path) -> dict[str, str]:
    """name -> source of every top-level test function in a test file."""
    src = path.read_text()
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            out[node.name] = ast.get_source_segment(src, node) or ""
    return out


def evidence_problems(claim: dict, root: Path = ROOT) -> list[str]:
    """A checked verdict's evidence must exist and name the claim: a pytest
    node `tests/x.py::test_y` whose source carries the claim id, or a script
    or sim file in the repo that does."""
    ev = claim.get("evidence")
    cid = claim["id"]
    if not ev:
        return [f"{cid}: verdict {claim.get('verdict')} with no evidence"]
    probs = []
    for ref in ([ev] if isinstance(ev, str) else ev):
        path, _, fn = ref.partition("::")
        f = root / path
        if not f.is_file():
            probs.append(f"{cid}: evidence {path} doesn't exist")
            continue
        if fn:
            fns = _test_functions(f)
            if fn not in fns:
                probs.append(f"{cid}: evidence {ref}: no such test")
            elif cid not in fns[fn]:
                probs.append(f"{cid}: evidence {ref} doesn't name the claim")
        elif cid not in f.read_text(errors="replace"):
            probs.append(f"{cid}: evidence {path} doesn't name the claim")
    return probs


def lint(ledger: dict, watch: dict, root: Path = ROOT) -> list[str]:
    """Everything the pipeline promises, checked on the ledger as written."""
    probs = []
    topics = {t["id"] for t in watch.get("topics") or []}
    for t in watch.get("topics") or []:
        if not t.get("queries"):
            probs.append(f"topic {t['id']}: no queries")
        if not t.get("area"):
            probs.append(f"topic {t['id']}: no EE area")
    ids, keys, cids = set(), set(), set()
    for p in ledger.get("papers") or []:
        pid = p.get("id", "?")
        if pid in ids:
            probs.append(f"{pid}: duplicate paper id")
        ids.add(pid)
        k = paper_key(p)
        if k in keys:
            probs.append(f"{pid}: the same work is in the ledger twice ({k})")
        keys.add(k)
        if p.get("status") not in STATUSES:
            probs.append(f"{pid}: status {p.get('status')!r} not one of {STATUSES}")
        for t in p.get("topics") or []:
            if t not in topics:
                probs.append(f"{pid}: topic {t} isn't in watch.yaml")
        acc = p.get("access") or {}
        if acc.get("state") not in ACCESS:
            probs.append(f"{pid}: access {acc.get('state')!r} not one of {ACCESS}")
        for c in acc.get("copies") or []:
            bad = shadow_host(c.get("url", ""))
            if bad:
                probs.append(f"{pid}: copy {c['url']} is a shadow library ({bad})")
        if p.get("status") == "dropped" and not p.get("why"):
            probs.append(f"{pid}: dropped with no why")
        claims = p.get("claims") or []
        if claims and p.get("status") != "kept":
            probs.append(f"{pid}: has claims but isn't kept")
        if p.get("status") == "kept" and not p.get("why"):
            probs.append(f"{pid}: kept with no why (what hwde wants from it)")
        retracted = (p.get("trust") or {}).get("retracted")
        for c in claims:
            cid = c.get("id", "?")
            if cid in cids:
                probs.append(f"{cid}: duplicate claim id")
            cids.add(cid)
            if not cid.startswith(pid + "/"):
                probs.append(f"{cid}: a claim id is <paper id>/<n>")
            if not c.get("claim"):
                probs.append(f"{cid}: no claim text")
            if not c.get("where"):
                probs.append(f"{cid}: no page/section/figure it was read from")
            v = c.get("verdict")
            if v not in VERDICTS:
                probs.append(f"{cid}: verdict {v!r} not one of {VERDICTS}")
                continue
            if v in CHECKED:
                probs.extend(evidence_problems(c, root))
                if not c.get("finding"):
                    probs.append(f"{cid}: verdict {v} with no finding (what the check showed)")
            if v == "not_checkable" and not c.get("why"):
                probs.append(f"{cid}: not_checkable with no why")
            if v == "untested" and not c.get("plan"):
                probs.append(f"{cid}: untested with no plan (the test, script or sim that would check it)")
            if c.get("guideline") and v not in ("reproduced", "partial"):
                probs.append(f"{cid}: a guideline from a claim hwde hasn't reproduced ({v})")
            if retracted and v in ("reproduced", "partial") and c.get("guideline"):
                probs.append(f"{cid}: a guideline from a retracted paper")
            if (acc.get("state") == "paywalled" and v in CHECKED
                    and c.get("read_from") != "abstract"):
                probs.append(f"{cid}: checked against a paywalled paper hwde can't have read (read_from: abstract, or mark the access person_supplied)")
    return probs


# ---------------------------------------------------------------- reports

def queue(ledger: dict) -> dict:
    """The pipeline's work, in the order it should be done."""
    ps = ledger.get("papers") or []
    return {
        "triage": [p["id"] for p in ps if p.get("status") == "candidate"],
        "read": [p["id"] for p in ps if p.get("status") == "kept"
                 and not p.get("claims")
                 and (p.get("access") or {}).get("state") in ("open", "person_supplied")
                 and not (p.get("access") or {}).get("blocked")],
        "test": [c["id"] for p in ps for c in p.get("claims") or []
                 if c.get("verdict") == "untested"],
        "paywalled": [p["id"] for p in ps if p.get("status") == "kept"
                      and (p.get("access") or {}).get("state") == "paywalled"],
        "blocked": [p["id"] for p in ps if p.get("status") == "kept"
                    and (p.get("access") or {}).get("blocked")],
    }


def _link(p: dict) -> str:
    if p.get("doi"):
        return f"https://doi.org/{p['doi']}"
    if p.get("arxiv"):
        return f"https://arxiv.org/abs/{p['arxiv']}"
    return f"https://openalex.org/{p.get('openalex', '')}"


GEN = ("<!-- Generated by `hwde papers docs` from reference/research/papers.yaml;"
       " edit the ledger, not this page. -->\n\n")


def paywalled_md(ledger: dict) -> str:
    rows = [p for p in ledger.get("papers") or []
            if p.get("status") == "kept"
            and (p.get("access") or {}).get("state") == "paywalled"]
    out = [GEN, "# Papers hwde wants and can't read\n\n",
           "These have no legitimate open copy (OpenAlex and Semantic Scholar, and "
           "Unpaywall given HWDE_RESEARCH_MAILTO, find none). If you have "
           "institutional access, a copy put "
           "where the agent can read it (not committed: it's the publisher's) and "
           "the paper's `access.state` set to `person_supplied` puts it back in "
           "the reading queue. hwde doesn't use shadow libraries.\n\n"]
    def table(ps):
        if not ps:
            return ["None right now.\n"]
        t = ["| Paper | Year | Venue | Why hwde wants it |\n|---|---|---|---|\n"]
        for p in ps:
            title = p["title"].replace("|", "/")
            t.append(f"| [{title}]({_link(p)}) | {p.get('year', '')} | "
                     f"{(p.get('venue') or '').replace('|', '/')} | "
                     f"{(p.get('why') or '').strip().replace(chr(10), ' ')} |\n")
        return t
    out += table(rows)
    walled = [p for p in ledger.get("papers") or []
              if p.get("status") == "kept"
              and (p.get("access") or {}).get("state") == "open"
              and (p.get("access") or {}).get("blocked")]
    out.append("\n## Open, but every copy's host turns robots away\n\n"
               "These are free to read; the hosts' bot walls stop the agent "
               "(it doesn't get around them). Saving the PDF from a browser "
               "and marking the paper `person_supplied` puts it in the "
               "reading queue.\n\n")
    out += table(walled)
    return "".join(out)


def guidelines_md(ledger: dict) -> str:
    out = [GEN, "# Design guidelines from research hwde has checked\n\n",
           "Each line is a claim from a paper that hwde's own test, script or "
           "simulation reproduced (or reproduced in part, within the scope "
           "given). A claim hwde hasn't checked is never a guideline here; "
           "`docs/research/claims.md` lists every claim and its verdict.\n\n"]
    n = 0
    for p in ledger.get("papers") or []:
        for c in p.get("claims") or []:
            if c.get("guideline") and c.get("verdict") in ("reproduced", "partial"):
                ev = c["evidence"] if isinstance(c["evidence"], str) else ", ".join(c["evidence"])
                scope = " (in part)" if c["verdict"] == "partial" else ""
                out.append(f"- {c['guideline'].strip()}{scope}  \n"
                           f"  From [{p['title']}]({_link(p)}) ({p.get('year', '')}), "
                           f"{c['where']}; checked by `{ev}` (`{c['id']}`).\n")
                n += 1
    if not n:
        out.append("None yet.\n")
    return "".join(out)


def claims_md(ledger: dict) -> str:
    out = [GEN, "# Every research claim hwde holds, and what it found\n\n",
           "| Claim | Paper | Verdict | What hwde found | Evidence or plan |\n",
           "|---|---|---|---|---|\n"]
    for p in ledger.get("papers") or []:
        for c in p.get("claims") or []:
            v = c.get("verdict")
            how = c.get("evidence") or c.get("plan") or c.get("why") or ""
            if isinstance(how, list):
                how = ", ".join(how)
            cell = lambda s: (s or "").strip().replace("|", "/").replace("\n", " ")
            out.append(f"| `{c['id']}` {cell(c.get('claim'))} | [{cell(p['title'])}]({_link(p)}) "
                       f"| {v} | {cell(c.get('finding'))} | {cell(how)} |\n")
    return "".join(out)
