"""Device-pixel surface with explicit coverage blending and PNG output.

Blending is done in sRGB space with an explicit integer formula:
    out = (src * w + dst * (MAX - w)) / MAX,  w = coverage * src_alpha
No sharpening or filtering is applied anywhere; coverage is used exactly
as FreeType rasterized it.
"""
import struct
import zlib

_MAX = 255 * 255


def blend_pixel(dst, src_r, src_g, src_b, src_a, coverage):
    """One explicit blending step; exposed so tests can verify the formula."""
    w = coverage * src_a
    inv = _MAX - w
    r = (src_r * w + dst[0] * inv + _MAX // 2) // _MAX
    g = (src_g * w + dst[1] * inv + _MAX // 2) // _MAX
    b = (src_b * w + dst[2] * inv + _MAX // 2) // _MAX
    return (r, g, b, 255)


class Surface:
    def __init__(self, width, height, bg=(255, 255, 255, 255)):
        self.width = int(width)
        self.height = int(height)
        px = bytes(bg)
        self.data = bytearray(px * (self.width * self.height))

    def blend_coverage(self, coverage, w, h, ox, oy, color):
        """Composite a grayscale coverage bitmap at integer device origin.

        Origins may be negative (negative left/top bearings): pixels outside
        the surface are clipped by the surface edge only, never by the
        glyph's own advance box.
        """
        sr, sg, sb, sa = color
        for row in range(h):
            y = oy + row
            if y < 0 or y >= self.height:
                continue
            src_base = row * w
            dst_base = (y * self.width) * 4
            for col in range(w):
                x = ox + col
                if x < 0 or x >= self.width:
                    continue
                c = coverage[src_base + col]
                if not c:
                    continue
                i = dst_base + x * 4
                wt = c * sa
                inv = _MAX - wt
                self.data[i] = (sr * wt + self.data[i] * inv + _MAX // 2) // _MAX
                self.data[i + 1] = (sg * wt + self.data[i + 1] * inv
                                    + _MAX // 2) // _MAX
                self.data[i + 2] = (sb * wt + self.data[i + 2] * inv
                                    + _MAX // 2) // _MAX
                self.data[i + 3] = 255

    def draw_line(self, x0, y0, x1, y1, color):
        sr, sg, sb, sa = color
        x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            if 0 <= x0 < self.width and 0 <= y0 < self.height:
                i = (y0 * self.width + x0) * 4
                wt = 255 * sa
                inv = _MAX - wt
                self.data[i] = (sr * wt + self.data[i] * inv + _MAX // 2) // _MAX
                self.data[i + 1] = (sg * wt + self.data[i + 1] * inv
                                    + _MAX // 2) // _MAX
                self.data[i + 2] = (sb * wt + self.data[i + 2] * inv
                                    + _MAX // 2) // _MAX
                self.data[i + 3] = 255
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy; x0 += sx
            if e2 <= dx:
                err += dx; y0 += sy

    def draw_rect(self, x0, y0, x1, y1, color):
        self.draw_line(x0, y0, x1, y0, color)
        self.draw_line(x1, y0, x1, y1, color)
        self.draw_line(x1, y1, x0, y1, color)
        self.draw_line(x0, y1, x0, y0, color)

    def ink_bbox(self):
        """Bounding box of non-background pixels, or None."""
        xs0, ys0, xs1, ys1 = self.width, self.height, -1, -1
        for y in range(self.height):
            base = y * self.width * 4
            for x in range(self.width):
                i = base + x * 4
                if self.data[i] != 255 or self.data[i + 1] != 255 \
                        or self.data[i + 2] != 255:
                    if x < xs0: xs0 = x
                    if x > xs1: xs1 = x
                    if y < ys0: ys0 = y
                    if y > ys1: ys1 = y
        return None if xs1 < 0 else (xs0, ys0, xs1, ys1)

    def to_png(self):
        return encode_png_rgba(self.width, self.height, bytes(self.data))


def _chunk(tag, payload):
    c = struct.pack(">I", len(payload)) + tag + payload
    return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def encode_png_rgba(width, height, rgba):
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgba[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _chunk(b"IEND", b""))


def coverage_png(glyph):
    """Grayscale coverage of one glyph as an RGBA PNG (for the viewer)."""
    w, h = max(glyph.width, 1), max(glyph.rows, 1)
    rgba = bytearray(w * h * 4)
    for y in range(glyph.rows):
        for x in range(glyph.width):
            v = glyph.coverage[y * glyph.width + x]
            i = (y * w + x) * 4
            rgba[i] = rgba[i + 1] = rgba[i + 2] = 255 - v
            rgba[i + 3] = 255
    return encode_png_rgba(w, h, bytes(rgba))
