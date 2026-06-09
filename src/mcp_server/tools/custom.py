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

import json
import platform
import sys
from datetime import datetime, timezone
from typing import Literal

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError

# Allowed math operators/names for the safe calculator
_SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "pow": pow, "int": int, "float": float,
}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def echo(message: str) -> str:
        """Return the message unchanged. Useful for testing server connectivity."""
        return message

    @mcp.tool()
    def system_info() -> dict:
        """Return basic information about the server environment.

        Includes: Python version, platform, current working directory, UTC timestamp.
        """
        return {
            "python_version": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "utc_time": datetime.now(timezone.utc).isoformat(),
        }

    @mcp.tool()
    def calculate(expression: str) -> str:
        """Safely evaluate a mathematical expression and return the result.

        Supports: +, -, *, /, **, //, %, parentheses, and the functions
        abs, round, min, max, pow, int, float.

        Example: calculate("2 ** 10 + round(3.7)")  →  "1028"
        """
        try:
            result = eval(  # noqa: S307
                expression,
                {"__builtins__": {}},
                _SAFE_NAMES,
            )
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
