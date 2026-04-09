# type: ignore
from nominal_code.agent.sub_agents.types import AGENT_TYPE_TOOLS, AgentType


class TestAgentType:
    def test_explore_value(self):
        assert AgentType.EXPLORE == "explore"

    def test_plan_value(self):
        assert AgentType.PLAN == "plan"

    def test_all_types_have_tool_mappings(self):
        for agent_type in AgentType:
            assert agent_type in AGENT_TYPE_TOOLS

    def test_explore_has_read_only_tools(self):
        tools = AGENT_TYPE_TOOLS[AgentType.EXPLORE]

        assert "Read" in tools
        assert "Glob" in tools
        assert "Grep" in tools
        assert "Bash" in tools

    def test_plan_has_read_only_tools(self):
        tools = AGENT_TYPE_TOOLS[AgentType.PLAN]

        assert "Read" in tools
        assert "Glob" in tools
        assert "Grep" in tools
        assert "Bash" in tools

    def test_no_agent_type_includes_agent_tool(self):
        for agent_type, tools in AGENT_TYPE_TOOLS.items():
            assert "Agent" not in tools, (
                f"AgentType.{agent_type} must not include 'Agent' tool"
            )

    def test_no_agent_type_includes_submit_review(self):
        for agent_type, tools in AGENT_TYPE_TOOLS.items():
            assert "submit_review" not in tools, (
                f"AgentType.{agent_type} must not include 'submit_review' tool"
            )
