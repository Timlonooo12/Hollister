"""The parser is the part that breaks when the site changes: pin its behaviour
against every response shape it is meant to survive."""

from __future__ import annotations

import json

from stockwatch.parsing import (
    availability_from_mapping,
    normalize_size,
    parse_availability,
    parse_html_size_buttons,
    product_id_from_url,
    status_to_bool,
)


class TestNormalizeSize:
    def test_canonical_forms(self):
        assert normalize_size("XS") == "XS"
        assert normalize_size("xs") == "XS"
        assert normalize_size(" x-small ") == "XS"
        assert normalize_size("X Small") == "XS"
        assert normalize_size("Extra Small") == "XS"
        assert normalize_size("Small") == "S"
        assert normalize_size("Taille S") == "S"
        assert normalize_size("2XL") == "XXL"

    def test_rejects_non_sizes(self):
        assert normalize_size("Bleu marine") is None
        assert normalize_size("") is None
        assert normalize_size(None) is None
        assert normalize_size(42) is None
        assert normalize_size("a" * 40) is None


class TestStatusParsing:
    def test_schema_org_urls(self):
        assert status_to_bool("https://schema.org/InStock") is True
        assert status_to_bool("http://schema.org/OutOfStock") is False

    def test_platform_wordings(self):
        assert status_to_bool("IN_STOCK") is True
        assert status_to_bool("Low Stock") is True
        assert status_to_bool("SOLD_OUT") is False
        assert status_to_bool("Rupture") is None  # unknown wording, no guess
        assert status_to_bool("BACKORDER") is False

    def test_signals_must_agree(self):
        assert availability_from_mapping({"inStock": True, "quantity": 0}) is False
        assert availability_from_mapping({"inStock": True, "quantity": 7}) is True
        assert availability_from_mapping({"soldOut": False}) is True
        assert availability_from_mapping({"soldOut": True, "available": True}) is False
        assert availability_from_mapping({"color": "navy"}) is None


class TestJsonExtraction:
    def test_embedded_initial_state(self):
        payload = {
            "product": {
                "productId": "63586319",
                "variants": [
                    {"sizeName": "XS", "inStock": False, "quantity": 0},
                    {"sizeName": "S", "inStock": True, "quantity": 4},
                    {"sizeName": "M", "inStock": True, "quantity": 12},
                ],
            }
        }
        body = f"<html><head><script>window.__INITIAL_STATE__ = {json.dumps(payload)};</script></head></html>"
        result = parse_availability(body, product_id="63586319")
        assert result.strategy == "json-focused"
        assert result.sizes == {"XS": False, "S": True, "M": True}
        assert result.available_sizes() == ["M", "S"]

    def test_next_data_script_block(self):
        payload = {"props": {"pageProps": {"sizes": [
            {"label": "XS", "availability": "OUT_OF_STOCK"},
            {"label": "S", "availability": "IN_STOCK"},
        ]}}}
        body = (
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(payload)
            + "</script>"
        )
        result = parse_availability(body)
        assert result.sizes == {"XS": False, "S": True}

    def test_structured_data_offers(self):
        payload = {
            "@type": "Product",
            "offers": [
                {"@type": "Offer", "name": "XS", "availability": "https://schema.org/InStock"},
                {"@type": "Offer", "name": "S", "availability": "https://schema.org/OutOfStock"},
            ],
        }
        body = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        result = parse_availability(body)
        assert result.sizes == {"XS": True, "S": False}

    def test_raw_json_api_response(self):
        body = json.dumps({"skus": [{"size": "XS", "availableQuantity": 0},
                                    {"size": "S", "availableQuantity": 3}]})
        result = parse_availability(body)
        assert result.sizes == {"XS": False, "S": True}

    def test_other_products_are_ignored_when_the_target_is_identified(self):
        payload = {
            "recommendations": [
                {"productId": "111", "variants": [{"size": "XS", "inStock": True}]},
            ],
            "product": {"productId": "63586319", "variants": [{"size": "XS", "inStock": False}]},
        }
        body = "<script>window.__INITIAL_STATE__ = " + json.dumps(payload) + ";</script>"
        result = parse_availability(body, product_id="63586319")
        assert result.sizes == {"XS": False}

    def test_same_size_seen_twice_resolves_to_available(self):
        payload = {"a": {"size": "S", "inStock": False}, "b": {"size": "S", "inStock": True}}
        result = parse_availability(json.dumps(payload))
        assert result.sizes == {"S": True}

    def test_block_page_yields_nothing(self):
        body = "<html><head><title>Access Denied</title></head><body>Reference #18.2</body></html>"
        assert parse_availability(body).found is False


