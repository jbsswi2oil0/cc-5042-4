"""Engineering annotation kernel.

Owns the document (logical units = reference px at 96 dpi), the device
pixel ratio, and the hinting switch; rasterizes annotations through
FreeType at the true device scale; exposes diagnostics (per-glyph sampling
positions, cumulative rounding drift, cache stats) and an error log that
both the API and the UI feed back into.
"""
import math
import threading
import time

from . import ft
from .cache import GlyphCache
from .font import FontFace
from .layout import LayoutError, layout_spans, parse_markup
from .surface import Surface, coverage_png

BASE_DPI = 96.0
DPR_OPTIONS = [0.75, 1.0, 1.25, 1.5, 2.0, 3.0]
MIN_SIZE_PT, MAX_SIZE_PT = 4.0, 144.0


class KernelError(Exception):
    pass


def parse_color(text):
    t = text.lstrip("#")
    if len(t) == 6:
        t += "ff"
    if len(t) != 8:
        raise KernelError("bad color %r (want #rrggbb or #rrggbbaa)" % text)
    try:
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4, 6))
    except ValueError:
        raise KernelError("bad color %r" % text)


class Annotation:
    _next_id = [1]

    def __init__(self, x, y, size_pt, text, color="#202020"):
        self.id = Annotation._next_id[0]
        Annotation._next_id[0] += 1
        self.x = float(x)          # logical, baseline start
        self.y = float(y)
        self.size_pt = float(size_pt)
        self.text = text
        self.color = color
        self.status = "ok"
        self.message = ""

    def to_dict(self):
        return {"id": self.id, "x": self.x, "y": self.y,
                "size_pt": self.size_pt, "text": self.text,
                "color": self.color, "status": self.status,
                "message": self.message}


class Entity:
    _next_id = [1]

    def __init__(self, kind, coords, color="#808080"):
        self.id = Entity._next_id[0]
        Entity._next_id[0] += 1
        self.kind = kind            # 'line' | 'rect'
        self.coords = [float(v) for v in coords]
        self.color = color

    def to_dict(self):
        return {"id": self.id, "kind": self.kind,
                "coords": self.coords, "color": self.color}


