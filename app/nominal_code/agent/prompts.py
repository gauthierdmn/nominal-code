from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.workspace.git import GitWorkspace

NOMINAL_CONFIG_DIR: str = ".nominal"
REPO_GUIDELINES_PATH: Path = Path(NOMINAL_CONFIG_DIR) / "guidelines.md"
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
}


def resolve_guidelines(
    repo_path: Path,
    default_guidelines: str,
    language_guidelines: dict[str, str],
    file_paths: list[Path],
) -> str:
    """
    Compose effective guidelines from general and language-specific sources.

    Resolution order for general guidelines: ``.nominal/guidelines.md`` in the
    repository overrides the default. For each detected language: ``.nominal/{lang}.md``
    overrides the built-in ``prompts/languages/{lang}.md``.

    Args:
        repo_path (Path): Absolute path to the repository root.
        default_guidelines (str): Fallback general guidelines from config.
        language_guidelines (dict[str, str]): Built-in language guidelines
            keyed by language name.
        file_paths (list[Path]): File paths used to detect relevant languages.

    Returns:
        str: The composed guidelines string.
    """

    guidelines: list[str] = []
    repo_guidelines: str = _load_repo_guidelines(repo_path) or default_guidelines

    if repo_guidelines:
        guidelines.append(repo_guidelines)

    for language in _detect_languages(file_paths):
        repo_lang_guidelines: str = _load_repo_language_guidelines(
            repo_path, language
        ) or language_guidelines.get(language, "")

        if repo_lang_guidelines:
            guidelines.append(repo_lang_guidelines)

    return "\n\n".join(guidelines)


def resolve_system_prompt(
    workspace: GitWorkspace,
    config: Config,
    bot_system_prompt: str,
    file_paths: list[Path],
) -> str:
    """
    Resolve guidelines and compose the full system prompt.

    Args:
        workspace (GitWorkspace): The workspace with the cloned repo.
        config (Config): Application configuration.
        bot_system_prompt (str): The bot-specific base system prompt.
        file_paths (list[Path]): File paths used to detect relevant languages.

    Returns:
        str: The combined system prompt with guidelines.
    """

    guidelines: str = resolve_guidelines(
        repo_path=workspace.repo_path,
        default_guidelines=config.coding_guidelines,
        language_guidelines=config.language_guidelines,
        file_paths=file_paths,
    )

    return bot_system_prompt + "\n\n" + guidelines


def _load_repo_guidelines(repo_path: Path) -> str:
    """
    Load repo-level coding guidelines from the repository root.

    Looks for a `.nominal/guidelines.md` file in the given repo path.
    Returns its contents if found, otherwise returns an empty string.

    Args:
        repo_path (Path): Absolute path to the repository root.

    Returns:
        str: The guidelines content, or empty string if not found.
    """

    full_path: Path = repo_path / REPO_GUIDELINES_PATH

    if not full_path.is_file():
        return ""

    return full_path.read_text(encoding="utf-8").strip()


def _detect_languages(file_paths: list[Path]) -> set[str]:
    """
    Detect programming languages from file paths using their extensions.

    Args:
        file_paths (list[Path]): File paths to inspect.

    Returns:
        set[str]: Detected language names.
    """

    languages: set[str] = set()

    for file_path in file_paths:
        extension: str = file_path.suffix.lower()
        language: str | None = EXTENSION_TO_LANGUAGE.get(extension)

        if language:
            languages.add(language)

    return languages


def _load_repo_language_guidelines(repo_path: Path, language: str) -> str:
    """
    Load a language-specific guideline file from the repository's ``.nominal/`` dir.

    Looks for ``.nominal/{language}.md`` in the given repo path.

    Args:
        repo_path (Path): Absolute path to the repository root.
        language (str): Language name (e.g. ``python``).

    Returns:
        str: The guideline content, or empty string if not found.
    """

    full_path: Path = repo_path / NOMINAL_CONFIG_DIR / "languages" / f"{language}.md"

    if not full_path.is_file():
        return ""

    return full_path.read_text(encoding="utf-8").strip()
