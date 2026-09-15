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

# Une fiche affiche « Couleur : Blanc » mais nomme ses données « white » : le
# libellé visible est traduit, pas la donnée. On rapproche donc les deux.
_COLOUR_WORDS = {
    "BLANC": "WHITE", "BLANCHE": "WHITE", "ECRU": "WHITE", "IVOIRE": "IVORY",
    "NOIR": "BLACK", "NOIRE": "BLACK",
    "GRIS": "GREY", "GRISE": "GREY", "GRAY": "GREY", "ANTHRACITE": "CHARCOAL",
    "BLEU": "BLUE", "BLEUE": "BLUE", "MARINE": "NAVY", "CIEL": "SKY",
    "VERT": "GREEN", "VERTE": "GREEN", "KAKI": "KHAKI", "OLIVE": "OLIVE",
    "ROUGE": "RED", "BORDEAUX": "BURGUNDY",
    "ROSE": "PINK", "FUCHSIA": "FUCHSIA", "CORAIL": "CORAL",
    "JAUNE": "YELLOW", "ORANGE": "ORANGE", "VIOLET": "PURPLE", "MAUVE": "PURPLE",
    "MARRON": "BROWN", "BRUN": "BROWN", "CHOCOLAT": "CHOCOLATE", "CAMEL": "CAMEL",
    "BEIGE": "BEIGE", "CREME": "CREAM", "SABLE": "SAND", "TAUPE": "TAUPE",
    "CLAIR": "LIGHT", "FONCE": "DARK", "FONCEE": "DARK", "PALE": "PALE",
    "CHINE": "HEATHER", "CHINEE": "HEATHER", "DELAVE": "WASHED",
}


