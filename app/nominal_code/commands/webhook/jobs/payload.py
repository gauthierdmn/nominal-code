from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from nominal_code.models import EventType
from nominal_code.platforms.base import (
    CommentEvent,
    LifecycleEvent,
    PlatformName,
    PullRequestEvent,
)


@dataclass(frozen=True)
class JobPayload:
    """
    Serializable job payload wrapping a platform event.

    Contains the event needed to execute a review job. The mention
    prompt (if any) lives on the ``CommentEvent`` itself. Auth tokens
    are not included — those come from K8s Secrets as environment
    variables.

    Attributes:
        event (CommentEvent | LifecycleEvent): The platform event.
        namespace (str): Logical namespace for job isolation and
            attribution. Empty string when unused.
        extra_env (dict[str, str]): Additional environment variables to
            inject into the job container. Empty dict when unused.
    """

    event: CommentEvent | LifecycleEvent
    namespace: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)

    def serialize(self) -> str:
        """
        Serialize the payload to a JSON string.

        Includes an ``is_comment_event`` discriminator so that
        ``deserialize()`` can reconstruct the correct event type.

        Returns:
            str: JSON representation of all fields.
        """

        event_dict: dict[str, object] = asdict(obj=self.event)
        event_dict["is_comment_event"] = isinstance(self.event, CommentEvent)

        return json.dumps(
            {
                "event": event_dict,
                "namespace": self.namespace,
                "extra_env": self.extra_env,
            }
        )

    @classmethod
    def deserialize(cls, data: str) -> JobPayload:
        """
        Deserialize a JSON string into a JobPayload.

        Args:
            data (str): JSON string produced by ``serialize()``.

        Returns:
            JobPayload: The reconstructed payload instance.

        Raises:
            json.JSONDecodeError: If the input is not valid JSON.
            TypeError: If required fields are missing.
            KeyError: If required fields are missing.
        """

        json_data: Any = json.loads(data)
        event_data: dict[str, Any] = json_data["event"]
        is_comment: bool = event_data.pop("is_comment_event")

        event: PullRequestEvent

        pr_number: int = int(str(event_data["pr_number"]))

        if is_comment:
            mention_prompt_val: object = event_data.get("mention_prompt")
            mention_prompt: str | None = (
                str(mention_prompt_val) if mention_prompt_val is not None else None
            )

            event = CommentEvent(
                platform=PlatformName(str(event_data["platform"])),
                repo_full_name=str(event_data["repo_full_name"]),
                pr_number=pr_number,
                pr_branch=str(event_data["pr_branch"]),
                pr_title=str(event_data.get("pr_title", "")),
                event_type=EventType(str(event_data["event_type"])),
                clone_url=str(event_data.get("clone_url", "")),
                comment_id=int(str(event_data.get("comment_id", 0))),
                author_username=str(event_data.get("author_username", "")),
                body=str(event_data.get("body", "")),
                diff_hunk=str(event_data.get("diff_hunk", "")),
                file_path=str(event_data.get("file_path", "")),
                discussion_id=str(event_data.get("discussion_id", "")),
                mention_prompt=mention_prompt,
            )
        else:
            event = LifecycleEvent(
                platform=PlatformName(str(event_data["platform"])),
                repo_full_name=str(event_data["repo_full_name"]),
                pr_number=pr_number,
                pr_branch=str(event_data["pr_branch"]),
                pr_title=str(event_data.get("pr_title", "")),
                event_type=EventType(str(event_data["event_type"])),
                clone_url=str(event_data.get("clone_url", "")),
                pr_author=str(event_data.get("pr_author", "")),
            )

        return cls(
            event=event,
            namespace=str(json_data.get("namespace", "")),
            extra_env=json_data.get("extra_env", {}),
        )
