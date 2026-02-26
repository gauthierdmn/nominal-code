from __future__ import annotations

import re


def extract_mention(text: str, bot_username: str) -> str | None:
    """
    Detect an @mention of the bot and extract the prompt that follows it.

    The match is case-insensitive and works with or without a leading ``@``.
    Returns None if the bot is not mentioned.

    Args:
        text (str): The comment body to search.
        bot_username (str): The bot's username (without ``@`` prefix).

    Returns:
        str | None: The extracted prompt after the mention, or None if
            the bot was not mentioned.
    """

    pattern: str = rf"@{re.escape(bot_username)}\b"
    match_: re.Match[str] | None = re.search(pattern, text, re.IGNORECASE)

    if match_ is None:
        return None

    prompt: str = text[match_.end() :].strip()

    return prompt if prompt else None
