import base64
import os

from nominal_code.config import ProviderConfig
from nominal_code.llm.registry import PROVIDERS
from nominal_code.models import ProviderName

DEFAULT_MAX_TURNS = 2
DEFAULT_DOCKER_IMAGE = "ghcr.io/gauthierdmn/nominal-code:latest"
DEFAULT_PROVIDER = "anthropic"

TEST_MODEL_OVERRIDES: dict[ProviderName, str] = {
    ProviderName.ANTHROPIC: "claude-3-haiku-20240307",
    ProviderName.OPENAI: "gpt-4.1-nano",
    ProviderName.GOOGLE: "gemini-2.5-flash-lite",
}


def _docker_image() -> str:
    """
    Return the Docker image to use in generated CI configs.

    Reads from ``TEST_DOCKER_IMAGE`` if set, otherwise falls back to
    the default latest image.

    Returns:
        str: The Docker image reference.
    """

    return os.environ.get("TEST_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)


def _provider() -> str:
    """
    Return the provider to use in generated CI configs.

    Reads from ``TEST_PROVIDER`` if set, otherwise falls back to
    ``anthropic``.

    Returns:
        str: The provider name.
    """

    return os.environ.get("TEST_PROVIDER", DEFAULT_PROVIDER)


def _provider_defaults(provider: str) -> ProviderConfig:
    """
    Return the provider config for a given provider name.

    Uses a cheaper test model when available, falling back to the
    production default.

    Args:
        provider (str): The provider name (e.g. ``anthropic``, ``openai``).

    Returns:
        ProviderConfig: The provider-specific configuration.

    Raises:
        ValueError: If the provider is not recognized.
    """

    provider_name = ProviderName(provider)
    defaults = PROVIDERS[provider_name]
    test_model = TEST_MODEL_OVERRIDES.get(provider_name)

    if test_model:
        return defaults.model_copy(update={"model": test_model})

    return defaults


def github_actions_workflow_yaml(
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str:
    """
    Generate a GitHub Actions workflow YAML for the review action.

    Uses ``TEST_PROVIDER`` and ``TEST_DOCKER_IMAGE`` env vars to select
    the provider and image. Defaults to anthropic with the all-in-one image.

    Args:
        max_turns (int): Maximum agent turns.

    Returns:
        str: The workflow YAML content.
    """

    image = _docker_image()
    provider = _provider()
    defaults = _provider_defaults(provider)

    provider_line = ""

    if provider != DEFAULT_PROVIDER:
        provider_line = f"\n        AGENT_PROVIDER: {provider}"

    return f"""\
name: nominal-code-review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    container:
      image: {image}
      env:
        {defaults.api_key_env}: ${{{{ secrets.{defaults.api_key_env} }}}}{provider_line}
        AGENT_MODEL: {defaults.model}
        AGENT_MAX_TURNS: "{max_turns}"
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Run review
        env:
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: cd /app && uv run nominal-code ci github
"""


def github_actions_workflow_base64(
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str:
    """
    Generate a base64-encoded GitHub Actions workflow YAML.

    Suitable for use with the GitHub Contents API.

    Args:
        max_turns (int): Maximum agent turns.

    Returns:
        str: Base64-encoded workflow YAML.
    """

    yaml_content = github_actions_workflow_yaml(max_turns=max_turns)

    return base64.b64encode(yaml_content.encode()).decode()


def gitlab_ci_yaml(
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str:
    """
    Generate a ``.gitlab-ci.yml`` for the review job.

    Uses ``TEST_PROVIDER`` and ``TEST_DOCKER_IMAGE`` env vars to select
    the provider and image. Defaults to anthropic with the all-in-one image.

    Args:
        max_turns (int): Maximum agent turns.

    Returns:
        str: The GitLab CI YAML content.
    """

    image = _docker_image()
    provider = _provider()
    defaults = _provider_defaults(provider)

    provider_line = ""

    if provider != DEFAULT_PROVIDER:
        provider_line = f'\n    AGENT_PROVIDER: "{provider}"'

    return f"""\
review:
  image: {image}
  variables:
    AGENT_MODEL: "{defaults.model}"
    AGENT_MAX_TURNS: "{max_turns}"{provider_line}
    GIT_CHECKOUT: "false"
  script:
    - cd "${{CI_PROJECT_DIR}}"
    - git checkout "${{CI_MERGE_REQUEST_SOURCE_BRANCH_NAME}}"
    - uv run nominal-code ci gitlab
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
"""
