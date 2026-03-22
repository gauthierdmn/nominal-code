# type: ignore
import pytest
from pydantic import ValidationError

from nominal_code.config.policies import FilteringPolicy, RoutingPolicy
from nominal_code.models import EventType


class TestFilteringPolicy:
    def test_defaults_are_empty(self):
        policy = FilteringPolicy()

        assert policy.allowed_users == frozenset()
        assert policy.allowed_repos == frozenset()
        assert policy.pr_title_include_tags == frozenset()
        assert policy.pr_title_exclude_tags == frozenset()

    def test_all_fields_set(self):
        policy = FilteringPolicy(
            allowed_users=frozenset({"alice", "bob"}),
            allowed_repos=frozenset({"owner/repo-a"}),
            pr_title_include_tags=frozenset({"nominalbot"}),
            pr_title_exclude_tags=frozenset({"skip"}),
        )

        assert policy.allowed_users == frozenset({"alice", "bob"})
        assert policy.allowed_repos == frozenset({"owner/repo-a"})
        assert policy.pr_title_include_tags == frozenset({"nominalbot"})
        assert policy.pr_title_exclude_tags == frozenset({"skip"})

    def test_is_frozen(self):
        policy = FilteringPolicy(
            allowed_users=frozenset({"alice"}),
        )

        with pytest.raises(ValidationError):
            policy.allowed_users = frozenset({"bob"})

    def test_equality(self):
        policy_a = FilteringPolicy(
            allowed_users=frozenset({"alice"}),
            allowed_repos=frozenset({"owner/repo"}),
        )
        policy_b = FilteringPolicy(
            allowed_users=frozenset({"alice"}),
            allowed_repos=frozenset({"owner/repo"}),
        )

        assert policy_a == policy_b

    def test_inequality_different_users(self):
        policy_a = FilteringPolicy(allowed_users=frozenset({"alice"}))
        policy_b = FilteringPolicy(allowed_users=frozenset({"bob"}))

        assert policy_a != policy_b


class TestRoutingPolicy:
    def test_defaults_are_empty(self):
        policy = RoutingPolicy()

        assert policy.reviewer_triggers == frozenset()
        assert policy.worker_bot_username == ""
        assert policy.reviewer_bot_username == ""

    def test_all_fields_set(self):
        policy = RoutingPolicy(
            reviewer_triggers=frozenset({EventType.PR_OPENED, EventType.PR_PUSH}),
            worker_bot_username="claude-worker",
            reviewer_bot_username="claude-reviewer",
        )

        assert policy.reviewer_triggers == frozenset(
            {EventType.PR_OPENED, EventType.PR_PUSH},
        )
        assert policy.worker_bot_username == "claude-worker"
        assert policy.reviewer_bot_username == "claude-reviewer"

    def test_is_frozen(self):
        policy = RoutingPolicy(worker_bot_username="claude-worker")

        with pytest.raises(ValidationError):
            policy.worker_bot_username = "other-bot"

    def test_reviewer_triggers_accepts_event_types(self):
        policy = RoutingPolicy(
            reviewer_triggers=frozenset({EventType.PR_OPENED}),
        )

        assert EventType.PR_OPENED in policy.reviewer_triggers
        assert EventType.PR_PUSH not in policy.reviewer_triggers

    def test_equality(self):
        policy_a = RoutingPolicy(
            worker_bot_username="claude-worker",
            reviewer_bot_username="claude-reviewer",
        )
        policy_b = RoutingPolicy(
            worker_bot_username="claude-worker",
            reviewer_bot_username="claude-reviewer",
        )

        assert policy_a == policy_b

    def test_inequality_different_triggers(self):
        policy_a = RoutingPolicy(
            reviewer_triggers=frozenset({EventType.PR_OPENED}),
        )
        policy_b = RoutingPolicy(
            reviewer_triggers=frozenset({EventType.PR_PUSH}),
        )

        assert policy_a != policy_b


class TestPolicyComposition:
    def test_filtering_and_routing_are_independent(self):
        filtering = FilteringPolicy(
            allowed_users=frozenset({"alice"}),
            allowed_repos=frozenset({"owner/repo"}),
        )
        routing = RoutingPolicy(
            reviewer_triggers=frozenset({EventType.PR_OPENED}),
            worker_bot_username="claude-worker",
        )

        assert filtering.allowed_users == frozenset({"alice"})
        assert routing.reviewer_triggers == frozenset({EventType.PR_OPENED})

    def test_override_filtering_with_org_values(self):
        global_filtering = FilteringPolicy(
            allowed_users=frozenset({"alice", "bob"}),
            allowed_repos=frozenset({"owner/repo-a"}),
            pr_title_include_tags=frozenset({"nominalbot"}),
        )

        org_repos = frozenset({"org/repo-x", "org/repo-y"})

        org_filtering = FilteringPolicy(
            allowed_users=global_filtering.allowed_users,
            allowed_repos=org_repos,
            pr_title_include_tags=global_filtering.pr_title_include_tags,
            pr_title_exclude_tags=global_filtering.pr_title_exclude_tags,
        )

        assert org_filtering.allowed_repos == org_repos
        assert org_filtering.allowed_users == global_filtering.allowed_users

    def test_override_routing_with_org_values(self):
        global_routing = RoutingPolicy(
            reviewer_triggers=frozenset({EventType.PR_OPENED}),
            worker_bot_username="claude-worker",
            reviewer_bot_username="claude-reviewer",
        )

        org_routing = RoutingPolicy(
            reviewer_triggers=global_routing.reviewer_triggers,
            worker_bot_username="org-worker-bot",
            reviewer_bot_username=global_routing.reviewer_bot_username,
        )

        assert org_routing.worker_bot_username == "org-worker-bot"
        assert org_routing.reviewer_bot_username == "claude-reviewer"
        assert org_routing.reviewer_triggers == global_routing.reviewer_triggers
