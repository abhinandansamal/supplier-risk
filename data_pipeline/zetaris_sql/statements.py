"""Split a Zetaris SQL file into the statements the getting-started runner will send.

Lightning SQL takes one statement per call without a trailing semicolon, so files separate
statements with semicolons and the runner splits them. This splitter does the same (aware of
quotes, backticks and comments) so the tests can check every statement before it is run.
Comments are dropped from the returned statements.
"""

_QUOTES = ("'", '"', "`")


def split_statements(text: str) -> list[str]:
    """Return the non-empty statements of a SQL script, without comments or semicolons.

    Args:
        text: The SQL script.

    Returns:
        Statements in file order, stripped, each without a trailing semicolon.
    """
    statements: list[str] = []
    current: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char in _QUOTES:
            end = _quote_end(text, i)
            current.append(text[i:end])
            i = end
        elif text.startswith("--", i):
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
            current.append(" ")
        elif char == ";":
            statements.append("".join(current))
            current = []
            i += 1
        else:
            current.append(char)
            i += 1
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


def _quote_end(text: str, start: int) -> int:
    """Return the index after the quoted literal that starts at `start` ('' escapes a quote)."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == quote:
            if text.startswith(quote * 2, i):
                i += 2
                continue
            return i + 1
        i += 1
    return len(text)
