# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.commands.webhook.jobs.handler import DefaultJobHandler


class TestJobHandlerProtocol:
    def test_default_handler_satisfies_protocol(self):
        handler = DefaultJobHandler()
        assert hasattr(handler, "handle_review")


class TestDefaultJobHandler:
    @pytest.mark.asyncio
    async def test_handle_review_delegates_to_run_and_post_review(self):
        handler = DefaultJobHandler()
        mock_result = MagicMock()

        with patch(
            "nominal_code.commands.webhook.jobs.handler.run_and_post_review",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            result = await handler.handle_review(
                event=MagicMock(),
                prompt="review this",
                config=MagicMock(),
                platform=MagicMock(),
                conversation_store=MagicMock(),
                namespace="test",
            )

            mock_fn.assert_called_once()
            assert result is mock_result
