# type: ignore
from nominal_code.agent.providers.base import (
    ContextLengthError,
    MissingProviderError,
    ProviderError,
    RateLimitError,
)


class TestProviderError:
    def test_is_exception(self):
        error = ProviderError("something failed")

        assert isinstance(error, Exception)
        assert str(error) == "something failed"


class TestRateLimitError:
    def test_inherits_from_provider_error(self):
        error = RateLimitError("rate limited")

        assert isinstance(error, ProviderError)
        assert isinstance(error, Exception)

    def test_message_preserved(self):
        error = RateLimitError("too many requests")

        assert str(error) == "too many requests"


class TestContextLengthError:
    def test_inherits_from_provider_error(self):
        error = ContextLengthError("context exceeded")

        assert isinstance(error, ProviderError)

    def test_message_preserved(self):
        error = ContextLengthError("context length exceeded")

        assert str(error) == "context length exceeded"


class TestMissingProviderError:
    def test_inherits_from_provider_error(self):
        error = MissingProviderError(
            "anthropic", "anthropic", 'pip install "nominal-code[anthropic]"'
        )

        assert isinstance(error, ProviderError)

    def test_includes_provider_name(self):
        error = MissingProviderError(
            "anthropic", "anthropic", 'pip install "nominal-code[anthropic]"'
        )

        assert "anthropic" in str(error)

    def test_includes_install_instruction(self):
        error = MissingProviderError(
            "openai", "openai", 'pip install "nominal-code[openai]"'
        )

        assert 'pip install "nominal-code[openai]"' in str(error)

    def test_includes_library_name(self):
        error = MissingProviderError(
            "google", "google-genai", 'pip install "nominal-code[google]"'
        )

        assert "google-genai" in str(error)
