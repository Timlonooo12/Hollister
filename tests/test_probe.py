"""Recherche d'un endpoint plus léger que la page HTML."""

from __future__ import annotations

from stockwatch.probe import extract_candidates, guessed_candidates, rank

PAGE_URL = "https://www.hollisterco.com/shop/eu-fr/p/icon-henley-63586319-2"
BODY = """
<html>
  <script src="/static/bundle.js"></script>
  <link rel="stylesheet" href="/assets/main.css">
  <script>
    var endpoints = {
      graph: "/api/ecomm/graphql",
      inventory: "https://www.hollisterco.com/api/ecomm/hol/inventory/63503980",
      image: "https://img.hollisterco.com/is/image/anf/product-63503980.jpg",
      elsewhere: "https://other.example.com/api/product/1",
      protocolRelative: "//www.hollisterco.com/api/ccp/v1/product/63503980"
    };
  </script>
</html>
"""


class TestCandidateExtraction:
    def test_keeps_api_looking_urls_of_the_same_host(self):
        found = extract_candidates(BODY, PAGE_URL)
        assert "https://www.hollisterco.com/api/ecomm/graphql" in found
        assert "https://www.hollisterco.com/api/ecomm/hol/inventory/63503980" in found
        assert "https://www.hollisterco.com/api/ccp/v1/product/63503980" in found

    def test_drops_assets_and_other_hosts(self):
        found = extract_candidates(BODY, PAGE_URL)
        assert not any(url.endswith((".js", ".css", ".jpg")) for url in found)
        assert not any("other.example.com" in url for url in found)
        assert not any("img.hollisterco.com" in url for url in found)

    def test_guesses_use_the_product_id_and_the_brand(self):
        guesses = guessed_candidates(PAGE_URL)
        assert any("63586319" in url for url in guesses)
        assert any("/hol/" in url for url in guesses)
        assert all(url.startswith("https://www.hollisterco.com/") for url in guesses)


class TestRanking:
    def test_inventory_and_product_id_come_first(self):
        candidates = [
            "https://www.hollisterco.com/api/help/faq",
            "https://www.hollisterco.com/api/ecomm/graphql",
            "https://www.hollisterco.com/api/ecomm/hol/inventory/63586319",
        ]
        ordered = rank(candidates, PAGE_URL)
        assert "inventory" in ordered[0]
        assert ordered[-1].endswith("/faq")

    def test_duplicates_are_removed(self):
        url = "https://www.hollisterco.com/api/ecomm/graphql"
        assert rank([url, url], PAGE_URL) == [url]
