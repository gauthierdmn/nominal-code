from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.workspace.git import GitWorkspace

NOMINAL_CONFIG_DIR: str = ".nominal"
REPO_GUIDELINES_PATH: str = os.path.join(NOMINAL_CONFIG_DIR, "guidelines.md")
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
}


def load_repo_guidelines(repo_path: str) -> str:
    """
    Load repo-level coding guidelines from the repository root.

    Looks for a `.nominal/guidelines.md` file in the given repo path.
    Returns its contents if found, otherwise returns an empty string.

    Args:
        repo_path (str): Absolute path to the repository root.

    Returns:
        str: The guidelines content, or empty string if not found.
    """

    full_path: str = os.path.join(repo_path, REPO_GUIDELINES_PATH)

    if not os.path.isfile(full_path):
        return ""

    with open(full_path, encoding="utf-8") as guidelines_file:
        return guidelines_file.read().strip()


def detect_languages(file_paths: list[str]) -> list[str]:
    """
    Detect programming languages from file paths using their extensions.

    Returns a sorted, deduplicated list of language names.

    Args:
        file_paths (list[str]): File paths to inspect.

    Returns:
        list[str]: Detected language names, sorted alphabetically.
    """

    languages: set[str] = set()

    for file_path in file_paths:
        extension: str = os.path.splitext(file_path)[1].lower()
        language: str | None = EXTENSION_TO_LANGUAGE.get(extension)

        if language:
            languages.add(language)

    return sorted(languages)


def load_repo_language_guidelines(repo_path: str, language: str) -> str:
    """
    Load a language-specific guideline file from the repository's ``.nominal/`` dir.

    Looks for ``.nominal/{language}.md`` in the given repo path.

    Args:
        repo_path (str): Absolute path to the repository root.
        language (str): Language name (e.g. ``python``).

    Returns:
        str: The guideline content, or empty string if not found.
    """

    full_path: str = os.path.join(
        repo_path,
        NOMINAL_CONFIG_DIR,
        "languages",
        f"{language}.md",
    )

    if not os.path.isfile(full_path):
        return ""

    with open(full_path, encoding="utf-8") as guidelines_file:
        return guidelines_file.read().strip()


def resolve_guidelines(
    repo_path: str,
    default_guidelines: str,
    language_guidelines: dict[str, str],
    file_paths: list[str],
) -> str:
    """
    Compose effective guidelines from general and language-specific sources.

    Resolution order for general guidelines: ``.nominal/guidelines.md`` in the
    repository overrides the default. For each detected language: ``.nominal/{lang}.md``
    overrides the built-in ``prompts/languages/{lang}.md``.

    Args:
        repo_path (str): Absolute path to the repository root.
        default_guidelines (str): Fallback general guidelines from config.
        language_guidelines (dict[str, str]): Built-in language guidelines
            keyed by language name.
        file_paths (list[str]): File paths used to detect relevant languages.

    Returns:
        str: The composed guidelines string.
    """

    parts: list[str] = []

    repo_general: str = load_repo_guidelines(repo_path)
    general: str = repo_general if repo_general else default_guidelines

    if general:
        parts.append(general)

    for language in detect_languages(file_paths):
        repo_lang: str = load_repo_language_guidelines(repo_path, language)
        lang_content: str = (
            repo_lang
            if repo_lang
            else language_guidelines.get(
                language,
                "",
            )
        )

        if lang_content:
            parts.append(lang_content)

    return "\n\n".join(parts)


def build_system_prompt(system_prompt: str, guidelines: str) -> str:
    """
    Combine the system prompt and guidelines into a single string.

    Either part may be empty; empty parts are skipped.

    Args:
        system_prompt (str): The base system prompt text.
        guidelines (str): The composed guidelines text.

    Returns:
        str: The combined system prompt, or empty string if both are empty.
    """

    parts: list[str] = [part for part in (system_prompt, guidelines) if part]

    return "\n\n".join(parts)


def resolve_system_prompt(
    workspace: GitWorkspace,
    config: Config,
    bot_system_prompt: str,
    file_paths: list[str],
) -> str:
    """
    Resolve guidelines and compose the full system prompt.

    Combines ``resolve_guidelines`` and ``build_system_prompt`` into a single
    call to avoid duplicating the pattern in every handler.

    Args:
        workspace (GitWorkspace): The workspace with the cloned repo.
        config (Config): Application configuration.
        bot_system_prompt (str): The bot-specific base system prompt.
        file_paths (list[str]): File paths used to detect relevant languages.

    Returns:
        str: The combined system prompt with guidelines.
    """

    effective_guidelines: str = resolve_guidelines(
        workspace.repo_path,
        config.coding_guidelines,
        config.language_guidelines,
        file_paths,
    )

    return build_system_prompt(bot_system_prompt, effective_guidelines)
