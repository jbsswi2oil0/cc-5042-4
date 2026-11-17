"""Glyph raster data. Coverage, advance, baseline and bounds are kept as
independent fields so negative bearings and zero-advance marks survive."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RasterGlyph:
    glyph_index: int
    width: int               # coverage bitmap width, device px
    rows: int                # coverage bitmap height, device px
    coverage: bytes          # grayscale 0..255, row-major, len == width*rows
    left: int                # px from pen origin to bitmap left edge; may be < 0
    top: int                 # px from baseline up to bitmap top edge
    advance: float           # pen advance, device px, fractional (as hinted)
    linear_advance: float    # design (unhinted) advance, device px
    size64: int              # 26.6 char size this was rasterized at
    hinting: bool
    phase_x64: int           # subpixel render offset, 1/64 device px
    phase_y64: int

    def ink_pixels(self):
        return sum(1 for b in self.coverage if b)

    def distinct_grays(self):
        return len(set(self.coverage))
