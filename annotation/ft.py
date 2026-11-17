"""Minimal FreeType 2.x bindings via ctypes.

Only the subset the annotation kernel needs: face management, fractional
char sizes, subpixel transform, glyph load/render, kerning, version query.
Struct layouts follow freetype.h (FT_FaceRec / FT_GlyphSlotRec prefixes).
"""
import ctypes as C

FT_LOAD_DEFAULT = 0
FT_LOAD_NO_HINTING = 1 << 1
FT_RENDER_MODE_NORMAL = 0
FT_KERNING_DEFAULT = 0
FT_FACE_FLAG_KERNING = 1 << 6
FT_PIXEL_MODE_GRAY = 2


class FT_Vector(C.Structure):
    _fields_ = [("x", C.c_long), ("y", C.c_long)]


class FT_Matrix(C.Structure):
    _fields_ = [("xx", C.c_long), ("xy", C.c_long),
                ("yx", C.c_long), ("yy", C.c_long)]


class FT_Generic(C.Structure):
    _fields_ = [("data", C.c_void_p), ("finalizer", C.c_void_p)]


class FT_BBox(C.Structure):
    _fields_ = [("xMin", C.c_long), ("yMin", C.c_long),
                ("xMax", C.c_long), ("yMax", C.c_long)]


class FT_Glyph_Metrics(C.Structure):
    _fields_ = [(n, C.c_long) for n in (
        "width", "height", "horiBearingX", "horiBearingY", "horiAdvance",
        "vertBearingX", "vertBearingY", "vertAdvance")]


class FT_Bitmap(C.Structure):
    _fields_ = [("rows", C.c_uint), ("width", C.c_uint), ("pitch", C.c_int),
                ("buffer", C.POINTER(C.c_ubyte)), ("num_grays", C.c_ushort),
                ("pixel_mode", C.c_ubyte), ("palette_mode", C.c_ubyte),
                ("palette", C.c_void_p)]


class FT_GlyphSlotRec(C.Structure):
    pass


FT_GlyphSlot = C.POINTER(FT_GlyphSlotRec)


class FT_FaceRec(C.Structure):
    _fields_ = [("num_faces", C.c_long), ("face_index", C.c_long),
                ("face_flags", C.c_long), ("style_flags", C.c_long),
                ("num_glyphs", C.c_long),
                ("family_name", C.c_char_p), ("style_name", C.c_char_p),
                ("num_fixed_sizes", C.c_int), ("available_sizes", C.c_void_p),
                ("num_charmaps", C.c_int), ("charmaps", C.c_void_p),
                ("generic", FT_Generic), ("bbox", FT_BBox),
                ("units_per_EM", C.c_ushort),
                ("ascender", C.c_short), ("descender", C.c_short),
                ("height", C.c_short),
                ("max_advance_width", C.c_short),
                ("max_advance_height", C.c_short),
                ("underline_position", C.c_short),
                ("underline_thickness", C.c_short),
                ("glyph", FT_GlyphSlot), ("size", C.c_void_p),
                ("charmap", C.c_void_p)]


FT_GlyphSlotRec._fields_ = [
    ("library", C.c_void_p), ("face", C.POINTER(FT_FaceRec)),
    ("next", FT_GlyphSlot), ("glyph_index", C.c_uint),
    ("generic", FT_Generic), ("metrics", FT_Glyph_Metrics),
    ("linearHoriAdvance", C.c_long), ("linearVertAdvance", C.c_long),
    ("advance", FT_Vector), ("format", C.c_uint),
    ("bitmap", FT_Bitmap), ("bitmap_left", C.c_int),
    ("bitmap_top", C.c_int)]


class FreeTypeError(Exception):
    def __init__(self, func, code):
        super().__init__("%s failed with FT error %d" % (func, code))
        self.code = code


class Library:
    """Loaded libfreetype handle with typed signatures."""

    def __init__(self, soname="libfreetype.so.6"):
        self._lib = C.CDLL(soname)
        L = self._lib
        L.FT_Init_FreeType.argtypes = [C.POINTER(C.c_void_p)]
        L.FT_Init_FreeType.restype = C.c_int
        L.FT_Done_FreeType.argtypes = [C.c_void_p]
        L.FT_New_Face.argtypes = [C.c_void_p, C.c_char_p, C.c_long,
                                  C.POINTER(C.c_void_p)]
        L.FT_New_Face.restype = C.c_int
        L.FT_Done_Face.argtypes = [C.c_void_p]
        L.FT_Set_Char_Size.argtypes = [C.c_void_p, C.c_long, C.c_long,
                                       C.c_uint, C.c_uint]
        L.FT_Set_Char_Size.restype = C.c_int
        L.FT_Set_Transform.argtypes = [C.c_void_p, C.POINTER(FT_Matrix),
                                       C.POINTER(FT_Vector)]
        L.FT_Set_Transform.restype = None  # returns void, not FT_Error
        L.FT_Get_Char_Index.argtypes = [C.c_void_p, C.c_ulong]
        L.FT_Get_Char_Index.restype = C.c_uint
        L.FT_Load_Glyph.argtypes = [C.c_void_p, C.c_uint, C.c_int]
        L.FT_Load_Glyph.restype = C.c_int
        L.FT_Render_Glyph.argtypes = [C.c_void_p, C.c_int]
        L.FT_Render_Glyph.restype = C.c_int
        L.FT_Get_Kerning.argtypes = [C.c_void_p, C.c_uint, C.c_uint,
                                     C.c_uint, C.POINTER(FT_Vector)]
        L.FT_Get_Kerning.restype = C.c_int
        L.FT_Library_Version.argtypes = [C.c_void_p, C.POINTER(C.c_int),
                                         C.POINTER(C.c_int), C.POINTER(C.c_int)]
        self.handle = C.c_void_p()
        self._check(L.FT_Init_FreeType(C.byref(self.handle)), "FT_Init_FreeType")

    def _check(self, code, func):
        if code != 0:
            raise FreeTypeError(func, code)

    def version(self):
        maj, mnr, pat = C.c_int(), C.c_int(), C.c_int()
        self._lib.FT_Library_Version(self.handle, C.byref(maj),
                                     C.byref(mnr), C.byref(pat))
        return (maj.value, mnr.value, pat.value)

    def close(self):
        if self.handle:
            self._lib.FT_Done_FreeType(self.handle)
            self.handle = None
