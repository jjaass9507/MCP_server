"""Custom business logic tools.

This module ships a few demonstration tools and serves as the template for
adding your own domain-specific logic.

HOW TO ADD NEW TOOLS
====================
1. Define your function below with type hints and a docstring.
2. Decorate it with @mcp.tool() inside register().
3. Raise ToolError for user-facing error messages.

HOW TO ADD A NEW TOOL CATEGORY
================================
1. Create a new file under tools/ (e.g., tools/payments.py).
2. Add a register(mcp: FastMCP) function with your @mcp.tool() decorators.
3. Import it in server.py and call payments.register(mcp).
"""

import ast
import json
import operator
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError
from mcp_server.utils.version import code_info

# Allowed math operators/names for the safe calculator
_SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "pow": pow, "int": int, "float": float,
}

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MAX_EXPONENT = 10_000
_MAX_INTEGER_BITS = 100_000


def _validate_number(value: object) -> int | float:
    if type(value) not in (int, float):
        raise ValueError("result must be a real number")
    if isinstance(value, int) and value.bit_length() > _MAX_INTEGER_BITS:
        raise ValueError("integer result is too large")
    return value


def _safe_calculate(expression: str) -> int | float:
    """Evaluate arithmetic using an explicit AST whitelist."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"invalid syntax: {e.msg}") from e

    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("expression is too complex")

    def evaluate(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise ValueError("only numeric literals are allowed")
            return _validate_number(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
                raise ValueError("exponent is too large")
            return _validate_number(_BINARY_OPERATORS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return _validate_number(
                _UNARY_OPERATORS[type(node.op)](evaluate(node.operand))
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_NAMES:
                raise ValueError("function is not allowed")
            if node.keywords:
                raise ValueError("keyword arguments are not allowed")
            args = [evaluate(arg) for arg in node.args]
            if node.func.id == "pow" and len(args) >= 2 and abs(args[1]) > _MAX_EXPONENT:
                raise ValueError("exponent is too large")
            return _validate_number(_SAFE_NAMES[node.func.id](*args))
        raise ValueError(f"unsupported expression: {type(node).__name__}")

    return evaluate(tree)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def echo(message: str) -> str:
        """Return the message unchanged. Useful for testing server connectivity."""
        return message

    @mcp.tool()
    def system_info() -> dict[str, Any]:
        """Return basic information about the server environment.

        Includes: Python version, platform, UTC timestamp, plus the running
        code's path, git commit and branch. Call this to verify WHICH VERSION
        of the server code is actually running — e.g. when a tool behaves
        like an old, already-fixed version, check git_commit/git_branch here
        before debugging anything else.
        """
        return {
            "python_version": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "utc_time": datetime.now(timezone.utc).isoformat(),
            **code_info(),
        }

    @mcp.tool()
    def calculate(expression: str) -> str:
        """Safely evaluate a mathematical expression and return the result.

        Supports: +, -, *, /, **, //, %, parentheses, and the functions
        abs, round, min, max, pow, int, float.

        Example: calculate("2 ** 10 + round(3.7)")  →  "1028"
        """
        try:
            result = _safe_calculate(expression)
            return str(result)
        except ZeroDivisionError:
            raise ToolError("Division by zero.")
        except Exception as e:
            raise ToolError(f"Could not evaluate expression: {e}") from e

    @mcp.tool()
    def format_data(
        data: str,
        input_format: Literal["json", "plain"] = "json",
        output_format: Literal["json", "plain", "pretty_json"] = "pretty_json",
    ) -> str:
        """Parse and reformat data between JSON and plain-text representations.

        Use input_format='json' to parse JSON and re-emit it as pretty_json or plain.
        Use input_format='plain' to wrap a plain string in a JSON object.

        Args:
            data:          The input data string.
            input_format:  How to interpret the input ('json' or 'plain').
            output_format: Desired output format ('json', 'pretty_json', or 'plain').
        """
        if input_format == "json":
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError as e:
                raise ToolError(f"Invalid JSON input: {e}") from e
        else:
            parsed = {"data": data}

        if output_format == "pretty_json":
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        elif output_format == "json":
            return json.dumps(parsed, ensure_ascii=False)
        else:
            if isinstance(parsed, str):
                return parsed
            return str(parsed)

    # ---------------------------------------------------------------
    # Add your own tools below this line following the same pattern.
    # ---------------------------------------------------------------
