# type: ignore
from unittest.mock import MagicMock

import pytest

from nominal_code.platforms.registry import (
    _REGISTRY,
    build_platforms,
    register_platform,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_REGISTRY)
    _REGISTRY.clear()

    yield

    _REGISTRY.clear()
    _REGISTRY.update(saved)


class TestRegisterPlatform:
    def test_register_platform_adds_to_registry(self):
        factory = MagicMock(return_value=None)
        register_platform("test_plat", factory)

        assert "test_plat" in _REGISTRY
        assert _REGISTRY["test_plat"] is factory

    def test_register_platform_duplicate_raises(self):
        factory = MagicMock(return_value=None)
        register_platform("dup", factory)

        with pytest.raises(ValueError, match="already registered"):
            register_platform("dup", factory)

    def test_register_platform_allow_replace(self):
        factory_a = MagicMock(return_value=None)
        factory_b = MagicMock(return_value=None)
        register_platform("replaceable", factory_a)
        register_platform("replaceable", factory_b, allow_replace=True)

        assert _REGISTRY["replaceable"] is factory_b


class TestBuildPlatforms:
    def test_build_platforms_returns_configured(self):
        mock_platform = MagicMock()
        factory = MagicMock(return_value=mock_platform)
        register_platform("active", factory)

        result = build_platforms()

        assert "active" in result
        assert result["active"] is mock_platform

    def test_build_platforms_skips_unconfigured(self):
        factory = MagicMock(return_value=None)
        register_platform("inactive", factory)

        result = build_platforms()

        assert "inactive" not in result

    def test_build_platforms_mixed(self):
        active_platform = MagicMock()
        register_platform("active", MagicMock(return_value=active_platform))
        register_platform("inactive", MagicMock(return_value=None))

        result = build_platforms()

        assert "active" in result
        assert "inactive" not in result
        assert len(result) == 1