def _fold(value: str) -> str:
    """Upper-case, accent-free, punctuation-free form of `value`."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9]+", "", text).upper()


def normalize_size(raw: Any) -> str | None:
    """Return the canonical size (``"XS"``) for `raw`, or None if it is not a
    size label.

    Accepts ``"xs"``, ``"X-Small"``, ``"Taille XS"``, ``" S "``, and the
    dimension-suffixed forms real catalogues use — Hollister ships
    ``"sizePrimary": "L_p"``, others ``"M/32"``.
    """
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
    size = _SIZE_ALIASES.get(folded)
    if size:
        return size
    for separator in ("_", "/", "|"):
        head, found, _ = candidate.partition(separator)
        if found and head.strip():
            size = _SIZE_ALIASES.get(_fold(head))
            if size:
                return size
    return None


# --------------------------------------------------------------------------
# Availability signals
# --------------------------------------------------------------------------

def _key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


# Keys whose *value* may hold the size label.
_SIZE_KEYS = {
    "size", "sizename", "sizelabel", "sizedescription", "sizecode", "sizevalue",
    # Hollister / Abercrombie name their two size dimensions this way.
    "sizeprimary", "sizesecondary", "primarysize", "secondarysize",
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

# Any assignment onto the global object, whatever the property is called.
# Hollister ships its Apollo cache as
#     window['APOLLO_STATE__product-mfe-web-service-ProductPageFrontend-config'] = {…}
# — a bracket assignment with a service-specific key, so no fixed name matches.
_GLOBAL_ASSIGNMENT_RE = re.compile(
    r"""(?:window|self|globalThis)\s*
        (?:\.\s*[A-Za-z_$][\w$]*
          |\[\s*(?P<quote>['"])[^'"\n]{1,200}(?P=quote)\s*\]
        )\s*=\s*""",
    re.VERBOSE,
)

_MAX_BLOBS = 40
# A single state blob can weigh several hundred kilobytes; beyond this it is not
# a page state any more and scanning it would blow the one-second budget.
_MAX_LITERAL_BYTES = 8_000_000


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

    starts: list[int] = []
    for pattern in (_ASSIGNMENT_RE, _GLOBAL_ASSIGNMENT_RE):
        for match in pattern.finditer(body):
            index = match.end()
            while index < len(body) and body[index] in " \t\r\n":
                index += 1
            starts.append(index)

    for index in sorted(set(starts)):
        literal = _scan_json_value(body, index)
        if not literal or len(literal) > _MAX_LITERAL_BYTES:
            continue
        try:
            yield json.loads(literal)
        except json.JSONDecodeError:
            # Plain JS object literals (unquoted keys, trailing commas) are not
            # JSON; skipping them costs nothing since the state blobs are.
            continue
        seen += 1
        if seen >= _MAX_BLOBS:
            return


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SizeObservation:
    """One (size, availability) pair found somewhere in the page.

    `confident` is False for a guess rather than a reading — a size button
    present in the markup with no stock state attached. Such an observation is
    reported by `diagnose` but never drives an alert: on a page whose stock is
    rendered in JavaScript, every size looks "not disabled" and the bot would
    announce a restock that never happened.
    """

    size: str
    available: bool
    source: str
    detail: str = ""
    focused: bool = False
    confident: bool = True
    owner: str | None = None   # product id the reading sits under, when the page says


@dataclass
class ParseResult:
    sizes: dict[str, bool] = field(default_factory=dict)
    observations: list[SizeObservation] = field(default_factory=list)
    strategy: str = "none"
    # productId -> nom du coloris, pour que `diagnose` et /variante parlent en
    # « Blanc » plutôt qu'en 63503980.
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def guessed_sizes(self) -> dict[str, bool]:
        """What the non-confident observations suggested — diagnostics only."""
        return merge_observations([o for o in self.observations if not o.confident])

    @property
    def found(self) -> bool:
        return bool(self.sizes)

    def available_sizes(self) -> list[str]:
        return sorted(size for size, ok in self.sizes.items() if ok)


_ID_KEYS = {"productid", "id", "sku", "productcode", "masterproductid", "parentid", "collectionid"}
# Ids narrow enough to pair a size label with a stock level. "productid" is
# deliberately absent: it names the whole product, not one variant.
# Keys naming the product a node belongs to — used to keep one colourway's
# stock from being merged with another's.
_OWNER_KEYS = {"productid", "masterproductid", "collectionid"}
# Keys carrying the human name of a colourway. Numeric product ids mean nothing
# to anyone; "Blanc" is what the shopper sees on the page.
_COLOUR_KEYS = {
    "color", "colour", "colorname", "colourname", "couleur", "colorlabel",
    "swatchname", "colordescription", "colorway", "displaycolor", "colorgroup",
}
_JOIN_ID_KEYS = {
    "skuid", "sku", "variantid", "variantcode", "itemid", "productitemid",
    "styleid", "id", "code", "key", "upc", "ean", "gtin",
}
_MAX_DEPTH = 24


def product_id_from_url(url: str) -> str | None:
    """`.../p/icon-henley-63586319-2?x=1` -> `63586319`."""
    path = url.split("?", 1)[0].rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    matches = re.findall(r"\d{5,}", slug)
    return matches[-1] if matches else None


@dataclass
class _Collector:
    """What one pass over the JSON documents found."""

    observations: list[SizeObservation] = field(default_factory=list)
    # productId -> nom du coloris, quand la page le donne.
    labels: dict[str, str] = field(default_factory=dict)
    # Split schemas keep the size labels and the stock levels in separate
    # structures, joined by a sku/variant id — collect both halves.
    size_by_id: dict[str, tuple[str, bool]] = field(default_factory=dict)
    stock_by_id: dict[str, tuple[bool, str]] = field(default_factory=dict)

    def full(self) -> bool:
        return len(self.observations) > 500 or len(self.size_by_id) > 2000


def _ids_of(node: dict[str, Any]) -> list[str]:
    values = []
    for raw_key, value in node.items():
        if _key(raw_key) in _JOIN_ID_KEYS and isinstance(value, str | int) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                values.append(text)
    return values


def _colour_of(node: dict[str, Any]) -> str | None:
    """Le nom de coloris porté par ce nœud, s'il y en a un."""
    for raw_key, value in node.items():
        if _key(raw_key) in _COLOUR_KEYS and isinstance(value, str):
            text = value.strip()
            if 0 < len(text) <= 40:
                return text
    return None


def _owner_of(node: dict[str, Any], inherited: str | None) -> str | None:
    """Which product this node belongs to, when the page says so."""
    for raw_key, value in node.items():
        if _key(raw_key) in _OWNER_KEYS and isinstance(value, str | int) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return inherited


def _walk(
    node: Any,
    product_id: str | None,
    focused: bool,
    depth: int,
    out: _Collector,
    owner: str | None = None,
) -> None:
    if depth > _MAX_DEPTH or out.full():
        return
    if isinstance(node, dict):
        here = focused
        if product_id and not here:
            for raw_key, value in node.items():
                if _key(raw_key) in _ID_KEYS and isinstance(value, str | int) and str(value) == product_id:
                    here = True
                    break
        owner_here = _owner_of(node, owner)
        if owner_here:
            label = _colour_of(node)
            if label:
                out.labels.setdefault(owner_here, label)
        size = size_from_mapping(node)
        available = availability_from_mapping(node)
        if size and available is not None:
            out.observations.append(
                SizeObservation(
                    size=size,
                    available=available,
                    source="json",
                    detail=_short_repr(node),
                    focused=here,
                    owner=owner_here,
                )
            )
        elif size:
            for ident in _ids_of(node):
                out.size_by_id.setdefault(ident, (size, here))
        elif available is not None:
            for ident in _ids_of(node):
                out.stock_by_id.setdefault(ident, (available, _short_repr(node)))
        for value in node.values():
            _walk(value, product_id, here, depth + 1, out, owner_here)
    elif isinstance(node, list):
        for value in node:
            _walk(value, product_id, focused, depth + 1, out, owner)


def _joined_observations(collector: _Collector) -> list[SizeObservation]:
    """Pair `{id, size}` entries with `{id, inStock}` entries found elsewhere."""
    joined: list[SizeObservation] = []
    for ident, (size, focused) in collector.size_by_id.items():
        stock = collector.stock_by_id.get(ident)
        if stock is None:
            continue
        available, detail = stock
        joined.append(
            SizeObservation(size=size, available=available, source="json-join", detail=detail, focused=focused)
        )
    return joined


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

    Only trustworthy when the markup actually expresses stock state somewhere —
    a `disabled` attribute, a sold-out class, a `data-instock` flag. If the page
    lists sizes without a single unavailability marker, its stock is rendered
    client-side (or behind an API call) and the markup says nothing: every
    observation is then flagged `confident=False` so it can be shown by
    `diagnose` but never trigger an alert.
    """
    observations: list[SizeObservation] = []
    explicit_flags: list[bool] = []

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
        explicit = False
        for name in ("data-instock", "data-in-stock", "data-available", "data-availability", "data-stock"):
            if name in attrs:
                available = status_to_bool(attrs[name])
                if available is not None:
                    explicit = True
                    break
        if available is None:
            disabled = re.search(r"(?<![\w-])disabled(?![\w-])", lowered) is not None
            disabled = disabled or attrs.get("aria-disabled", "").lower() == "true"
            disabled = disabled or any(marker in lowered for marker in _UNAVAILABLE_MARKERS)
            available = not disabled
            explicit = disabled

        observations.append(
            SizeObservation(size=size, available=available, source="html", detail=attrs_raw.strip()[:220])
        )
        explicit_flags.append(explicit)

    # One size marked sold out proves the markup carries stock state, so the
    # unmarked siblings really are available. No marker anywhere proves nothing.
    trustworthy = any(explicit_flags)
    return [
        SizeObservation(
            size=observation.size,
            available=observation.available,
            source="html",
            detail=observation.detail,
            confident=trustworthy,
        )
        for observation in observations
    ]


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


def colour_tokens(value: str) -> frozenset[str]:
    """Les mots d'un nom de coloris, sans accents ni casse, traduits.

    Comparer des ensembles de mots plutôt que des chaînes permet de retrouver
    « light blue » à partir de « bleu clair » : l'ordre des mots diffère d'une
    langue à l'autre.
    """
    words = [_fold(word) for word in re.split(r"[^0-9A-Za-zÀ-ÿ]+", value) if word]
    return frozenset(_COLOUR_WORDS.get(word, word) for word in words if word)


def owners_matching_colour(labels: dict[str, str], wanted: str) -> list[str]:
    """Les produits dont le nom de coloris correspond à `wanted`.

    Sans accents ni casse, et de part et d'autre de la traduction : « blanc »
    retrouve « white », « bleu clair » retrouve « light blue », et « gris »
    retrouve « light heather grey ».
    """
    target = colour_tokens(wanted)
    if not target:
        return []
    exact = [owner for owner, label in labels.items() if colour_tokens(label) == target]
    if exact:
        return exact
    return [owner for owner, label in labels.items() if target <= colour_tokens(label)]


def parse_availability(
    body: str,
    *,
    product_id: str | None = None,
    product_color: str | None = None,
    html_fallback: bool = True,
) -> ParseResult:
    """Extract `{size: available}` from a product page or API response.

    `sizes` only ever holds readings the parser can defend; a guess (see
    `SizeObservation.confident`) stays out of it and shows up in
    `guessed_sizes`, which `diagnose` prints and the watcher ignores.
    """
    if not body:
        return ParseResult()

    collector = _Collector()
    for blob in iter_json_blobs(body):
        _walk(blob, product_id, False, 0, collector)

    observations = collector.observations
    labels = collector.labels
    strategy = "json"
    if not observations:
        observations = _joined_observations(collector)
        strategy = "json-join"

    def result(sizes: dict[str, bool], chosen: list[SizeObservation], name: str) -> ParseResult:
        return ParseResult(sizes=sizes, observations=chosen, strategy=name, labels=labels)

    if observations:
        # 1. Le coloris demandé par son nom — ce que voit l'acheteur sur la page.
        if product_color:
            wanted = owners_matching_colour(labels, product_color)
            picked = [o for o in observations if o.owner in wanted]
            if picked and len(set(wanted)) == 1:
                return result(merge_observations(picked), picked, f"{strategy}-couleur")
            if len(set(wanted)) > 1:
                return result({}, observations, "json-ambiguous-products")

        # 2. Le cas net : des lectures rattachées exactement au produit demandé.
        #    On compare au produit *le plus proche* de chaque lecture, jamais à
        #    un ancêtre : sur un cache Apollo, le produit affiché est nommé à la
        #    racine, et « tout ce qui descend de lui » englobe aussi les autres
        #    coloris — ce qui revient à annoncer le stock d'un autre article.
        if product_id:
            owned = [observation for observation in observations if observation.owner == product_id]
            if owned:
                return result(merge_observations(owned), owned, f"{strategy}-focused")

        # 3. Faute de mieux : les lectures situées sous un nœud qui nomme le
        #    produit, à condition qu'elles ne relèvent que d'un seul produit.
        focused = [observation for observation in observations if observation.focused]
        if focused:
            if len({observation.owner for observation in focused if observation.owner}) <= 1:
                return result(merge_observations(focused), focused, f"{strategy}-focused")
            return result({}, observations, "json-ambiguous-products")

        # 4. Aucune piste : on ne conclut que si toute la page parle d'un seul
        #    produit. Sinon on préfère dire qu'on ne sait pas.
        owners = {observation.owner for observation in observations if observation.owner}
        if len(owners) > 1:
            return result({}, observations, "json-ambiguous-products")
        return result(merge_observations(observations), observations, strategy)

    if html_fallback:
        html_observations = parse_html_size_buttons(body)
        if html_observations:
            confident = [observation for observation in html_observations if observation.confident]
            return ParseResult(
                sizes=merge_observations(confident),
                observations=html_observations,
                # Sizes rendered without any stock state: readable page, but the
                # stock is not in it. Reported as unreadable, never as available.
                strategy="html-heuristic" if confident else "html-no-stock-state",
                labels=labels,
            )

    return ParseResult(labels=labels)