class AnnotationKernel:
    def __init__(self, font_path, width=960, height=600,
                 dpr=1.0, hinting=True):
        self.library = ft.Library()
        self.face = FontFace(self.library, font_path)
        self.cache = GlyphCache()
        self.width = float(width)   # logical
        self.height = float(height)
        self.dpr = float(dpr)
        self.hinting = bool(hinting)
        self.annotations = []
        self.entities = []
        self.errors = []            # feedback log: UI + kernel failures
        self._lock = threading.RLock()
        self._last = None           # (Surface, diagnostics)

    # ---------------------------------------------------------- feedback
    def _log_error(self, where, message):
        entry = {"t": time.strftime("%H:%M:%S"), "where": where,
                 "message": str(message)}
        self.errors.append(entry)
        del self.errors[:-50]
        return entry

    # ---------------------------------------------------------- settings
    def set_dpr(self, dpr):
        dpr = float(dpr)
        if dpr <= 0 or dpr > 8:
            return self._log_error("set_dpr", "rejected dpr %r" % dpr)
        self.dpr = dpr
        return {"dpr": self.dpr, "dpi": self.dpi}

    def set_hinting(self, on):
        self.hinting = bool(on)
        return {"hinting": self.hinting}

    @property
    def dpi(self):
        return BASE_DPI * self.dpr

    # ---------------------------------------------------------- document
    def _validate_annotation(self, x, y, size_pt, text):
        if not (MIN_SIZE_PT <= size_pt <= MAX_SIZE_PT):
            raise KernelError("size %.3g pt outside [%g, %g]"
                              % (size_pt, MIN_SIZE_PT, MAX_SIZE_PT))
        if not text or not text.strip():
            raise KernelError("empty annotation text")
        if len(text) > 512:
            raise KernelError("text too long (%d chars)" % len(text))
        m = 200
        if not (-m <= x <= self.width + m and -m <= y <= self.height + m):
            raise KernelError("position (%.1f, %.1f) outside document"
                              % (x, y))

    def add_annotation(self, x, y, size_pt, text, color="#202020"):
        with self._lock:
            try:
                size_pt = float(size_pt)
                x, y = float(x), float(y)
                self._validate_annotation(x, y, size_pt, text)
                parse_color(color)
                parse_markup(text)          # fail early on bad markup
            except (KernelError, LayoutError, ValueError, TypeError) as e:
                self._log_error("add_annotation", e)
                return None
            ann = Annotation(x, y, size_pt, text, color)
            self.annotations.append(ann)
            return ann.to_dict()

    def move_annotation(self, ann_id, x, y):
        with self._lock:
            ann = self._find(ann_id)
            if ann is None:
                self._log_error("move", "no annotation %r" % ann_id)
                return None
            try:
                x, y = float(x), float(y)
                self._validate_annotation(x, y, ann.size_pt, ann.text)
            except (KernelError, ValueError, TypeError) as e:
                ann.status, ann.message = "error", str(e)
                self._log_error("move #%s" % ann_id, e)
                return None
            ann.x, ann.y = x, y
            ann.status, ann.message = "ok", ""
            return ann.to_dict()

    def update_annotation(self, ann_id, text=None, size_pt=None, color=None):
        with self._lock:
            ann = self._find(ann_id)
            if ann is None:
                self._log_error("update", "no annotation %r" % ann_id)
                return None
            new_text = ann.text if text is None else str(text)
            new_size = ann.size_pt if size_pt is None else float(size_pt)
            new_color = ann.color if color is None else str(color)
            try:
                self._validate_annotation(ann.x, ann.y, new_size, new_text)
                parse_color(new_color)
                parse_markup(new_text)
            except (KernelError, LayoutError, ValueError, TypeError) as e:
                ann.status, ann.message = "error", str(e)
                self._log_error("update #%s" % ann_id, e)
                return None
            ann.text, ann.size_pt, ann.color = new_text, new_size, new_color
            ann.status, ann.message = "ok", ""
            return ann.to_dict()

    def delete_annotation(self, ann_id):
        with self._lock:
            ann = self._find(ann_id)
            if ann is None:
                self._log_error("delete", "no annotation %r" % ann_id)
                return None
            self.annotations.remove(ann)
            return {"deleted": ann.id}

    def add_entity(self, kind, coords, color="#808080"):
        with self._lock:
            if kind not in ("line", "rect") or len(coords) != 4:
                return self._log_error("add_entity",
                                       "bad entity %r %r" % (kind, coords))
            ent = Entity(kind, coords, color)
            self.entities.append(ent)
            return ent.to_dict()

    def _find(self, ann_id):
        for a in self.annotations:
            if a.id == int(ann_id):
                return a
        return None

    # ---------------------------------------------------------- rendering
    def render(self):
        """Rasterize the whole document at the current device scale."""
        with self._lock:
            dev_w = max(1, int(math.ceil(self.width * self.dpr)))
            dev_h = max(1, int(math.ceil(self.height * self.dpr)))
            surf = Surface(dev_w, dev_h)
            for ent in self.entities:
                c = parse_color(ent.color)
                x0, y0, x1, y1 = (v * self.dpr for v in ent.coords)
                if ent.kind == "line":
                    surf.draw_line(x0, y0, x1, y1, c)
                else:
                    surf.draw_rect(x0, y0, x1, y1, c)
            diags = []
            for ann in self.annotations:
                diags.append(self._render_annotation(ann, surf))
            self._last = (surf, diags)
            return surf, diags

    def _render_annotation(self, ann, surf):
        rec = {"id": ann.id, "text": ann.text, "status": ann.status,
               "message": ann.message, "glyphs": []}
        if ann.status == "error":
            return rec
        try:
            spans = parse_markup(ann.text)
            color = parse_color(ann.color)
            layout = layout_spans(
                self.face, self.cache, spans, ann.size_pt, self.dpi,
                self.hinting, ann.x * self.dpr, ann.y * self.dpr)
        except (LayoutError, KernelError, ValueError) as e:
            ann.status, ann.message = "error", str(e)
            self._log_error("render #%s" % ann.id, e)
            rec.update(status="error", message=str(e))
            return rec
        for pg in layout.glyphs:
            surf.blend_coverage(pg.glyph.coverage, pg.glyph.width,
                                pg.glyph.rows, pg.origin_x, pg.origin_y,
                                color)
            rec["glyphs"].append({
                "char": pg.char, "glyph_index": pg.glyph_index,
                "size_pt": round(pg.size_pt, 3),
                "pen": [round(pg.pen_x, 3), round(pg.pen_y, 3)],
                "origin": [pg.origin_x, pg.origin_y],
                "advance": round(pg.advance, 4),
                "linear_advance": round(pg.glyph.linear_advance, 4),
                "left": pg.left, "top": pg.top,
                "width": pg.width, "rows": pg.rows,
                "cache_hit": pg.cache_hit})
        rec.update(
            baseline=[round(ann.x * self.dpr, 3), round(ann.y * self.dpr, 3)],
            pen_final=round(layout.pen_final, 3),
            naive_final=round(layout.naive_final, 3),
            rounding_drift=round(layout.rounding_drift, 3),
            hinting=self.hinting, dpr=self.dpr)
        return rec

    def render_png(self):
        surf, _ = self.render()
        return surf.to_png()

    def glyph_coverage(self, ann_id, seq):
        """Coverage PNG + metrics of one placed glyph (for the viewer)."""
        with self._lock:
            ann = self._find(ann_id)
            if ann is None:
                return None, self._log_error("glyph", "no annotation %r"
                                             % ann_id)
            self.render()
            diags = self._last[1]
            for rec in diags:
                if rec["id"] == ann.id and rec["glyphs"]:
                    if not (0 <= int(seq) < len(rec["glyphs"])):
                        return None, self._log_error(
                            "glyph", "seq %r out of range" % seq)
                    spans = parse_markup(ann.text)
                    layout = layout_spans(
                        self.face, self.cache, spans, ann.size_pt, self.dpi,
                        self.hinting, ann.x * self.dpr, ann.y * self.dpr)
                    pg = layout.glyphs[int(seq)]
                    return coverage_png(pg.glyph), rec["glyphs"][int(seq)]
            return None, self._log_error("glyph", "annotation %s has no"
                                         " rendered glyphs" % ann_id)

    # ---------------------------------------------------------- state
    def controls(self):
        """Control descriptor the UI is built from (kernel drives the UI)."""
        return {
            "tools": [{"id": "select", "label": "选择/移动"},
                      {"id": "add", "label": "添加注记"},
                      {"id": "line", "label": "直线"},
                      {"id": "rect", "label": "矩形"}],
            "dpr_options": DPR_OPTIONS,
            "toggles": [{"id": "hinting", "label": "hinting"},
                        {"id": "sampling", "label": "采样位置"}],
            "size_range": [MIN_SIZE_PT, MAX_SIZE_PT],
        }

    def state(self):
        with self._lock:
            self.render()  # keep diagnostics in sync with current settings
            diags = self._last[1] if self._last else []
            dev = [self.width * self.dpr, self.height * self.dpr]
            return {
                "document": {"width": self.width, "height": self.height,
                             "device_width": dev[0], "device_height": dev[1]},
                "settings": {"dpr": self.dpr, "dpi": self.dpi,
                             "hinting": self.hinting},
                "font": {"family": self.face.family, "path": self.face.path,
                         "units_per_em": self.face.units_per_em,
                         "freetype": self.library.version()},
                "annotations": [a.to_dict() for a in self.annotations],
                "entities": [e.to_dict() for e in self.entities],
                "diagnostics": diags,
                "cache": self.cache.stats(),
                "errors": list(self.errors),
                "controls": self.controls(),
            }

    def close(self):
        self.face.close()
        self.library.close()
