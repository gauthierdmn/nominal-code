import base64
import os

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_TURNS = 2
DEFAULT_DOCKER_IMAGE = "ghcr.io/gauthierdmn/nominal-code:latest"


def _docker_image() -> str:
    """
    Return the Docker image to use in generated CI configs.

    Reads from ``TEST_DOCKER_IMAGE`` if set, otherwise falls back to
    the default latest image.

    Returns:
        str: The Docker image reference.
    """

    return os.environ.get("TEST_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)


def github_actions_workflow_yaml(
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str:
    """
    Generate a GitHub Actions workflow YAML for the review action.

    Args:
        model (str): The Anthropic model to use.
        max_turns (int): Maximum agent turns.

    Returns:
        str: The workflow YAML content.
    """

    image = _docker_image()

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
        ANTHROPIC_API_KEY: ${{{{ secrets.ANTHROPIC_API_KEY }}}}
        AGENT_MODEL: {model}
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
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str:
    """
    Generate a base64-encoded GitHub Actions workflow YAML.

    Suitable for use with the GitHub Contents API.

    Args:
        model (str): The Anthropic model to use.
        max_turns (int): Maximum agent turns.

    Returns:
        str: Base64-encoded workflow YAML.
    """

    yaml_content = github_actions_workflow_yaml(model=model, max_turns=max_turns)

    return base64.b64encode(yaml_content.encode()).decode()


def gitlab_ci_yaml(
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> str:
    """
    Generate a ``.gitlab-ci.yml`` for the review job.

    Args:
        model (str): The Anthropic model to use.
        max_turns (int): Maximum agent turns.

    Returns:
        str: The GitLab CI YAML content.
    """

    image = _docker_image()

    return f"""\
review:
  image: {image}
  variables:
    AGENT_MODEL: "{model}"
    AGENT_MAX_TURNS: "{max_turns}"
    GIT_CHECKOUT: "false"
  script:
    - cd "${{CI_PROJECT_DIR}}"
    - git checkout "${{CI_MERGE_REQUEST_SOURCE_BRANCH_NAME}}"
    - uv run nominal-code ci gitlab
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
"""
