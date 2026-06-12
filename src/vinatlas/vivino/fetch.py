"""Vivino raw fetcher.

Captures full, unparsed JSON payloads into data/raw/vivino/<entity>/*.jsonl
(one envelope per line: fetched_at, endpoint, params, http_status, payload).
Parsing happens later, in the DuckDB layer — never here.

Fail-loud: a 200 response with an unexpected shape aborts the run (the raw
envelope is written to disk before validation, so the offending payload is
always inspectable). Recovery after an abort is a full re-run; views dedupe
re-fetched entities by latest fetched_at.

Run from the project root:
    uv run vinatlas-fetch [run] [--region-id 734] [--country-code si] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

BASE = "https://www.vivino.com"

# Pinned impersonation targets. Floating "chrome" alias can trip the CloudFront
# WAF challenge (curl_cffi issue #500); rotate to the next target on 403.
IMPERSONATE_TARGETS = ["chrome131", "safari", "firefox"]

# impersonate already injects UA / sec-ch-ua / accept-language — never override those.
XHR_HEADERS = {
    # NOT the browser's "application/json, text/plain, */*": Vivino's content
    # negotiation returns 415 when text/plain is in the list (verified live).
    "Accept": "application/json",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Referer": f"{BASE}/",
    "Origin": BASE,
}

RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRIES = 5
BACKOFF_CAP_S = 30.0
POLITENESS_S = 1.0  # ~1 req/s with jitter
TIMEOUT_S = 30
EXPLORE_PAGE_CAP = 50  # safety cap, Brda needs ~9


class FetchAbort(RuntimeError):
    """Unrecoverable condition (WAF block, shape drift): stop the run."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def strict_get(value: object, key: str, context: str):
    """Fail loud: a 200 payload missing an expected key means shape drift."""
    if not isinstance(value, dict) or value.get(key) is None:
        raise FetchAbort(f"shape drift at {context}: missing '{key}' (raw envelope recorded)")
    return value[key]


class VivinoClient:
    """curl_cffi Session with browser TLS fingerprint, cookie warming and
    transient-only retries. 403 rotates the impersonation target (never a
    retry loop); all targets exhausted -> FetchAbort. 404 is returned as-is."""

    def __init__(self) -> None:
        self._target_idx = 0
        self._warm_session()

    def _warm_session(self) -> None:
        target = IMPERSONATE_TARGETS[self._target_idx]
        log(f"[client] new session, impersonate={target}, warming cookies")
        self._session = requests.Session(impersonate=target)
        try:
            resp = self._session.get(BASE + "/", timeout=TIMEOUT_S)
        except RequestException as exc:
            raise FetchAbort(f"homepage warm-up failed ({type(exc).__name__}: {exc})")
        if resp.status_code != 200:
            log(f"[client] warning: homepage warm-up returned {resp.status_code}")

    def _rotate_target(self) -> None:
        self._target_idx += 1
        if self._target_idx >= len(IMPERSONATE_TARGETS):
            raise FetchAbort(
                "403 across all impersonation targets "
                f"({', '.join(IMPERSONATE_TARGETS)}) — aborting run"
            )
        self._warm_session()

    def _request(self, path: str, params: dict | None) -> requests.Response:
        attempt = 0
        while True:
            time.sleep(POLITENESS_S * random.uniform(0.7, 1.3))
            try:
                resp = self._session.get(
                    BASE + path, params=params, headers=XHR_HEADERS, timeout=TIMEOUT_S
                )
            except RequestException as exc:  # connection errors are transient -> retry
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise FetchAbort(f"connection errors exhausted retries on {path}: {exc}")
                delay = random.uniform(0, min(BACKOFF_CAP_S, 2.0**attempt))
                log(f"[client] {path}: {type(exc).__name__}, retry {attempt}/{MAX_RETRIES} in {delay:.1f}s")
                time.sleep(delay)
                continue

            if resp.status_code == 403:
                log(f"[client] 403 on {path} with {IMPERSONATE_TARGETS[self._target_idx]}, rotating target")
                self._rotate_target()
                continue

            if resp.status_code in RETRY_STATUSES:
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise FetchAbort(f"{resp.status_code} on {path} exhausted retries")
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)
                else:
                    delay = random.uniform(0, min(BACKOFF_CAP_S, 2.0**attempt))
                log(f"[client] {resp.status_code} on {path}, retry {attempt}/{MAX_RETRIES} in {delay:.1f}s")
                time.sleep(delay)
                continue

            return resp

    def get_json(self, path: str, params: dict | None = None) -> tuple[int, object]:
        resp = self._request(path, params)
        if resp.status_code == 404:
            return 404, None
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"_unparsed_text": resp.text}


