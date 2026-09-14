"""Schema-agnostic extraction of per-size availability from a product page.

Why not a hard-coded selector or API path: Hollister (Abercrombie & Fitch's
platform) renders the product page from an embedded JSON state and also exposes
that state through internal endpoints, both of which change without notice. So
instead of pinning one shape, this module:

1. collects every JSON document it can find in the response — the body itself
   if it is JSON, `<script type="application/json">` / `application/ld+json`
   blocks, and `window.__INITIAL_STATE__`-style assignments;
2. walks them looking for objects that carry *both* something that reads like a
   size (``"XS"``, ``"X-Small"``, ``"Taille S"``) *and* something that reads
   like a stock signal (``inStock``, ``availability``, ``quantity``, ...);
3. falls back to reading the size buttons out of the raw HTML when no JSON
   yields anything.

`python -m stockwatch diagnose` prints exactly what each step found, which is
how you verify the parse against the live page.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Size normalisation
# --------------------------------------------------------------------------

# Canonical alias -> canonical size. Keys are compared after stripping accents,
# spaces, dots and hyphens and upper-casing ("x-small" -> "XSMALL").
_SIZE_ALIASES: dict[str, str] = {
    "XXS": "XXS", "XXSMALL": "XXS", "2XS": "XXS",
    "XS": "XS", "XSMALL": "XS", "EXTRASMALL": "XS", "TXS": "XS",
    "S": "S", "SMALL": "S", "PETIT": "S",
    "M": "M", "MEDIUM": "M", "MOYEN": "M",
    "L": "L", "LARGE": "L", "GRAND": "L",
    "XL": "XL", "XLARGE": "XL", "EXTRALARGE": "XL",
    "XXL": "XXL", "XXLARGE": "XXL", "2XL": "XXL",
    "XXXL": "XXXL", "3XL": "XXXL", "XXXLARGE": "XXXL",
}

_STRIP_PREFIXES = ("TAILLE", "SIZE", "TALLA", "GROSSE", "TAGLIA")


def _fold(value: str) -> str:
    """Upper-case, accent-free, punctuation-free form of `value`."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9]+", "", text).upper()


