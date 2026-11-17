"""Text layout: markup parsing and fractional-pen glyph placement.

The pen is a float in device px and is NEVER rounded per glyph; only the
rasterization phase handed to FreeType is quantized to 1/64 px. The layout
also computes what a naive per-glyph integer rounding would have produced,
so the cumulative line-length error is measured and reported, not ignored.
"""
import math
from dataclasses import dataclass, field

SUP_SCALE, SUP_SHIFT_EM = 0.7, 0.35
SUB_SCALE, SUB_SHIFT_EM = 0.7, -0.20


class LayoutError(Exception):
    pass


@dataclass(frozen=True)
class Span:
    text: str
    scale: float = 1.0        # relative to base size
    shift_em: float = 0.0     # baseline shift in ems of the BASE size


def parse_markup(text):
    """Plain text plus ^{sup} and ~{sub}; \\ escapes ^ ~ \\."""
    spans, buf, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            buf.append(text[i + 1]); i += 2; continue
        if c in "^~" and i + 1 < n and text[i + 1] == "{":
            j = text.find("}", i + 2)
            if j < 0:
                raise LayoutError("unclosed '%s{' markup" % c)
            if buf:
                spans.append(Span("".join(buf))); buf = []
            body = text[i + 2:j]
            if not body:
                raise LayoutError("empty super/subscript")
            if c == "^":
                spans.append(Span(body, SUP_SCALE, SUP_SHIFT_EM))
            else:
                spans.append(Span(body, SUB_SCALE, SUB_SHIFT_EM))
            i = j + 1; continue
        buf.append(c); i += 1
    if buf:
        spans.append(Span("".join(buf)))
    if not spans:
        raise LayoutError("empty text")
    return spans


@dataclass
class PlacedGlyph:
    char: str
    glyph_index: int
    size_pt: float
    pen_x: float            # float device-px pen position BEFORE this glyph
    pen_y: float            # float device-px baseline for this glyph
    origin_x: int           # integer device-px coverage origin (pen+left)
    origin_y: int
    advance: float          # fractional device px actually used
    naive_advance: int      # what per-glyph rounding would have used
    left: int
    top: int
    width: int
    rows: int
    cache_hit: bool
    glyph: object = field(repr=False)  # RasterGlyph


@dataclass
class Layout:
    glyphs: list
    pen_final: float        # fractional device-px line length we used
    naive_final: float      # line length if each advance were rounded
    spans: list

    @property
    def rounding_drift(self):
        """Cumulative line-length error of naive per-glyph rounding."""
        return self.naive_final - self.pen_final


def layout_spans(face, cache, spans, base_size_pt, dpi, hinting,
                 origin_x=0.0, origin_y=0.0):
    """Place glyphs along a baseline starting at float device-px origin."""
    base_dev = base_size_pt * dpi / 72.0   # 1 em of the base size, device px
    pen_x = origin_x
    glyphs = []
    naive = 0.0
    for span in spans:
        size_pt = base_size_pt * span.scale
        size64 = face.set_size(size_pt, dpi)
        baseline_y = origin_y - span.shift_em * base_dev
        prev_gi = 0
        for ch in span.text:
            gi = face.glyph_index(ch)
            if gi == 0:
                raise LayoutError("no glyph for U+%04X %r" % (ord(ch), ch))
            pen_x += face.kerning(prev_gi, gi)
            phase_x = pen_x - math.floor(pen_x)
            phase_y = baseline_y - math.floor(baseline_y)
            key = cache.key(gi, size64, hinting, phase_x, phase_y)
            hit = True
            g = cache.get(key)
            if g is None:
                hit = False
                g = cache.put(key, face.rasterize(gi, hinting,
                                                  phase_x, phase_y))
            ox = math.floor(pen_x) + g.left
            oy = math.floor(baseline_y) - g.top
            glyphs.append(PlacedGlyph(
                char=ch, glyph_index=gi, size_pt=size_pt,
                pen_x=pen_x, pen_y=baseline_y, origin_x=ox, origin_y=oy,
                advance=g.advance, naive_advance=int(round(g.advance)),
                left=g.left, top=g.top, width=g.width, rows=g.rows,
                cache_hit=hit, glyph=g))
            pen_x += g.advance
            naive += int(round(g.advance))
            prev_gi = gi
    return Layout(glyphs=glyphs, pen_final=pen_x - origin_x,
                  naive_final=naive, spans=spans)
