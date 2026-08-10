"""Tests for custom utility tools."""

import pytest

from mcp_server.tools.custom import _safe_calculate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 ** 10 + round(3.7)", 1028),
        ("abs(-5) + min(2, 3)", 7),
        ("7 // 2 + 7 % 2", 4),
    ],
)
def test_safe_calculate_supported_expressions(expression, expected):
    assert _safe_calculate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "().__class__.__bases__[0].__subclasses__()",
        "__import__('os').getcwd()",
        "(lambda: 1)()",
        "round(number=1.2)",
        "'not a number'",
    ],
)
def test_safe_calculate_rejects_non_whitelisted_syntax(expression):
    with pytest.raises(ValueError):
        _safe_calculate(expression)


def test_safe_calculate_rejects_overly_complex_expression():
    with pytest.raises(ValueError, match="too complex"):
        _safe_calculate("+".join(["1"] * 60))


@pytest.mark.parametrize("expression", ["2 ** 10001", "pow(2, 10001)"])
def test_safe_calculate_rejects_excessive_exponents(expression):
    with pytest.raises(ValueError, match="exponent is too large"):
        _safe_calculate(expression)