def normalize_size(raw: Any) -> str | None:
    """Return the canonical size (``"XS"``) for `raw`, or None if it is not a
    size label. Accepts ``"xs"``, ``"X-Small"``, ``"Taille XS"``, ``" S "``."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > 24:
        return None
    folded = _fold(candidate)
    for prefix in _STRIP_PREFIXES:
        if folded.startswith(prefix) and len(folded) > len(prefix):
            folded = folded[len(prefix):]
            break
    return _SIZE_ALIASES.get(folded)


# --------------------------------------------------------------------------
# Availability signals
# --------------------------------------------------------------------------

def _key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


# Keys whose *value* may hold the size label.
_SIZE_KEYS = {
    "size", "sizename", "sizelabel", "sizedescription", "sizecode", "sizevalue",
    "shortdescription", "displayname", "displayvalue", "label", "value", "name",
    "title", "text", "optionvalue", "variantvalue", "description", "dimensionvalue",
}

# Boolean-ish keys where True means "you can buy it".
_POSITIVE_BOOL_KEYS = {
    "instock", "isinstock", "instockflag", "available", "isavailable",
    "availableforsale", "purchasable", "ispurchasable", "orderable", "isorderable",
    "sellable", "issellable", "selectable", "isselectable", "inventoryavailable",
}

# Boolean-ish keys where True means the opposite.
_NEGATIVE_BOOL_KEYS = {
    "soldout", "issoldout", "outofstock", "isoutofstock", "disabled", "isdisabled",
    "unavailable", "isunavailable", "notavailable", "oos",
}

# Numeric keys: > 0 means in stock.
_QUANTITY_KEYS = {
    "quantity", "availablequantity", "availablecount", "inventory", "inventorylevel",
    "inventoryquantity", "stock", "stocklevel", "stockquantity", "atsquantity", "qty",
}

# Free-text status keys ("IN_STOCK", "https://schema.org/OutOfStock", ...).
_STATUS_KEYS = {
    "availability", "availabilitystatus", "availabilitystate", "inventorystatus",
    "stockstatus", "stocklevelstatus", "status", "state", "inventorymessage",
    "availabilitymessage", "inventorystate",
}

_TRUE_WORDS = {"true", "yes", "y", "1", "in stock", "instock", "available"}
_FALSE_WORDS = {"false", "no", "n", "0", "out of stock", "outofstock", "unavailable"}

# Checked in order: negative markers first, because "unavailable" and
# "notavailable" both contain "available".
_NEGATIVE_STATUS_MARKERS = (
    "outofstock", "soldout", "notavailable", "unavailable", "nostock", "nolongeravailable",
    "discontinued", "comingsoon", "backorder", "preorder", "instoreonly", "notifyme",
    "limitedavailability",
)
_POSITIVE_STATUS_MARKERS = ("instock", "lowstock", "limitedstock", "available", "instore")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value > 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    return None


def status_to_bool(value: Any) -> bool | None:
    """Map a free-text stock status to a boolean, or None when unrecognised."""
    if not isinstance(value, str):
        return None
    folded = _fold(value)
    if not folded:
        return None
    for marker in _NEGATIVE_STATUS_MARKERS:
        if marker.upper() in folded:
            return False
    for marker in _POSITIVE_STATUS_MARKERS:
        if marker.upper() in folded:
            return True
    return _as_bool(value)


def availability_from_mapping(node: dict[str, Any]) -> bool | None:
    """Read every stock signal carried by `node`.

    Signals must agree: a variant flagged ``inStock: true`` but ``quantity: 0``
    is reported out of stock. Returns None when the node carries no signal.
    """
    votes: list[bool] = []
    for raw_key, value in node.items():
        key = _key(raw_key)
        if key in _NEGATIVE_BOOL_KEYS:
            flag = _as_bool(value)
            if flag is not None:
                votes.append(not flag)
        elif key in _POSITIVE_BOOL_KEYS:
            flag = _as_bool(value)
            if flag is not None:
                votes.append(flag)
        elif key in _QUANTITY_KEYS and isinstance(value, int | float) and not isinstance(value, bool):
            votes.append(value > 0)
        elif key in _STATUS_KEYS:
            flag = status_to_bool(value)
            if flag is not None:
                votes.append(flag)
    if not votes:
        return None
    return all(votes)


def size_from_mapping(node: dict[str, Any]) -> str | None:
    """Return the size this node describes, if any."""
    for raw_key, value in node.items():
        if _key(raw_key) in _SIZE_KEYS:
            size = normalize_size(value)
            if size:
                return size
    return None


# --------------------------------------------------------------------------
# JSON discovery inside an HTML body
# --------------------------------------------------------------------------

_SCRIPT_JSON_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"'](?:application/json|application/ld\+json)[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

_ASSIGNMENT_RE = re.compile(
    r"(?:window\.)?(?:__INITIAL_STATE__|__PRELOADED_STATE__|__NEXT_DATA__|__NUXT__|"
    r"__APOLLO_STATE__|__STATE__|digitalData|dataLayer|productData|initialState)"
    r"\s*=\s*",
    re.IGNORECASE,
)

_MAX_BLOBS = 40


def _scan_json_value(text: str, start: int) -> str | None:
    """Return the JSON object/array literal starting at `start`, string-aware."""
    if start >= len(text) or text[start] not in "{[":
        return None
    openers = {"{": "}", "[": "]"}
    stack = [openers[text[start]]]
    in_string = False
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in openers:
            stack.append(openers[char])
        elif char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return text[start : index + 1]
    return None


def iter_json_blobs(body: str) -> Iterator[Any]:
    """Yield every JSON document embedded in `body` (or the body itself)."""
    stripped = body.lstrip()
    if stripped[:1] in "{[":
        try:
            yield json.loads(stripped)
            return
        except json.JSONDecodeError:
            pass

    seen = 0
    for match in _SCRIPT_JSON_RE.finditer(body):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            # Some blocks are wrapped in a JS comment or padded with HTML
            # comments; retry on the first balanced literal inside.
            index = min((raw.find(ch) for ch in "{[" if raw.find(ch) != -1), default=-1)
            if index == -1:
                continue
            literal = _scan_json_value(raw, index)
            if literal:
                try:
                    yield json.loads(literal)
                except json.JSONDecodeError:
                    continue
        seen += 1
        if seen >= _MAX_BLOBS:
            return

    for match in _ASSIGNMENT_RE.finditer(body):
        index = match.end()
        while index < len(body) and body[index] in " \t\r\n":
            index += 1
        literal = _scan_json_value(body, index)
        if not literal:
            continue
        try:
            yield json.loads(literal)
        except json.JSONDecodeError:
            continue
        seen += 1
        if seen >= _MAX_BLOBS:
            return


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SizeObservation:
    """One (size, availability) pair found somewhere in the page."""

    size: str
    available: bool
    source: str
    detail: str = ""
    focused: bool = False


@dataclass
class ParseResult:
    sizes: dict[str, bool] = field(default_factory=dict)
    observations: list[SizeObservation] = field(default_factory=list)
    strategy: str = "none"

    @property
    def found(self) -> bool:
        return bool(self.sizes)

    def available_sizes(self) -> list[str]:
        return sorted(size for size, ok in self.sizes.items() if ok)


_ID_KEYS = {"productid", "id", "sku", "productcode", "masterproductid", "parentid", "collectionid"}
_MAX_DEPTH = 24


def product_id_from_url(url: str) -> str | None:
    """`.../p/icon-henley-63586319-2?x=1` -> `63586319`."""
    path = url.split("?", 1)[0].rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    matches = re.findall(r"\d{5,}", slug)
    return matches[-1] if matches else None


def _walk(node: Any, product_id: str | None, focused: bool, depth: int, out: list[SizeObservation]) -> None:
    if depth > _MAX_DEPTH or len(out) > 500:
        return
    if isinstance(node, dict):
        here = focused
        if product_id and not here:
            for raw_key, value in node.items():
                if _key(raw_key) in _ID_KEYS and isinstance(value, str | int) and str(value) == product_id:
                    here = True
                    break
        size = size_from_mapping(node)
        available = availability_from_mapping(node)
        if size and available is not None:
            out.append(
                SizeObservation(
                    size=size,
                    available=available,
                    source="json",
                    detail=_short_repr(node),
                    focused=here,
                )
            )
        for value in node.values():
            _walk(value, product_id, here, depth + 1, out)
    elif isinstance(node, list):
        for value in node:
            _walk(value, product_id, focused, depth + 1, out)


def _short_repr(node: dict[str, Any]) -> str:
    keep = {k: v for k, v in node.items() if not isinstance(v, dict | list)}
    text = json.dumps(keep, ensure_ascii=False, default=str)
    return text[:220]


# --------------------------------------------------------------------------
# HTML fallback
# --------------------------------------------------------------------------

_TAG_RE = re.compile(
    r"<(?P<tag>button|a|li|label|span|div|input|option)\b(?P<attrs>[^>]*)>(?P<text>[^<]{0,60})",
    re.IGNORECASE,
)
_ATTR_RE = re.compile(r"""([a-zA-Z0-9_:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")

_UNAVAILABLE_MARKERS = (
    "sold-out", "soldout", "out-of-stock", "outofstock", "oos", "unavailable",
    "not-available", "notify-me", "notifyme", "is-disabled", "disabled", "strikethrough",
)
_SIZE_ATTRS = ("aria-label", "data-size", "data-value", "data-sizecode", "title", "value", "alt")


def parse_html_size_buttons(html: str) -> list[SizeObservation]:
    """Last-resort heuristic: read the size selector out of the markup.

    A size element that renders without a `disabled` attribute and without a
    sold-out class is treated as available.
    """
    observations: list[SizeObservation] = []
    for match in _TAG_RE.finditer(html):
        attrs_raw = match.group("attrs") or ""
        attrs = {
            key.lower(): (dq if dq is not None else sq) or ""
            for key, dq, sq in _ATTR_RE.findall(attrs_raw)
        }
        size = normalize_size(match.group("text"))
        if not size:
            for name in _SIZE_ATTRS:
                size = normalize_size(attrs.get(name, ""))
                if size:
                    break
        if not size:
            continue

        lowered = attrs_raw.lower()
        available: bool | None = None
        for name in ("data-instock", "data-in-stock", "data-available", "data-availability"):
            if name in attrs:
                available = status_to_bool(attrs[name])
                if available is not None:
                    break
        if available is None:
            disabled = re.search(r"(?<![\w-])disabled(?![\w-])", lowered) is not None
            disabled = disabled or attrs.get("aria-disabled", "").lower() == "true"
            disabled = disabled or any(marker in lowered for marker in _UNAVAILABLE_MARKERS)
            available = not disabled

        observations.append(
            SizeObservation(
                size=size,
                available=available,
                source="html",
                detail=attrs_raw.strip()[:220],
            )
        )
    return observations


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def merge_observations(observations: list[SizeObservation]) -> dict[str, bool]:
    """Collapse observations to one verdict per size.

    A size counts as available when *any* observation says so: missing a
    restock is worse than one noisy alert, and the same variant is often
    described several times (grid, selector, structured data).
    """
    merged: dict[str, bool] = {}
    for observation in observations:
        merged[observation.size] = merged.get(observation.size, False) or observation.available
    return merged


def parse_availability(body: str, *, product_id: str | None = None, html_fallback: bool = True) -> ParseResult:
    """Extract `{size: available}` from a product page or API response."""
    if not body:
        return ParseResult()

    observations: list[SizeObservation] = []
    for blob in iter_json_blobs(body):
        _walk(blob, product_id, False, 0, observations)

    if observations:
        focused = [observation for observation in observations if observation.focused]
        chosen = focused or observations
        return ParseResult(
            sizes=merge_observations(chosen),
            observations=chosen,
            strategy="json-focused" if focused else "json",
        )

    if html_fallback:
        html_observations = parse_html_size_buttons(body)
        if html_observations:
            return ParseResult(
                sizes=merge_observations(html_observations),
                observations=html_observations,
                strategy="html-heuristic",
            )

    return ParseResult()
