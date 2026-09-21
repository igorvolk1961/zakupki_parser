"""Тесты заполнения профиля по URL (SSRF-защита, фетч, LLM, лицензии, e2e)."""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from zakupki_parser.api.app.profile_source import (
    ProfileFromUrlError,
    ProfileFromUrlNotConfigured,
    _ensure_public_host,
    _extract_licenses,
    _is_public_ip,
    fetch_url_html,
    generate_profile_from_url,
    html_to_text,
)

_LICENSE_TYPES = [(1, "Лицензия на утилизацию отходов"), (2, "Лицензия ФСБ на шифрование")]


def test_is_public_ip_rejects_private_and_special_ranges() -> None:
    blocked = [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # private
        "192.168.1.1",  # private
        "169.254.1.1",  # link-local (cloud metadata тоже сюда попадает у некоторых провайдеров)
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "::1",  # loopback v6
        "fc00::1",  # unique local v6
    ]
    for raw in blocked:
        assert _is_public_ip(ipaddress.ip_address(raw)) is False, raw


def test_is_public_ip_allows_public_address() -> None:
    assert _is_public_ip(ipaddress.ip_address("8.8.8.8")) is True


_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


async def test_ensure_public_host_blocks_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(host: str) -> list[_IpAddress]:
        return [ipaddress.ip_address("10.0.0.1")]

    monkeypatch.setattr("zakupki_parser.api.app.profile_source._resolve", fake_resolve)
    with pytest.raises(ProfileFromUrlError, match="внутренний"):
        await _ensure_public_host("internal.example")


async def test_ensure_public_host_allows_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(host: str) -> list[_IpAddress]:
        return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr("zakupki_parser.api.app.profile_source._resolve", fake_resolve)
    await _ensure_public_host("example.com")  # не бросает


def _no_ssrf_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отключает DNS-резолвинг для тестов fetch/e2e (сеть в песочнице недоступна)."""

    async def _ok(host: str) -> None:
        return None

    monkeypatch.setattr("zakupki_parser.api.app.profile_source._ensure_public_host", _ok)


async def test_fetch_url_html_rejects_non_http_scheme() -> None:
    with pytest.raises(ProfileFromUrlError, match="http/https"):
        await fetch_url_html("file:///etc/passwd")


async def test_fetch_url_html_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_ssrf_check(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    body = await fetch_url_html("https://example.com", client=client)
    assert body == b"<p>hi</p>"


async def test_fetch_url_html_follows_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_ssrf_check(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == "https://example.com/old":
            return httpx.Response(302, headers={"location": "https://example.com/new"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    body = await fetch_url_html("https://example.com/old", client=client)
    assert body == b"<p>ok</p>"
    assert calls == ["https://example.com/old", "https://example.com/new"]


async def test_fetch_url_html_rejects_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_ssrf_check(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 100)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(ProfileFromUrlError, match="большая"):
        await fetch_url_html("https://example.com", client=client, max_bytes=10)


async def test_fetch_url_html_rejects_wrong_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_ssrf_check(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(ProfileFromUrlError, match="HTML"):
        await fetch_url_html("https://example.com", client=client)


def test_html_to_text_extracts_visible_text() -> None:
    html = (
        b"<html><body><h1>Company</h1><p>We build widgets.</p><script>evil()</script></body></html>"
    )
    text = html_to_text(html)
    assert "Company" in text
    assert "widgets" in text
    assert "evil" not in text


def test_html_to_text_raises_on_empty_page() -> None:
    with pytest.raises(ProfileFromUrlError, match="текста"):
        html_to_text(b"<html><body></body></html>")


def test_extract_licenses_keeps_only_known_type_ids() -> None:
    raw = [
        {"license_type_id": 1, "number": "123", "authority": "Росприроднадзор", "notes": "  "},
        {"license_type_id": 999, "number": "нет такого типа"},  # отбрасывается
        {"number": "нет id вовсе"},  # отбрасывается
        "не объект",  # отбрасывается
    ]
    result = _extract_licenses(raw, {1, 2})
    assert result == [
        {
            "license_type_id": 1,
            "number": "123",
            "authority": "Росприроднадзор",
            "issue_date": None,
            "expiry_date": None,
            "notes": None,
        }
    ]


def test_extract_licenses_cleans_malformed_dates() -> None:
    raw = [{"license_type_id": 2, "issue_date": "2024-01-15", "expiry_date": "не дата"}]
    result = _extract_licenses(raw, {1, 2})
    assert result[0]["issue_date"] == "2024-01-15"
    assert result[0]["expiry_date"] is None


def test_extract_licenses_non_list_returns_empty() -> None:
    assert _extract_licenses(None, {1, 2}) == []
    assert _extract_licenses("не список", {1, 2}) == []


async def test_generate_profile_from_url_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_ssrf_check(monkeypatch)
    monkeypatch.delenv("ZAKUPKI_PROFILE_LLM_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(ProfileFromUrlNotConfigured):
        await generate_profile_from_url("https://example.com", _LICENSE_TYPES, http_client=client)


async def test_generate_profile_from_url_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_ssrf_check(monkeypatch)
    monkeypatch.setenv("ZAKUPKI_PROFILE_LLM_API_KEY", "test-key")

    llm_payload = (
        '{"positioning": "Строим виджеты под ключ", "breadth": "narrow", '
        '"competencies": [{"area": "Виджеты", "description": "Проектирование и поставка", '
        '"examples": ["Виджет для завода"]}], "exclusions": ["Консалтинг"], '
        '"licenses": [{"license_type_id": 1, "number": "77-АБ-001", '
        '"authority": "Росприроднадзор", "issue_date": "2022-03-01", '
        '"expiry_date": null, "notes": null}, '
        '{"license_type_id": 42, "number": "выдуманный тип"}]}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<h1>Widgets Inc</h1><p>We build widgets. License 77-AB-001.</p>",
            )
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": llm_payload}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    result = await generate_profile_from_url(
        "https://example.com", _LICENSE_TYPES, http_client=client
    )
    assert '"positioning":"Строим виджеты под ключ"' in result.competencies
    assert '"breadth":"narrow"' in result.competencies
    assert "Виджеты" in result.competencies
    # Только лицензия с реальным license_type_id — выдуманный (42) отброшен.
    assert result.licenses == [
        {
            "license_type_id": 1,
            "number": "77-АБ-001",
            "authority": "Росприроднадзор",
            "issue_date": "2022-03-01",
            "expiry_date": None,
            "notes": None,
        }
    ]


async def test_generate_profile_from_url_invalid_llm_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_ssrf_check(monkeypatch)
    monkeypatch.setenv("ZAKUPKI_PROFILE_LLM_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>hi</p>")
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(ProfileFromUrlError, match="не JSON"):
        await generate_profile_from_url("https://example.com", _LICENSE_TYPES, http_client=client)
