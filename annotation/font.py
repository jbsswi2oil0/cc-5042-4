"""FontFace: one FreeType face; rasterizes glyphs at explicit device scale.

Logical size is points (fractional allowed); device scale comes from the
DPI the caller passes per rasterization pass, so a hi-DPI render is a real
re-rasterization at a larger char size, never an upscale of a lo-DPI bitmap.
"""
import ctypes as C
from . import ft
from .glyph import RasterGlyph

_IDENTITY = ft.FT_Matrix(0x10000, 0, 0, 0x10000)


class FontFace:
    def __init__(self, library, path):
        self._lib = library
        self.path = path
        self._face = C.c_void_p()
        library._check(
            library._lib.FT_New_Face(library.handle, path.encode(), 0,
                                     C.byref(self._face)), "FT_New_Face")
        rec = C.cast(self._face, C.POINTER(ft.FT_FaceRec)).contents
        self.family = rec.family_name.decode()
        self.units_per_em = rec.units_per_EM
        self.ascender = rec.ascender
        self.descender = rec.descender
        self.bbox = (rec.bbox.xMin, rec.bbox.yMin, rec.bbox.xMax, rec.bbox.yMax)
        self.has_kerning = bool(rec.face_flags & ft.FT_FACE_FLAG_KERNING)
        self._size64 = None
        self._dpi = None

    def set_size(self, size_pt, dpi):
        """size_pt: fractional points; dpi: device resolution of this pass."""
        size64 = int(round(size_pt * 64))
        dpi_i = int(round(dpi))
        if size64 <= 0:
            raise ValueError("char size must be positive, got %r" % size_pt)
        if (size64, dpi_i) != (self._size64, self._dpi):
            self._lib._check(
                self._lib._lib.FT_Set_Char_Size(self._face, 0, size64,
                                                dpi_i, dpi_i),
                "FT_Set_Char_Size")
            self._size64, self._dpi = size64, dpi_i
        return size64

    def glyph_index(self, ch):
        return self._lib._lib.FT_Get_Char_Index(self._face, ord(ch))

    def kerning(self, left_gi, right_gi):
        """Fractional device-px kerning delta between two glyphs."""
        if not self.has_kerning or not left_gi or not right_gi:
            return 0.0
        vec = ft.FT_Vector()
        self._lib._check(
            self._lib._lib.FT_Get_Kerning(self._face, left_gi, right_gi,
                                          ft.FT_KERNING_DEFAULT, C.byref(vec)),
            "FT_Get_Kerning")
        return vec.x / 64.0

    def rasterize(self, glyph_index, hinting, phase_x=0.0, phase_y=0.0):
        """Render one glyph at the current char size.

        phase_x/phase_y: subpixel offset of the pen in device px (0..1),
        applied through FT_Set_Transform so coverage is positioned honestly
        instead of snapping the pen to whole pixels.
        """
        px64 = int(round(phase_x * 64))
        py64 = int(round(phase_y * 64))
        delta = ft.FT_Vector(px64, -py64)  # device y grows down, FT y grows up
        self._lib._lib.FT_Set_Transform(self._face, C.byref(_IDENTITY),
                                        C.byref(delta))
        flags = ft.FT_LOAD_DEFAULT if hinting else ft.FT_LOAD_NO_HINTING
        self._lib._check(
            self._lib._lib.FT_Load_Glyph(self._face, glyph_index, flags),
            "FT_Load_Glyph")
        face_rec = C.cast(self._face, C.POINTER(ft.FT_FaceRec)).contents
        self._lib._check(
            self._lib._lib.FT_Render_Glyph(face_rec.glyph,
                                           ft.FT_RENDER_MODE_NORMAL),
            "FT_Render_Glyph")
        slot = face_rec.glyph.contents
        bm = slot.bitmap
        if bm.pixel_mode != ft.FT_PIXEL_MODE_GRAY:
            raise RuntimeError("expected grayscale coverage, mode=%d"
                               % bm.pixel_mode)
        w, rows, pitch = bm.width, bm.rows, bm.pitch
        if w and rows:
            raw = C.string_at(bm.buffer, rows * abs(pitch))
            if pitch == w:
                coverage = raw
            else:  # repack rows tightly, dropping pad bytes
                coverage = b"".join(
                    raw[r * abs(pitch): r * abs(pitch) + w] for r in range(rows))
        else:
            coverage = b""
        return RasterGlyph(
            glyph_index=glyph_index, width=w, rows=rows, coverage=coverage,
            left=slot.bitmap_left, top=slot.bitmap_top,
            advance=slot.advance.x / 64.0,
            linear_advance=slot.linearHoriAdvance / 65536.0,
            size64=self._size64, hinting=hinting,
            phase_x64=px64, phase_y64=py64)

    def close(self):
        if self._face:
            self._lib._lib.FT_Done_Face(self._face)
            self._face = None
