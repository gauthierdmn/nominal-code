import base64
import json

from nominal_code.agent.result import AgentResult

GITHUB_TEST_REPO = "gauthierdmn/nominal-code-test"
GITLAB_TEST_REPO = "gauthierdmn/nominal-code-test"

BUGGY_CALCULATOR_PATH = "src/calculator.py"

# Line numbers must match BUGGY_REVIEW_JSON:
#   line 1  → import os          (unused import finding)
#   line 46 → def divide(...)    (missing return type finding)
#   line 60 → first / second     (division by zero finding)
BUGGY_CALCULATOR_CONTENT = """\
import os


def add(first, second):
    \"\"\"Add two numbers together.\"\"\"

    return first + second


def subtract(first, second):
    \"\"\"Subtract second from first.\"\"\"

    return first - second


def multiply(first, second):
    \"\"\"Multiply two numbers together.\"\"\"

    return first * second


def power(base, exponent):
    \"\"\"Raise base to the given exponent.\"\"\"

    result = 1

    for _ in range(exponent):
        result *= base

    return result


def absolute(value):
    \"\"\"Return the absolute value of a number.\"\"\"
    if value < 0:
        return -value
    return value


def negate(value):
    \"\"\"Negate a number.\"\"\"

    return -value


def divide(first, second):
    \"\"\"Divide first by second.\"\"\"

    if not isinstance(first, (int, float)):
        raise TypeError("first must be a number")

    if not isinstance(second, (int, float)):
        raise TypeError("second must be a number")

    log_message = f"Dividing {first} by {second}"
    print(log_message)

    precision = 10
    rounded = False
    result = first / second

    if precision and rounded:
        result = round(result, precision)

    return result
"""

CLEAN_CALCULATOR_PATH = "src/calculator.py"

CLEAN_CALCULATOR_CONTENT = """\
def clamp(value, minimum, maximum):
    \"\"\"Clamp a value between minimum and maximum.\"\"\"

    if value < minimum:
        return minimum

    if value > maximum:
        return maximum

    return value
"""


def _to_base64(content: str) -> str:
    """
    Base64-encode a string for the GitHub Contents API.

    Args:
        content (str): Plain text content.

    Returns:
        str: Base64-encoded string.
    """

    return base64.b64encode(content.encode()).decode()


BUGGY_CALCULATOR_CONTENT_B64 = _to_base64(BUGGY_CALCULATOR_CONTENT)
CLEAN_CALCULATOR_CONTENT_B64 = _to_base64(CLEAN_CALCULATOR_CONTENT)

# Line numbers match the actual file content of BUGGY_CALCULATOR_CONTENT.
# The diff adds `import os` at line 1 and the `divide` function at lines 46-60.
# We place findings on lines that exist in the RIGHT side of the diff.
BUGGY_REVIEW_JSON = json.dumps(
    {
        "summary": "Found issues in calculator.py",
        "comments": [
            {
                "path": "src/calculator.py",
                "line": 1,
                "body": "Unused import `os`.",
                "side": "RIGHT",
            },
            {
                "path": "src/calculator.py",
                "line": 46,
                "body": "Missing return type annotation on `divide`.",
                "side": "RIGHT",
            },
            {
                "path": "src/calculator.py",
                "line": 60,
                "body": "Division by zero when `second` is 0.",
                "side": "RIGHT",
            },
        ],
    }
)

BUGGY_AGENT_RESULT = AgentResult(
    output=BUGGY_REVIEW_JSON,
    is_error=False,
    num_turns=2,
    duration_ms=3000,
    conversation_id=None,
)

CLEAN_REVIEW_JSON = json.dumps(
    {
        "summary": "No issues found in the refactored clamp function.",
        "comments": [],
    }
)

CLEAN_AGENT_RESULT = AgentResult(
    output=CLEAN_REVIEW_JSON,
    is_error=False,
    num_turns=1,
    duration_ms=1500,
    conversation_id=None,
)
