"""The static UI: no HTML sinks for data, no inline code, no external resources."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "app" / "web" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")

# Security: every way to turn a string into markup or code (T9).
_SINKS = [
    r"\.innerHTML",
    r"\.outerHTML",
    r"insertAdjacentHTML",
    r"document\.write",
    r"\beval\s*\(",
    r"new\s+Function",
    r"createContextualFragment",
    r"DOMParser",
    r"setTimeout\s*\(\s*['\"`]",
    r"\.setAttribute\s*\(\s*['\"]on",
    r"\.src\s*=",
    r"\.href\s*=",
    r"localStorage",
    r"sessionStorage",
]


@pytest.mark.parametrize("sink", _SINKS)
def test_js_never_uses_an_html_or_code_sink(sink: str) -> None:
    assert not re.search(sink, JS), sink


def test_js_renders_data_with_text_content() -> None:
    assert "textContent" in JS


def test_html_has_no_inline_script_style_or_handlers() -> None:
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", HTML)
    assert "<style" not in HTML and " style=" not in HTML
    assert not re.search(r"\son[a-z]+\s*=", HTML)


@pytest.mark.parametrize("text", [JS, HTML, CSS])
def test_no_external_resources(text: str) -> None:
    assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", text, re.IGNORECASE)
    assert "@import" not in text


def test_example_questions_are_offered() -> None:
    for supplier_id in ("S0003", "S0005", "S0011"):
        assert supplier_id in HTML
