---
paths:
  - "backend/**/*.py"
  - "scripts/**/*.py"
  - "data_pipeline/**/*.py"
  - "loadtests/**/*.py"
---
# Python rules: Google Python Style Guide

## Docstrings (Google style, required)
- Every module starts with a docstring: one summary line, then what the module is for.
- Every public class, function and method has a docstring:
  - One-line summary in the imperative mood ("Return...", "Fetch..."), ending with a period.
  - Then a blank line and more detail when the behaviour is not obvious.
  - Sections in this order when they apply: Args:, Returns: (or Yields:), Raises:, Example:.
  - Args: one line per parameter "name: description." (types live in the type hints).
  - Classes: describe purpose; list public attributes under Attributes:.
- Example:
      def get_open_orders(supplier_id: str, limit: int = 50) -> list[OpenOrder]:
          """Return open purchase orders for one supplier.

          Uses the fixed SQL template OPEN_ORDERS_SQL against the risk.v_open_orders view.

          Args:
              supplier_id: Supplier id in the form S0000.
              limit: Maximum number of rows to return (1-200).

          Returns:
              Open orders sorted by due date, oldest first.

          Raises:
              ValueError: If supplier_id does not match the expected pattern.
              ZetarisError: If the Zetaris query fails.
          """

## Comments
- Explain WHY (intent, constraints, trade-offs), not WHAT the code already says.
- Mark security-relevant code with a short "Security:" comment.
- No commented-out code. TODOs only as "TODO(SEC-<id>|issue): reason".

## Code
- Google naming: snake_case functions/variables, PascalCase classes, UPPER_CASE constants; descriptive
  names; imports grouped (stdlib, third-party, local), one per line, no wildcard imports.
- Type hints on every signature; mypy strict must pass. Pydantic v2 models for inputs/outputs.
- Small functions with one responsibility; max line length 100; raise specific exceptions; no bare except.
- async httpx for network calls with explicit timeouts. Use the structured logger, never print().

## Tests
- Each test module has a one-line module docstring. Test functions need a docstring only when the
  name does not explain the scenario (test_returns_unauthorized_on_401 needs none).
