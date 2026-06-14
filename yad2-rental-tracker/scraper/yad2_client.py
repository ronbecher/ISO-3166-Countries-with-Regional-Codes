"""
yad2_client.py — fetch + parse Yad2 rental listings.

Primary path: Yad2 internal JSON feed (gw.yad2.co.il/realestate-feed/...) via httpx.
Fallback path: Playwright (headless Chromium) that renders the search page and
extracts the embedded Next.js JSON, used ONLY when the JSON path is blocked.

IMPORTANT — empirical IDs:
  Yad2 has no public API. The feed endpoint, query-parameter names, and the
  internal numeric IDs for cities / neighborhoods / property types are NOT
  guessed in code. City & neighborhood IDs are resolved at runtime from Yad2's
  address-autocomplete endpoint by Hebrew name. Property-type codes and the feed
  URL live in `yad2_mappings.json` so they can be corrected without touching
  code. See the README ("Resolving Yad2 IDs") before trusting any run.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import httpx

log = logging.getLogger("yad2_client")

HERE = Path(__file__).resolve().parent

# Politeness / safety knobs. Keep request volume minimal (see brief §13).
DELAY_BETWEEN_QUERIES = (1.5, 4.0)   # randomized seconds between target queries
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0             # seconds: 2, 4, 8 ...
MAX_PAGES_PER_QUERY = 5             # hard cap on pagination per (target, hood)
HARD_REQUEST_CAP = 60               # absolute ceiling on HTTP requests per run
REQUEST_TIMEOUT = 25.0

# A large sentinel for "no upper bound" range filters.
_BIG_SQM = 100000


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_config(path: str | os.PathLike | None = None) -> dict:
    return _load_json(Path(path) if path else HERE / "config.json")


def load_mappings(path: str | os.PathLike | None = None) -> dict:
    return _load_json(Path(path) if path else HERE / "yad2_mappings.json")


@dataclass
class Listing:
    """Flat, normalized record — mirrors the Supabase `listings` table (brief §6)."""

    id: str
    title: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    address: str | None = None
    property_type: str | None = None
    rooms: float | None = None
    size_sqm: float | None = None
    floor: int | None = None
    has_parking: bool | None = None
    price: int | None = None
    url: str | None = None
    image_url: str | None = None
    # Bookkeeping populated by the client, not stored verbatim:
    _target_city: str | None = field(default=None, repr=False)

    def to_record(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_target_city", None)
        return d


def _realistic_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.yad2.co.il/realestate/rent",
        "Origin": "https://www.yad2.co.il",
        "Connection": "keep-alive",
    }


# --------------------------------------------------------------------------- #
# Safe extraction helpers — the feed shape is not contractual, so be defensive.
# --------------------------------------------------------------------------- #
def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def _dig(d: Any, *path: str) -> Any:
    cur = d
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _to_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(str(v).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


class Yad2Blocked(RuntimeError):
    """Raised when the JSON path returns an anti-bot block/challenge."""


class Yad2Client:
    def __init__(self, config: dict | None = None, mappings: dict | None = None):
        self.config = config or load_config()
        self.mappings = mappings or load_mappings()
        self.feed_url: str = self.mappings["feed_url"]
        self.autocomplete_url: str = self.mappings["autocomplete_url"]
        self._request_count = 0
        self._location_cache: dict[str, dict] = {}
        self._client = httpx.Client(
            headers=_realistic_headers(),
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    # -- lifecycle ---------------------------------------------------------- #
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Yad2Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level GET with retry/backoff + block detection ----------------- #
    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        if self._request_count >= HARD_REQUEST_CAP:
            raise RuntimeError(f"Hard request cap ({HARD_REQUEST_CAP}) reached this run")
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._request_count += 1
            try:
                resp = self._client.get(url, params=params)
                if resp.status_code in (403, 429) or self._looks_blocked(resp):
                    raise Yad2Blocked(f"blocked: HTTP {resp.status_code} @ {url}")
                resp.raise_for_status()
                return resp
            except (httpx.HTTPError, Yad2Blocked) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    backoff = RETRY_BACKOFF_BASE ** attempt + random.uniform(0, 1)
                    log.warning("GET failed (%s), retry %d/%d in %.1fs",
                                exc, attempt, MAX_RETRIES, backoff)
                    time.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _looks_blocked(resp: httpx.Response) -> bool:
        ctype = resp.headers.get("content-type", "")
        if "text/html" in ctype:
            body = resp.text[:2000].lower()
            return any(s in body for s in ("captcha", "are you a robot", "shieldsquare", "px-captcha"))
        return False

    # -- location resolution (empirical, via autocomplete) ------------------ #
    def resolve_location(self, text: str) -> dict:
        """Resolve a Hebrew city/neighborhood name to Yad2 internal IDs.

        Returns a dict that may contain: city_id, area_id, top_area_id, hood_id.
        Cached per query string. Raises if nothing resolves.
        """
        if text in self._location_cache:
            return self._location_cache[text]
        resp = self._get(self.autocomplete_url, params={"text": text})
        data = resp.json()
        result = self._parse_autocomplete(data, text)
        if not result:
            raise ValueError(f"Could not resolve Yad2 location id for '{text}'")
        self._location_cache[text] = result
        return result

    @staticmethod
    def _parse_autocomplete(data: Any, text: str) -> dict | None:
        """Pull the best-matching city/neighborhood ids out of the autocomplete payload."""
        cities = _first(data, "cities", default=[]) or []
        hoods = _first(data, "hoods", "neighborhoods", default=[]) or []
        areas = _first(data, "areas", default=[]) or []
        top_areas = _first(data, "topAreas", default=[]) or []

        out: dict[str, Any] = {}

        # Neighborhood match takes precedence when the query names a hood.
        for h in hoods:
            title = _first(h, "fullTitleText", "title", "text", default="")
            if text in str(title):
                out["hood_id"] = _to_int(_first(h, "hoodId", "neighborhoodId", "id"))
                out["city_id"] = _to_int(_first(h, "cityId", "city_id"))
                out["area_id"] = _to_int(_first(h, "areaId", "area_id"))
                out["top_area_id"] = _to_int(_first(h, "topAreaId", "top_area_id"))
                break

        if "city_id" not in out:
            for c in cities:
                title = _first(c, "fullTitleText", "title", "text", default="")
                if text in str(title) or str(title) in text:
                    out["city_id"] = _to_int(_first(c, "cityId", "city_id", "id"))
                    out["area_id"] = _to_int(_first(c, "areaId", "area_id"))
                    out["top_area_id"] = _to_int(_first(c, "topAreaId", "top_area_id"))
                    break

        if not out and (areas or top_areas):
            cand = (areas or top_areas)[0]
            out["area_id"] = _to_int(_first(cand, "areaId", "id"))

        return out or None

    # -- query building ----------------------------------------------------- #
    def _property_codes(self) -> str:
        wanted = self.config["filters"]["property_types"]
        codes = self.mappings["property_type_codes"]
        ids = []
        for name in wanted:
            code = codes.get(name)
            if code is None:
                log.warning("No Yad2 code mapped for property_type '%s' — skipping", name)
                continue
            ids.append(str(code))
        # de-dupe while preserving order (private_house & cottage share a code)
        seen: set[str] = set()
        return ",".join(x for x in ids if not (x in seen or seen.add(x)))

    def _build_params(self, loc: dict, page: int) -> dict[str, Any]:
        f = self.config["filters"]
        params: dict[str, Any] = {"page": page}

        if loc.get("city_id"):
            params["city"] = loc["city_id"]
        if loc.get("area_id"):
            params["area"] = loc["area_id"]
        if loc.get("top_area_id"):
            params["topArea"] = loc["top_area_id"]
        if loc.get("hood_id"):
            params["neighborhood"] = loc["hood_id"]

        if f.get("price_max") is not None:
            params["price"] = f"0-{f['price_max']}"
        if f.get("rooms_min") is not None or f.get("rooms_max") is not None:
            lo = f.get("rooms_min") or 1
            hi = f.get("rooms_max") or 99
            params["rooms"] = f"{lo}-{hi}"
        if f.get("size_min_sqm") is not None:
            params["squaremeter"] = f"{f['size_min_sqm']}-{_BIG_SQM}"
        if f.get("parking_required"):
            params["parking"] = 1
        if f.get("floor") is not None:
            params["floor"] = f["floor"]

        codes = self._property_codes()
        if codes:
            params["property"] = codes
        return params

    # -- normalization ------------------------------------------------------ #
    def _normalize(self, item: dict, target_city: str) -> Listing | None:
        listing_id = _first(
            item, "id", "orderId", "token", "adNumber", "linkToken", "feedId"
        )
        if listing_id is None:
            return None
        listing_id = str(listing_id)

        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        # Yad2 frequently nests location under address.{city,neighborhood,...}
        city = (
            _dig(address, "city", "text")
            or _first(address, "city")
            or _first(item, "city", "city_name")
            or target_city
        )
        neighborhood = (
            _dig(address, "neighborhood", "text")
            or _first(address, "neighborhood", "neighbourhood")
            or _first(item, "neighborhood", "neighborhood_name")
        )
        street = _dig(address, "street", "text") or _first(item, "street")
        house = _dig(address, "house", "number") or _first(item, "house_number")
        addr_str = " ".join(str(x) for x in (street, house) if x) or None

        additional = item.get("additionalDetails") if isinstance(item.get("additionalDetails"), dict) else {}
        rooms = _to_float(
            _first(additional, "roomsCount", "rooms")
            or _first(item, "rooms", "Rooms_text", "rooms_count")
        )
        size = _to_float(
            _first(additional, "squareMeter", "square_meters")
            or _first(item, "square_meters", "squareMeter", "size")
        )
        floor = _to_int(
            _dig(address, "house", "floor")
            or _first(additional, "floor")
            or _first(item, "floor")
        )

        ptype = (
            _first(additional, "propertyType", "property_type")
            or _dig(item, "additionalDetails", "property", "text")
            or _first(item, "property_type", "propertyType")
        )

        price = _to_int(_first(item, "price", "Price") or _dig(item, "price", "amount"))

        image_url = None
        meta = item.get("metaData") if isinstance(item.get("metaData"), dict) else {}
        imgs = _first(meta, "images", "coverImage") or _first(item, "images", "img_url")
        if isinstance(imgs, list) and imgs:
            image_url = imgs[0] if isinstance(imgs[0], str) else _first(imgs[0], "src", "url")
        elif isinstance(imgs, str):
            image_url = imgs

        token = _first(item, "token", "linkToken", "orderId") or listing_id
        url = f"https://www.yad2.co.il/realestate/item/{token}"

        parking = self._infer_parking(item, additional)

        title = _first(item, "title", "title_1") or (
            f"{ptype or ''} {addr_str or neighborhood or city or ''}".strip() or None
        )

        return Listing(
            id=listing_id,
            title=title,
            city=str(city) if city else None,
            neighborhood=str(neighborhood) if neighborhood else None,
            address=addr_str,
            property_type=str(ptype) if ptype else None,
            rooms=rooms,
            size_sqm=size,
            floor=floor,
            has_parking=parking,
            price=price,
            url=url,
            image_url=image_url,
            _target_city=target_city,
        )

    @staticmethod
    def _infer_parking(item: dict, additional: dict) -> bool | None:
        for src in (additional, item):
            val = _first(src, "parking", "parkingSpaces", "parking_spaces", "hasParking")
            if val is None:
                continue
            if isinstance(val, bool):
                return val
            n = _to_int(val)
            if n is not None:
                return n > 0
        return None

    # -- feed parsing ------------------------------------------------------- #
    @staticmethod
    def _extract_items(payload: Any) -> list[dict]:
        """Find the list of listing dicts inside a feed response of unknown shape."""
        for path in (
            ("data", "markers"),
            ("data", "feed_items"),
            ("data", "feed", "feed_items"),
            ("data", "items"),
            ("markers",),
            ("feed_items",),
            ("results",),
        ):
            node = _dig(payload, *path) if len(path) > 1 else payload.get(path[0]) if isinstance(payload, dict) else None
            if isinstance(node, list) and node:
                return [x for x in node if isinstance(x, dict)]
        # last resort: any top-level list of dicts
        if isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
        return []

    @staticmethod
    def _total_pages(payload: Any) -> int:
        for path in (("data", "pagination", "last_page"), ("pagination", "last_page"),
                     ("data", "pagination", "pages"), ("data", "total_pages")):
            v = _to_int(_dig(payload, *path))
            if v:
                return v
        return 1

    # -- per-target fetch --------------------------------------------------- #
    def fetch_target(self, target: dict) -> tuple[list[Listing], bool]:
        """Fetch all listings for one target (city + optional neighborhoods).

        Returns (listings, ok). `ok` is False if every query for this target
        errored or was blocked — the caller uses it to skip the removal step
        (brief §7 failed-fetch guard).
        """
        city = target["city"]
        hoods = target.get("neighborhoods") or []
        # One query per (city) or per (city, neighborhood). City-level when no hoods.
        queries: list[tuple[str, str | None]] = (
            [(city, h) for h in hoods] if hoods else [(city, None)]
        )

        collected: dict[str, Listing] = {}
        any_ok = False
        for city_name, hood_name in queries:
            try:
                loc = self.resolve_location(hood_name or city_name)
                # Ensure we always carry the city scope even for a hood query.
                if hood_name and "city_id" not in loc:
                    loc = {**self.resolve_location(city_name), **loc}
                listings = self._fetch_query(loc, city_name)
                for lst in listings:
                    collected[lst.id] = lst
                any_ok = True
            except Exception as exc:  # noqa: BLE001 — one bad query must not kill the run
                log.error("Query failed for %s / %s: %s", city_name, hood_name, exc)
            time.sleep(random.uniform(*DELAY_BETWEEN_QUERIES))
        return list(collected.values()), any_ok

    def _fetch_query(self, loc: dict, city_name: str) -> list[Listing]:
        out: list[Listing] = []
        page = 1
        while page <= MAX_PAGES_PER_QUERY:
            params = self._build_params(loc, page)
            try:
                resp = self._get(self.feed_url, params=params)
                payload = resp.json()
            except Yad2Blocked:
                log.warning("JSON path blocked — invoking Playwright fallback")
                payload = self._playwright_fallback(params)
            items = self._extract_items(payload)
            for it in items:
                norm = self._normalize(it, city_name)
                if norm:
                    out.append(norm)
            if page >= self._total_pages(payload) or not items:
                break
            page += 1
        return out

    # -- Playwright fallback ------------------------------------------------ #
    def _playwright_fallback(self, params: dict) -> dict:
        """Render the search page headlessly and pull the embedded JSON.

        Imported lazily so the JSON-only happy path doesn't require a browser.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise Yad2Blocked("JSON blocked and Playwright not installed") from exc

        query = "&".join(f"{k}={v}" for k, v in params.items())
        page_url = f"https://www.yad2.co.il/realestate/rent?{query}"
        log.info("Playwright fallback fetching %s", page_url)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=_realistic_headers()["User-Agent"],
                locale="he-IL",
            )
            page = ctx.new_page()
            page.goto(page_url, wait_until="networkidle", timeout=45000)
            content = page.content()
            data = self._extract_next_data(content)
            browser.close()
        if data is None:
            raise Yad2Blocked("Playwright fallback could not extract embedded JSON")
        return data

    @staticmethod
    def _extract_next_data(html: str) -> dict | None:
        marker = 'id="__NEXT_DATA__"'
        idx = html.find(marker)
        if idx == -1:
            return None
        start = html.find(">", idx) + 1
        end = html.find("</script>", start)
        if start <= 0 or end == -1:
            return None
        try:
            blob = json.loads(html[start:end])
        except json.JSONDecodeError:
            return None
        # The feed usually hangs off props.pageProps.* — search broadly.
        return _dig(blob, "props", "pageProps") or blob

    # -- public entrypoint -------------------------------------------------- #
    def fetch_all(self) -> tuple[dict[str, list[Listing]], dict[str, bool]]:
        """Fetch every target. Returns:
        - per_target: {target_city: [Listing, ...]}  (deduped within target)
        - target_ok:  {target_city: bool}            (False => skip removals)
        Note: results are NOT merged across targets, because the removal step in
        sync.py operates per-target scope (brief §7).
        """
        per_target: dict[str, list[Listing]] = {}
        target_ok: dict[str, bool] = {}
        for target in self.config["targets"]:
            city = target["city"]
            listings, ok = self.fetch_target(target)
            per_target[city] = listings
            target_ok[city] = ok
            log.info("Target %s: %d listings (ok=%s)", city, len(listings), ok)
        return per_target, target_ok


def dry_run() -> None:
    """Print results without touching the DB or Telegram (brief §12 step 2)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with Yad2Client() as client:
        per_target, ok = client.fetch_all()
    total = 0
    for city, listings in per_target.items():
        print(f"\n=== {city} (ok={ok[city]}) — {len(listings)} listings ===")
        for l in listings:
            total += 1
            print(f"  [{l.id}] {l.price}₪ | {l.rooms} rooms | {l.size_sqm}sqm | "
                  f"{l.neighborhood or ''} | parking={l.has_parking} | {l.url}")
    print(f"\nTOTAL: {total} listings across {len(per_target)} targets")


if __name__ == "__main__":
    dry_run()