class RawWriter:
    """Appends one envelope per response to data/raw/vivino/<entity>/<run_id>.jsonl."""

    def __init__(self, data_dir: Path, run_id: str) -> None:
        self.root = data_dir / "raw" / "vivino"
        self.run_id = run_id

    def write(self, entity: str, endpoint: str, params: dict, status: int, payload: object) -> None:
        entity_dir = self.root / entity
        entity_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "params": params,
            "http_status": status,
            "payload": payload,
        }
        with open(entity_dir / f"{self.run_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def fetch_and_record(
    client: VivinoClient,
    writer: RawWriter,
    entity: str,
    endpoint: str,
    query: dict | None = None,
    meta: dict | None = None,
) -> tuple[int, object]:
    """One request: fetch, write the raw envelope FIRST, then validate JSON-ness."""
    status, payload = client.get_json(endpoint, query)
    writer.write(entity, endpoint, {**(query or {}), **(meta or {})}, status, payload)
    if status == 200 and isinstance(payload, dict) and "_unparsed_text" in payload:
        raise FetchAbort(f"non-JSON 200 body on {endpoint} (raw envelope recorded)")
    return status, payload


def fetch_winery(client: VivinoClient, writer: RawWriter, winery_id: int, name: str) -> list[int]:
    """Catalog + per-vintage stats for one winery. Returns catalog wine ids.
    404 is a tolerated skip (dead entity); any other shape surprise aborts."""
    endpoint = f"/api/wineries/{winery_id}/wines"
    status, payload = fetch_and_record(client, writer, "winery_wines", endpoint, meta={"winery_id": winery_id})
    if status == 404:
        log(f"[winery {name}] catalog 404, skipping")
        return []
    if status != 200:
        raise FetchAbort(f"unexpected http {status} on {endpoint}")
    wines = strict_get(payload, "wines", endpoint)
    log(f"[winery {name}] catalog: {len(wines)} wines")

    endpoint = f"/api/wineries/{winery_id}/vintages"
    status, _ = fetch_and_record(client, writer, "winery_vintages", endpoint, meta={"winery_id": winery_id})
    if status == 404:
        log(f"[winery {name}] vintages 404, skipping")
    elif status != 200:
        raise FetchAbort(f"unexpected http {status} on {endpoint}")

    return [strict_get(w, "id", f"winery {name} catalog wine entry") for w in wines]


def fetch_reviews(client: VivinoClient, writer: RawWriter, wine_ids: list[int]) -> None:
    """First review page (50) per wine — full texts for phase-2 reasoning."""
    for i, wine_id in enumerate(wine_ids, 1):
        endpoint = f"/api/wines/{wine_id}/reviews"
        status, payload = fetch_and_record(
            client, writer, "reviews", endpoint,
            query={"per_page": 50, "page": 1}, meta={"wine_id": wine_id},
        )
        if status == 404:
            log(f"[reviews {i}/{len(wine_ids)}] wine {wine_id}: 404, skipping")
            continue
        if status != 200:
            raise FetchAbort(f"unexpected http {status} on {endpoint}")
        n = len(strict_get(payload, "reviews", endpoint))
        log(f"[reviews {i}/{len(wine_ids)}] wine {wine_id}: {n} reviews")


