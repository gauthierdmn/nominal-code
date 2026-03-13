# type: ignore
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from nominal_code.main import main, setup_logging


class TestSetupLogging:
    def test_setup_logging_default_level_is_info(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("nominal_code.main.logging.basicConfig") as mock_basic:
                setup_logging()

                mock_basic.assert_called_once()
                call_kwargs = mock_basic.call_args.kwargs

                assert call_kwargs["level"] == logging.INFO

    def test_setup_logging_respects_log_level_env(self):
        with patch.dict("os.environ", {"LOG_LEVEL": "DEBUG"}, clear=True):
            with patch("nominal_code.main.logging.basicConfig") as mock_basic:
                setup_logging()

                call_kwargs = mock_basic.call_args.kwargs

                assert call_kwargs["level"] == logging.DEBUG

    def test_setup_logging_warning_level(self):
        with patch.dict("os.environ", {"LOG_LEVEL": "WARNING"}, clear=True):
            with patch("nominal_code.main.logging.basicConfig") as mock_basic:
                setup_logging()

                call_kwargs = mock_basic.call_args.kwargs

                assert call_kwargs["level"] == logging.WARNING

    def test_setup_logging_unknown_level_defaults_to_info(self):
        with patch.dict("os.environ", {"LOG_LEVEL": "NOTALEVEL"}, clear=True):
            with patch("nominal_code.main.logging.basicConfig") as mock_basic:
                setup_logging()

                call_kwargs = mock_basic.call_args.kwargs

                assert call_kwargs["level"] == logging.INFO

    def test_setup_logging_uses_stdout(self):
        with patch("nominal_code.main.logging.basicConfig") as mock_basic:
            setup_logging()

            call_kwargs = mock_basic.call_args.kwargs

            assert call_kwargs["stream"] is sys.stdout


class TestMain:
    def test_main_dispatches_to_cli_main_when_review_arg(self):
        with patch.object(sys, "argv", ["nominal-code", "review", "owner/repo#1"]):
            with patch("nominal_code.commands.cli.cli_main") as mock_cli:
                main()

                mock_cli.assert_called_once()

    def test_main_serve_calls_asyncio_run(self):
        with patch.object(sys, "argv", ["nominal-code", "serve"]):
            with patch("nominal_code.main.setup_logging"):
                with patch(
                    "nominal_code.commands.webhook.server.run_webhook_server",
                    new=MagicMock(),
                ):
                    with patch("nominal_code.main.asyncio.run") as mock_run:
                        main()

                        mock_run.assert_called_once()

    def test_main_serve_handles_keyboard_interrupt_gracefully(self):
        with patch.object(sys, "argv", ["nominal-code", "serve"]):
            with patch("nominal_code.main.setup_logging"):
                with patch(
                    "nominal_code.commands.webhook.server.run_webhook_server",
                    new=MagicMock(),
                ):
                    with patch(
                        "nominal_code.main.asyncio.run",
                        side_effect=KeyboardInterrupt,
                    ):
                        main()

    def test_main_serve_calls_setup_logging_before_asyncio_run(self):
        call_order = []

        with patch.object(sys, "argv", ["nominal-code", "serve"]):
            with patch(
                "nominal_code.main.setup_logging",
                side_effect=lambda: call_order.append("setup_logging"),
            ):
                with patch(
                    "nominal_code.commands.webhook.server.run_webhook_server",
                    new=MagicMock(),
                ):
                    with patch(
                        "nominal_code.main.asyncio.run",
                        side_effect=lambda *a, **kw: call_order.append("asyncio_run"),
                    ):
                        main()

        assert call_order == ["setup_logging", "asyncio_run"]

    def test_main_no_args_exits_with_error(self):
        with patch.object(sys, "argv", ["nominal-code"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_main_unknown_command_exits_with_error(self):
        with patch.object(sys, "argv", ["nominal-code", "bogus"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_main_ci_without_platform_exits_with_error(self):
        with patch.object(sys, "argv", ["nominal-code", "ci"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1


class TestRunWebhookServer:
    @pytest.mark.asyncio
    async def test_run_webhook_server_exits_on_config_error(self):
        with patch(
            "nominal_code.commands.webhook.server.load_config",
            side_effect=ValueError("bad config"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                from nominal_code.commands.webhook.server import run_webhook_server

                await run_webhook_server()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_run_webhook_server_exits_when_no_platforms(self):
        mock_config = MagicMock()
        mock_config.worker = None
        mock_config.reviewer = None
        mock_config.webhook = MagicMock()
        mock_config.webhook.host = "0.0.0.0"
        mock_config.webhook.port = 8080

        with patch(
            "nominal_code.commands.webhook.server.load_config",
            return_value=mock_config,
        ):
            with patch(
                "nominal_code.commands.webhook.server.build_platforms",
                return_value={},
            ):
                with pytest.raises(SystemExit) as exc_info:
                    from nominal_code.commands.webhook.server import run_webhook_server

                    await run_webhook_server()

        assert exc_info.value.code == 1
