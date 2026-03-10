from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from json_repair import loads as json_repair_loads

from nominal_code.agent.invoke import invoke_agent_stateless
from nominal_code.models import AgentReview, DiffSide, ReviewFinding

if TYPE_CHECKING:
    from pathlib import Path

    from nominal_code.agent.result import AgentResult
    from nominal_code.config import Config

SUMMARY_PATTERN: re.Pattern[str] = re.compile(
    r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"',
)
FALLBACK_MESSAGE: str = (
    "I completed my analysis but failed to produce a structured review. "
    "You can re-trigger the review by mentioning me again. "
    "If the issue persists, contact your administrator."
)
JSON_FIX_SYSTEM_PROMPT: str = (
    "You are a JSON repair tool. You receive malformed JSON and output "
    "ONLY the corrected, valid JSON. Do not add commentary, markdown "
    "fences, or explanations. Preserve all content and structure — fix "
    "only syntax errors."
)
JSON_FIX_PROMPT: str = (
    "The following text is malformed JSON. Common issues include "
    "unescaped double quotes inside string values, trailing commas, "
    "and missing commas. Fix the syntax errors and output ONLY the "
    "corrected JSON.\n\n{broken_json}"
)
JSON_FIX_RETRY_PROMPT: str = (
    "The following JSON has syntax errors. Pay special attention to:\n"
    '- Double quotes inside string values MUST be escaped as \\"\n'
    "- The `suggestion` fields often contain code with double-quoted strings "
    "that need escaping\n"
    "- No trailing commas after the last element in arrays or objects\n\n"
    "The expected structure is:\n"
    '{{"summary": "...", "comments": [{{"path": "...", "line": N, '
    '"body": "...", "suggestion": "optional code"}}]}}\n\n'
    "Fix this JSON and output ONLY valid JSON:\n\n{broken_json}"
)

logger: logging.Logger = logging.getLogger(__name__)


def parse_review_output(output: str) -> AgentReview | None:
    """
    Parse the agent's JSON output into an AgentReview.

    Extracts the JSON object from the output (stripping prose and code
    fences), then uses ``json_repair.loads`` which both validates and
    repairs common JSON syntax errors (unescaped quotes, trailing commas).

    Returns None if the output cannot be parsed into the expected structure.

    Args:
        output (str): Raw text output from the agent.

    Returns:
        AgentReview | None: Parsed result, or None on failure.
    """

    try:
        extracted: str = extract_json_substring(output.strip())
        data: object = json_repair_loads(extracted)

        if not isinstance(data, dict):
            return None

        summary: object = data.get("summary")

        if not isinstance(summary, str) or not summary:
            return None

        raw_comments: object = data.get("comments", [])

        if not isinstance(raw_comments, list):
            return None

        findings: list[ReviewFinding] = [parse_finding(item) for item in raw_comments]

    except ValueError:
        return None

    return AgentReview(summary=summary, findings=findings)


