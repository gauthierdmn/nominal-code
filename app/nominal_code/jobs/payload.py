from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ReviewJob:
    """
    Serializable review job payload.

    Contains everything a review pod needs to execute a review without
    any additional API calls for setup. Auth tokens are not included —
    those come from K8s Secrets as environment variables.

    Attributes:
        platform (str): Platform identifier (``"github"`` or ``"gitlab"``).
        repo_full_name (str): Full repository name (e.g. ``"owner/repo"``).
        pr_number (int): Pull request or merge request number.
        pr_branch (str): Head branch name.
        pr_title (str): Pull request title.
        event_type (str): The ``EventType`` value that produced this job.
        is_comment_event (bool): True for comment events, False for lifecycle.
        author_username (str): Comment author (empty for lifecycle events).
        comment_body (str): Raw comment body (empty for lifecycle events).
        comment_id (int): Unique comment identifier on the platform.
        diff_hunk (str): Diff hunk context around the comment.
        file_path (str): File path the comment is attached to.
        discussion_id (str): GitLab discussion ID for threaded replies.
        prompt (str): Extracted prompt after mention parsing.
        pr_author (str): PR/MR author username (lifecycle events).
        bot_type (str): Bot personality (``"reviewer"`` or ``"worker"``).
    """

    platform: str
    repo_full_name: str
    pr_number: int
    pr_branch: str
    pr_title: str
    event_type: str
    is_comment_event: bool
    author_username: str
    comment_body: str
    comment_id: int
    diff_hunk: str
    file_path: str
    discussion_id: str
    prompt: str
    pr_author: str
    bot_type: str

    def serialize(self) -> str:
        """
        Serialize the job to a JSON string.

        Returns:
            str: JSON representation of all fields.
        """

        return json.dumps(asdict(self))

    @classmethod
    def deserialize(cls, data: str) -> ReviewJob:
        """
        Deserialize a JSON string into a ReviewJob.

        Args:
            data (str): JSON string produced by ``serialize()``.

        Returns:
            ReviewJob: The reconstructed job instance.

        Raises:
            json.JSONDecodeError: If the input is not valid JSON.
            TypeError: If required fields are missing.
        """

        return cls(**json.loads(data))
