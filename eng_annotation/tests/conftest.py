"""pytest 共用固件。

字体与缓存目录通过环境变量注入；测试只读取系统字体，不依赖截图。
"""

import os

import pytest

from eng_annotation import AnnotationEngine, RasterSettings
from eng_annotation.font_backend import FontBackend

FONT_PATH = os.environ.get(
    "ENG_ANNOTATION_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
)


@pytest.fixture(scope="session")
def font_path() -> str:
    assert os.path.isfile(FONT_PATH), f"测试字体缺失: {FONT_PATH}"
    return FONT_PATH


@pytest.fixture()
def engine(tmp_path, font_path) -> AnnotationEngine:
    return AnnotationEngine(font_path, cache_dir=str(tmp_path / "glyph_cache"))


@pytest.fixture()
def backend(font_path) -> FontBackend:
    return FontBackend(font_path)


@pytest.fixture(params=[1.0, 1.5, 2.0, 3.0])
def ratios(request):
    return request.param


@pytest.fixture()
def settings_factory():
    def _make(ratio=1.0, hinting=True, subpixel=True) -> RasterSettings:
        return RasterSettings(
            pixel_ratio=ratio, hinting=hinting, subpixel_position=subpixel
        )

    return _make