class TestSplitSchemas:
    def test_sizes_and_stock_joined_by_sku(self):
        payload = {
            "sizes": [{"skuId": "A1", "label": "XS"}, {"skuId": "A2", "label": "S"}],
            "inventory": [{"skuId": "A1", "inStock": False}, {"skuId": "A2", "inStock": True}],
        }
        result = parse_availability(json.dumps(payload))
        assert result.strategy == "json-join"
        assert result.sizes == {"XS": False, "S": True}

    def test_a_size_with_no_matching_stock_entry_is_dropped(self):
        payload = {
            "sizes": [{"skuId": "A1", "label": "XS"}],
            "inventory": [{"skuId": "ZZ", "inStock": True}],
        }
        assert parse_availability(json.dumps(payload)).found is False

    def test_direct_readings_win_over_a_join(self):
        payload = {
            "variants": [{"skuId": "A1", "size": "XS", "inStock": False}],
            "inventory": [{"skuId": "A1", "inStock": True}],
        }
        result = parse_availability(json.dumps(payload))
        assert result.strategy == "json"
        assert result.sizes == {"XS": False}


class TestRealCatalogueSchemas:
    """Shapes seen on the live Hollister page (see README §5 bis)."""

    HOLLISTER = {
        "skus": [
            {"__typename": "Sku", "inventory": 0, "inventoryStatus": "Unavailable",
             "productId": "63503980", "shortSku": "675056184", "sizePrimary": "XS_p"},
            {"__typename": "Sku", "inventory": 4, "inventoryStatus": "InStock",
             "productId": "63503980", "shortSku": "675056185", "sizePrimary": "S_p"},
        ]
    }

    def test_size_primary_with_a_dimension_suffix(self):
        assert normalize_size("L_p") == "L"
        assert normalize_size("XS_p") == "XS"
        assert normalize_size("XXL_p") == "XXL"
        assert normalize_size("M/32") == "M"
        assert normalize_size("_p") is None
        assert normalize_size("navy_p") is None

    APOLLO_KEY = "APOLLO_STATE__product-mfe-web-service-ProductPageFrontend-config"

    def test_apollo_cache_assigned_under_a_bracket_key(self):
        """The real page ships its state as
        `window['APOLLO_STATE__…-config'] = {…}` — a bracket assignment with a
        service-specific key, so no fixed property name matches it."""
        cache = {"CACHE": {
            "SuggestedSearchAttributes:63503980": {"__typename": "SuggestedSearchAttributes",
                                                   "id": "63503980"},
            "Product:63503980": {"__typename": "Product", "productId": "63503980", "skus": [
                {"__typename": "Sku", "inventory": 0, "inventoryStatus": "Unavailable",
                 "productId": "63503980", "shortSku": "675056184", "sizePrimary": "XS_p"},
                {"__typename": "Sku", "inventory": 6, "inventoryStatus": "InStock",
                 "productId": "63503980", "shortSku": "675056185", "sizePrimary": "S_p"},
            ]},
        }}
        body = (
            "<script type=\"text/javascript\">window['" + self.APOLLO_KEY + "'] = "
            + json.dumps(cache) + ";</script>"
        )
        result = parse_availability(body, product_id="63586319")
        assert result.sizes == {"XS": False, "S": True}

    def test_size_tiles_without_stock_do_not_pollute_the_reading(self):
        cache = {"CACHE": {"Product:1": {"productId": "1",
            "primarySizeArray": [
                {"__typename": "SizeTile", "sizeText": "XS", "value": "XS_p",
                 "label": "XS", "description": "Taille"},
            ],
            "skus": [{"__typename": "Sku", "inventory": 0, "inventoryStatus": "Unavailable",
                      "productId": "1", "sizePrimary": "XS_p"}],
        }}}
        body = "<script>window['APOLLO_STATE__x'] = " + json.dumps(cache) + ";</script>"
        assert parse_availability(body).sizes == {"XS": False}

    def test_a_javascript_object_literal_is_skipped_quietly(self):
        body = "<script>window.config = {unquoted: 'key', trailing: [1,2,],};</script>"
        assert parse_availability(body, html_fallback=False).found is False

    def test_hollister_sku_list(self):
        result = parse_availability(json.dumps(self.HOLLISTER), product_id="63503980")
        assert result.sizes == {"XS": False, "S": True}

    def test_colourways_are_not_merged(self):
        payload = {"products": [
            {"productId": "111", "skus": [{"sizePrimary": "XS_p", "inventory": 0,
                                           "inventoryStatus": "Unavailable"}]},
            {"productId": "222", "skus": [{"sizePrimary": "XS_p", "inventory": 9,
                                           "inventoryStatus": "InStock"}]},
        ]}
        result = parse_availability(json.dumps(payload))
        # Nothing says which colour the page shows: refuse rather than merge.
        assert result.strategy == "json-ambiguous-products"
        assert result.sizes == {}

    def test_naming_the_colourway_resolves_it(self):
        payload = {"products": [
            {"productId": "111", "skus": [{"sizePrimary": "XS_p", "inventory": 0,
                                           "inventoryStatus": "Unavailable"}]},
            {"productId": "222", "skus": [{"sizePrimary": "XS_p", "inventory": 9,
                                           "inventoryStatus": "InStock"}]},
        ]}
        assert parse_availability(json.dumps(payload), product_id="111").sizes == {"XS": False}
        assert parse_availability(json.dumps(payload), product_id="222").sizes == {"XS": True}

    def test_naming_the_shown_product_must_not_widen_to_the_other_colourways(self):
        """Régression : sur le cache Apollo de Hollister, le produit affiché est
        nommé à la racine et le cache contient tous les coloris. Cibler « tout
        ce qui descend du nœud portant cet identifiant » revenait à fusionner
        les 17 coloris — le bot a annoncé « DISPO XS, S » sur un article épuisé."""
        cache = {"productId": "63503980", "CACHE": {
            "Product:63503980": {"productId": "63503980", "skus": [
                {"sizePrimary": "XS_p", "inventory": 0, "inventoryStatus": "Unavailable"},
                {"sizePrimary": "S_p", "inventory": 0, "inventoryStatus": "Unavailable"},
                {"sizePrimary": "XXL_p", "inventory": 3, "inventoryStatus": "InStock"},
            ]},
            "Product:63492467": {"productId": "63492467", "skus": [
                {"sizePrimary": "XS_p", "inventory": 9, "inventoryStatus": "InStock"},
                {"sizePrimary": "S_p", "inventory": 9, "inventoryStatus": "InStock"},
            ]},
        }}
        body = "<script>window['APOLLO_STATE__x'] = " + json.dumps(cache) + ";</script>"
        result = parse_availability(body, product_id="63503980")
        assert result.sizes == {"XS": False, "S": False, "XXL": True}
        assert all(observation.owner == "63503980" for observation in result.observations)

    def test_an_ancestor_covering_several_products_is_refused(self):
        cache = {"collectionId": "63586319", "CACHE": {
            "Product:1": {"productId": "1", "skus": [
                {"sizePrimary": "XS_p", "inventory": 0, "inventoryStatus": "Unavailable"}]},
            "Product:2": {"productId": "2", "skus": [
                {"sizePrimary": "XS_p", "inventory": 5, "inventoryStatus": "InStock"}]},
        }}
        body = "<script>window['APOLLO_STATE__x'] = " + json.dumps(cache) + ";</script>"
        result = parse_availability(body, product_id="63586319")
        assert result.strategy == "json-ambiguous-products"
        assert result.sizes == {}

    def test_one_product_needs_no_disambiguation(self):
        payload = {"product": {"productId": "111", "skus": [
            {"sizePrimary": "XS_p", "inventory": 0, "inventoryStatus": "Unavailable"},
            {"sizePrimary": "S_p", "inventory": 2, "inventoryStatus": "InStock"},
        ]}}
        result = parse_availability(json.dumps(payload))
        assert result.sizes == {"XS": False, "S": True}


