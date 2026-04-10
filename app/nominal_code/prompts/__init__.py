from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

PROMPTS_PACKAGE: str = "nominal_code.prompts"


def load_prompt(name: str) -> str:
    """
    Load a bundled prompt file by relative path.

    Uses ``importlib.resources`` so prompts are always resolved from the
    installed package, regardless of the current working directory.

    Args:
        name (str): Relative path within the prompts package
            (e.g. ``"reviewer_prompt.md"`` or ``"sub_agents/explore.md"``).

    Returns:
        str: The prompt text, stripped of leading/trailing whitespace.
    """

    resource: Traversable = files(PROMPTS_PACKAGE).joinpath(name)

    return resource.read_text(encoding="utf-8").strip()


def load_bundled_language_guidelines() -> dict[str, str]:
    """
    Load all bundled language guideline files from the package.

    Each ``.md`` file in the ``languages/`` subdirectory becomes an entry
    keyed by its stem (e.g. ``"python"`` for ``python.md``).

    Returns:
        dict[str, str]: Language name to guideline content mapping.
    """

    languages_dir: Traversable = files(PROMPTS_PACKAGE).joinpath("languages")
    guidelines: dict[str, str] = {}

    for resource in languages_dir.iterdir():
        if resource.name.endswith(".md"):
            content: str = resource.read_text(encoding="utf-8").strip()

            if content:
                guidelines[resource.name.removesuffix(".md")] = content

    return guidelines
