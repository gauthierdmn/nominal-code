import nominal_code.platforms.github as _github  # noqa: F401
import nominal_code.platforms.gitlab as _gitlab  # noqa: F401
from nominal_code.platforms.registry import build_platforms

__all__: list[str] = ["build_platforms"]