class TestHtmlFallback:
    def test_disabled_buttons_are_out_of_stock(self):
        html = """
        <div class="size-selector">
          <button class="size-btn size-btn--sold-out" data-size="XS" disabled>XS</button>
          <button class="size-btn" data-size="S">S</button>
        </div>
        """
        result = parse_availability(html)
        assert result.strategy == "html-heuristic"
        assert result.sizes == {"XS": False, "S": True}

    def test_aria_label_and_explicit_flag(self):
        html = '<li aria-label="Taille XS" data-instock="false"></li><li aria-label="Taille S" data-instock="true"></li>'
        assert {o.size: o.available for o in parse_html_size_buttons(html)} == {"XS": False, "S": True}

    def test_sizes_without_any_stock_marker_are_not_a_reading(self):
        """The Hollister case: sizes rendered client-side. Guessing here means
        announcing a restock on a sold-out product."""
        html = '<button data-size="XS">XS</button><button data-size="S">S</button>'
        result = parse_availability(html)
        assert result.strategy == "html-no-stock-state"
        assert result.found is False
        assert result.sizes == {}
        # Still visible to `diagnose`, just not actionable.
        assert result.guessed_sizes == {"XS": True, "S": True}

    def test_one_sold_out_marker_makes_the_whole_selector_readable(self):
        html = (
            '<button data-size="XS" class="sold-out">XS</button>'
            '<button data-size="S">S</button>'
        )
        result = parse_availability(html)
        assert result.strategy == "html-heuristic"
        assert result.sizes == {"XS": False, "S": True}

    def test_fallback_can_be_disabled(self):
        html = '<button data-size="S">S</button>'
        assert parse_availability(html, html_fallback=False).found is False

    def test_json_wins_over_html(self):
        html = (
            '<script type="application/json">{"v":[{"size":"S","inStock":false}]}</script>'
            '<button data-size="S">S</button>'
        )
        result = parse_availability(html)
        assert result.strategy == "json"
        assert result.sizes == {"S": False}


def test_product_id_from_url():
    url = "https://www.hollisterco.com/shop/eu-fr/p/icon-henley-63586319-2?faceout=model&seq=15"
    assert product_id_from_url(url) == "63586319"
    assert product_id_from_url("https://example.test/p/tee") is None
