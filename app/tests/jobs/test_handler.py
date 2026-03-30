# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.jobs.handler import DefaultJobHandler, JobHandler


class TestJobHandlerProtocol:
    def test_default_handler_satisfies_protocol(self):
        handler = DefaultJobHandler()
        assert isinstance(handler, JobHandler)


class TestDefaultJobHandler:
    @pytest.mark.asyncio
    async def test_handle_review_delegates_to_run_and_post_review(self):
        handler = DefaultJobHandler()
        mock_result = MagicMock()

        with patch(
            "nominal_code.handlers.review.run_and_post_review",
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

    @pytest.mark.asyncio
    async def test_handle_worker_delegates_to_review_and_fix(self):
        handler = DefaultJobHandler()

        with patch(
            "nominal_code.handlers.worker.review_and_fix",
            new_callable=AsyncMock,
        ) as mock_fn:
            await handler.handle_worker(
                event=MagicMock(),
                prompt="fix this",
                config=MagicMock(),
                platform=MagicMock(),
                conversation_store=MagicMock(),
                namespace="test",
            )

            mock_fn.assert_called_once()