def cmd_run(args: argparse.Namespace) -> None:
    client = VivinoClient()
    writer = RawWriter(args.data_dir, make_run_id())

    # (a) region metadata — reference coverage numbers (Brda: 134 wineries / 1358 wines)
    endpoint = f"/api/regions/{args.region_id}"
    status, _ = fetch_and_record(client, writer, "regions", endpoint, meta={"region_id": args.region_id})
    if status != 200:
        raise FetchAbort(f"unexpected http {status} on {endpoint}")
    log(f"[region {args.region_id}] ok")

    # (b) explore sweep — vintage-grain hits, seeds the winery set
    winery_ids: dict[int, str] = {}
    page, seen = 1, 0
    while True:
        endpoint = "/api/explore/explore"
        params = {
            "country_codes[]": args.country_code,
            "region_ids[]": args.region_id,
            "page": page,
        }
        status, payload = fetch_and_record(client, writer, "explore", endpoint, query=params)
        if status != 200:
            raise FetchAbort(f"unexpected http {status} on {endpoint} page {page}")
        ev = strict_get(payload, "explore_vintage", f"{endpoint} page {page}")
        matches = ev.get("matches")
        total = ev.get("records_matched")
        if not isinstance(matches, list) or not isinstance(total, int):
            raise FetchAbort(f"shape drift at {endpoint} page {page}: matches/records_matched")
        seen += len(matches)
        for m in matches:
            vintage = strict_get(m, "vintage", "explore match")
            winery = strict_get(strict_get(vintage, "wine", "explore vintage"), "winery", "explore wine")
            winery_ids[strict_get(winery, "id", "explore winery")] = winery.get("name", "?")
        log(f"[explore] page {page}: {len(matches)} matches ({seen}/{total}), {len(winery_ids)} wineries so far")
        if seen >= total or args.limit:  # smoke test: one page validates the shape
            break
        if not matches:
            raise FetchAbort(f"explore page {page} empty with only {seen}/{total} matches seen")
        page += 1
        if page > EXPLORE_PAGE_CAP:
            raise FetchAbort(f"explore exceeded {EXPLORE_PAGE_CAP} pages with {seen}/{total} matches")

    if not winery_ids:
        raise FetchAbort("explore sweep seeded 0 wineries — nothing to fetch")

    wineries = sorted(winery_ids)
    if args.limit:
        wineries = wineries[: args.limit]
    log(f"[plan] {len(wineries)} wineries to fetch: {', '.join(winery_ids[w] for w in wineries)}")

    # (c)+(d) full catalog + per-vintage stats per winery
    all_wine_ids: list[int] = []
    seen_wines: set[int] = set()
    for wid in wineries:
        for wine_id in fetch_winery(client, writer, wid, winery_ids[wid]):
            if wine_id not in seen_wines:
                seen_wines.add(wine_id)
                all_wine_ids.append(wine_id)

    # (e) first review page per catalog wine
    if args.limit:
        all_wine_ids = all_wine_ids[: args.limit]
    fetch_reviews(client, writer, all_wine_ids)

    log(f"[done] {len(wineries)} wineries, {len(all_wine_ids)} wines with reviews -> {writer.root}")


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> None:
    parser = argparse.ArgumentParser(prog="vinatlas-fetch", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="full sweep: region -> explore -> catalogs -> vintages -> reviews")
    p_run.add_argument("--region-id", type=int, default=734, help="Vivino region (734 = Goriška Brda)")
    p_run.add_argument("--country-code", default="si", help="origin country for explore (country_codes[])")
    p_run.add_argument("--data-dir", type=Path, default=Path("data"))
    p_run.add_argument("--limit", type=int, default=None, help="smoke test: cap wineries/wines, 1 explore page")
    p_run.set_defaults(func=cmd_run)

    argv = sys.argv[1:]
    if not argv or argv[0] not in {"run", "-h", "--help"}:
        argv = ["run", *argv]  # default subcommand
    args = parser.parse_args(argv)

    try:
        args.func(args)
    except FetchAbort as exc:
        log(f"[abort] {exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()