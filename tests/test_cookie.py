"""Import d'un cookie de navigateur : ce que le navigateur sait exporter d'un
clic droit, sans passer par le presse-papier et les pièges de quoting du shell."""

from __future__ import annotations

from stockwatch.cookie import extract_cookie, extract_headers, mask, unescape, write_env

CURL = """curl 'https://www.hollisterco.com/shop/eu-fr/p/icon-henley-63586319-2' \\
  -X 'GET' \\
  -H 'Accept: text/html,application/xhtml+xml' \\
  -H 'Cookie: ANFSession=abc123; _abck=XYZ~-1~long~value; bm_sz=999; at_check=true' \\
  -H 'User-Agent: Mozilla/5.0 (Macintosh)'"""


class TestExtraction:
    def test_from_a_curl_command(self):
        assert extract_cookie(CURL) == "ANFSession=abc123; _abck=XYZ~-1~long~value; bm_sz=999; at_check=true"

    def test_header_name_case_does_not_matter(self):
        assert extract_cookie("""curl 'x' -H "cookie: a=1; b=2" """) == "a=1; b=2"

    def test_long_option_and_double_quotes(self):
        assert extract_cookie('''curl 'x' --header "Cookie: a=1"''') == "a=1"

    def test_curl_cookie_jar_option(self):
        assert extract_cookie("""curl 'x' -b 'a=1; b=2'""") == "a=1; b=2"

    def test_a_bare_header_pasted_alone(self):
        assert extract_cookie("ANFSession=abc; _abck=zzz") == "ANFSession=abc; _abck=zzz"
        assert extract_cookie("Cookie: ANFSession=abc; _abck=zzz") == "ANFSession=abc; _abck=zzz"

    def test_a_cookie_spread_over_several_lines(self):
        assert extract_cookie("curl 'x' -H 'Cookie: a=1;\n  b=2'") == "a=1; b=2"

    def test_nothing_usable(self):
        assert extract_cookie("bonjour") is None
        assert extract_cookie("") is None
        assert extract_cookie("curl 'https://example.test' -H 'Accept: text/html'") is None


class TestSafariExport:
    """Safari exporte `-H $'Cookie: …'` dès qu'une valeur contient une
    apostrophe ou un accent. S'arrêter à la première apostrophe tronquerait le
    cookie au premier « women's » venu."""

    SAFARI = (
        "curl 'https://www.hollisterco.com/shop/eu-fr/p/icon-henley-63586319-2' \\\n"
        "-X 'GET' \\\n"
        "-H $'Cookie: a=1; page=hol:pdp:women\\'s:tops; nom=caf\\xc3\\xa9 cr\\xc3\\xa8me; last=9' \\\n"
        "-H 'User-Agent: Mozilla/5.0 (Macintosh) Version/26.1 Safari/605.1.15' \\\n"
        "-H 'Accept-Language: fr-FR,fr;q=0.9'"
    )

    def test_the_cookie_is_not_truncated_at_an_escaped_quote(self):
        cookie = extract_cookie(self.SAFARI)
        assert cookie.endswith("last=9")
        assert "women's" in cookie
        assert len([chunk for chunk in cookie.split(";") if "=" in chunk]) == 4

    def test_accented_bytes_are_decoded(self):
        assert "café crème" in extract_cookie(self.SAFARI)

    def test_the_browser_identity_comes_along(self):
        headers = extract_headers(self.SAFARI)
        assert "Safari/605.1.15" in headers["user-agent"]
        assert headers["accept-language"] == "fr-FR,fr;q=0.9"

    def test_unescape_handles_the_usual_suspects(self):
        assert unescape(r"a\'b") == "a'b"
        assert unescape(r"caf\xc3\xa9") == "café"
        assert unescape(r"a\\b") == "a\\b"
        assert unescape("sans echappement") == "sans echappement"


class TestEnvFile:
    def test_the_key_is_added_then_replaced(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("STOCKWATCH_BOT_TOKEN=1:x\nSTOCKWATCH_SIZES=XS,S\n", "utf-8")

        write_env(env, "STOCKWATCH_COOKIE", "a=1; b=2")
        assert "STOCKWATCH_COOKIE=a=1; b=2" in env.read_text()

        write_env(env, "STOCKWATCH_COOKIE", "c=3")
        content = env.read_text()
        assert "STOCKWATCH_COOKIE=c=3" in content
        assert content.count("STOCKWATCH_COOKIE=") == 1
        assert "STOCKWATCH_BOT_TOKEN=1:x" in content      # le reste est intact

    def test_the_file_is_not_world_readable(self, tmp_path):
        env = tmp_path / ".env"
        write_env(env, "STOCKWATCH_COOKIE", "a=1")
        assert env.stat().st_mode & 0o077 == 0

    def test_it_works_on_a_missing_file(self, tmp_path):
        env = tmp_path / "nouveau.env"
        write_env(env, "STOCKWATCH_COOKIE", "a=1")
        assert env.read_text() == "STOCKWATCH_COOKIE=a=1\n"


def test_the_preview_never_shows_values():
    preview = mask("ANFSession=secret-value; _abck=another-secret")
    assert "secret" not in preview
    assert "ANFSession" in preview and "_abck" in preview
