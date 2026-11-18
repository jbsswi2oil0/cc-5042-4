"""字形覆盖缓存。

缓存的是 FreeType 真实栅格化产出的灰度覆盖（.npy 位图 + JSON 度量），
不是截图也不是放大产物。缓存键包含字体身份、设备像素字号、hinting
开关与亚像素相位，因此任何一项变化都会触发重新栅格化。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict

import numpy as np

from .font_backend import FontBackend
from .models import GlyphCoverage


class GlyphCache:
    def __init__(self, backend: FontBackend, cache_dir: str | None = None) -> None:
        self.backend = backend
        self.cache_dir = cache_dir
        self._memory: dict[str, GlyphCoverage] = {}
        self.hits = 0
        self.misses = 0
        if cache_dir is not None:
            os.makedirs(cache_dir, exist_ok=True)

    def key(
        self,
        codepoint: int,
        pixel_size_px: float,
        hinting: bool,
        subpixel_frac_x: float,
    ) -> str:
        stamp = f"{os.path.realpath(self.backend.font_path)}:" \
                f"{os.path.getmtime(self.backend.font_path):.0f}"
        raw = f"{stamp}|{codepoint}|{pixel_size_px:.4f}|{int(hinting)}|" \
              f"{subpixel_frac_x:.4f}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def get(
        self,
        codepoint: int,
        pixel_size_px: float,
        hinting: bool,
        subpixel_frac_x: float = 0.0,
    ) -> GlyphCoverage:
        key = self.key(codepoint, pixel_size_px, hinting, subpixel_frac_x)
        if key in self._memory:
            self.hits += 1
            return self._memory[key]

        disk = self._load_disk(key)
        if disk is not None:
            self._memory[key] = disk
            self.hits += 1
            return disk

        self.misses += 1
        cov = self.backend.rasterize(
            codepoint, pixel_size_px, hinting, subpixel_frac_x
        )
        self._memory[key] = cov
        self._store_disk(key, cov)
        return cov

    def _paths(self, key: str) -> tuple[str, str]:
        assert self.cache_dir is not None
        return (
            os.path.join(self.cache_dir, f"{key}.npy"),
            os.path.join(self.cache_dir, f"{key}.json"),
        )

    def _load_disk(self, key: str) -> GlyphCoverage | None:
        if self.cache_dir is None:
            return None
        npy_path, json_path = self._paths(key)
        if not (os.path.isfile(npy_path) and os.path.isfile(json_path)):
            return None
        try:
            coverage = np.load(npy_path)
            with open(json_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError, KeyError):
            return None
        return GlyphCoverage(
            codepoint=meta["codepoint"],
            glyph_index=meta["glyph_index"],
            pixel_size_px=meta["pixel_size_px"],
            coverage=coverage,
            bitmap_left_px=meta["bitmap_left_px"],
            bitmap_top_px=meta["bitmap_top_px"],
            advance_px=meta["advance_px"],
            x_min_bearing_px=meta["x_min_bearing_px"],
            y_max_bearing_px=meta["y_max_bearing_px"],
            hinted=meta["hinted"],
            subpixel_frac_x=meta.get("subpixel_frac_x", 0.0),
        )

    def _store_disk(self, key: str, cov: GlyphCoverage) -> None:
        if self.cache_dir is None:
            return
        npy_path, json_path = self._paths(key)
        np.save(npy_path, cov.coverage)
        meta = asdict(cov)
        meta.pop("coverage")
        tmp = json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, json_path)

    def __len__(self) -> int:
        return len(self._memory)