def parse_finding(item: object) -> ReviewFinding:
    """
    Parse a single comment dict into a ReviewFinding.

    Args:
        item (object): A raw comment entry from the agent's JSON output.

    Returns:
        ReviewFinding: The parsed finding.

    Raises:
        ValueError: If the item is missing required fields or has invalid types.
    """

    if not isinstance(item, dict):
        raise ValueError("comment is not a dict")

    path: object = item.get("path")
    line: object = item.get("line")
    body: object = item.get("body")

    if not isinstance(path, str) or not path:
        raise ValueError("invalid path")

    if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
        raise ValueError("invalid line")

    if not isinstance(body, str) or not body:
        raise ValueError("invalid body")

    side_raw: object = item.get("side", DiffSide.RIGHT.value)

    if not isinstance(side_raw, str) or side_raw not in (DiffSide.LEFT, DiffSide.RIGHT):
        raise ValueError("invalid side")

    side: DiffSide = DiffSide(side_raw)

    suggestion_raw: object = item.get("suggestion")

    if suggestion_raw is not None:
        if not isinstance(suggestion_raw, str) or not suggestion_raw:
            raise ValueError("invalid suggestion")

        if side == DiffSide.LEFT:
            raise ValueError("suggestion not allowed on LEFT side")

    suggestion: str | None = suggestion_raw if isinstance(suggestion_raw, str) else None

    start_line_raw: object = item.get("start_line")

    if start_line_raw is not None:
        if (
            isinstance(start_line_raw, bool)
            or not isinstance(start_line_raw, int)
            or start_line_raw <= 0
        ):
            raise ValueError("invalid start_line")

        if start_line_raw > line:
            raise ValueError("start_line must be <= line")

    start_line: int | None = start_line_raw if isinstance(start_line_raw, int) else None

    return ReviewFinding(
        file_path=path,
        line=line,
        body=body,
        side=side,
        suggestion=suggestion,
        start_line=start_line,
    )


def extract_json_substring(text: str) -> str:
    """
    Extract the outermost JSON object from text that may contain prose.

    Finds the first ``{`` and last ``}`` and returns that substring.
    Falls back to the original text if no braces are found.

    Args:
        text (str): Raw text potentially containing a JSON object.

    Returns:
        str: The extracted JSON substring, or the original text.
    """

    first_brace: int = text.find("{")
    last_brace: int = text.rfind("}")

    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        return text

    return text[first_brace : last_brace + 1]


def build_fallback_comment(raw_output: str) -> str:
    """
    Build a user-facing comment when the review JSON cannot be parsed.

    Attempts to extract the ``summary`` field from the broken JSON via
    regex. If found, posts the summary with a note that inline comments
    could not be produced. Otherwise, posts a generic retry message.

    Args:
        raw_output (str): The raw agent output that failed parsing.

    Returns:
        str: The fallback comment to post on the PR.
    """

    match: re.Match[str] | None = SUMMARY_PATTERN.search(raw_output)

    if match:
        summary: str = match.group(1).replace('\\"', '"')

        return (
            f"{summary}\n\n"
            "_I was unable to produce inline review comments for this PR. "
            "You can re-trigger the review by mentioning me again. "
            "If the issue persists, contact your administrator._"
        )

    return FALLBACK_MESSAGE


async def repair_review_output(
    broken_output: str,
    config: Config,
    cwd: Path,
) -> AgentReview | None:
    """
    Attempt to repair malformed review JSON via LLM-based repair.

    Since ``parse_review_output`` already applies extraction and
    ``json_repair.loads``, each LLM attempt gets the full repair
    pipeline for free. Tries two LLM prompts with increasing specificity.

    Args:
        broken_output (str): The raw agent output that failed JSON parsing.
        config (Config): Application configuration (for agent settings).
        cwd (Path): Working directory for the agent.

    Returns:
        AgentReview | None: The parsed review if any strategy succeeds,
            or None if all fail.
    """

    current_json: str = extract_json_substring(broken_output)

    for attempt, prompt_template in enumerate(
        [JSON_FIX_PROMPT, JSON_FIX_RETRY_PROMPT],
        start=1,
    ):
        prompt: str = prompt_template.format(broken_json=current_json)

        logger.info("Attempting LLM JSON repair (attempt %d/2)", attempt)

        fix_result: AgentResult = await invoke_agent_stateless(
            prompt=prompt,
            cwd=cwd,
            system_prompt=JSON_FIX_SYSTEM_PROMPT,
            allowed_tools=[],
            agent_config=config.agent,
        )

        parsed: AgentReview | None = parse_review_output(output=fix_result.output)

        if parsed is not None:
            logger.info("LLM JSON repair succeeded on attempt %d", attempt)

            return parsed

        current_json = extract_json_substring(fix_result.output)

    logger.warning("All JSON repair strategies failed")

    return None
