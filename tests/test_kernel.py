"""Acceptance tests for the annotation rendering kernel.

Run: python3 -m unittest discover -s tests -v   (from /workspace)
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from annotation import ft
from annotation.cache import GlyphCache
from annotation.font import FontFace
from annotation.kernel import AnnotationKernel
from annotation.layout import parse_markup, layout_spans, LayoutError
from annotation.surface import Surface, blend_pixel

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def make_kernel(**kw):
    kw.setdefault("dpr", 1.0)
    return AnnotationKernel(FONT, width=800, height=500, **kw)


class FontFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ft.Library()
        cls.face = FontFace(cls.lib, FONT)

    @classmethod
    def tearDownClass(cls):
        cls.face.close()
        cls.lib.close()


class TestReferenceGlyph(FontFixture):
    """Reference glyph: metrics and coverage against known DejaVu values."""

    def test_capital_H_metrics(self):
        self.face.set_size(12.0, 96)
        gi = self.face.glyph_index('H')
        g = self.face.rasterize(gi, hinting=True)
        self.assertAlmostEqual(g.advance, 12.0, places=2)
        self.assertEqual(g.top, 12)          # cap height sits on baseline
        self.assertEqual(g.left, 1)
        self.assertEqual((g.width, g.rows), (10, 12))
        self.assertGreater(g.ink_pixels(), 0)

    def test_linear_advance_independent_of_hinting(self):
        self.face.set_size(12.0, 96)
        gi = self.face.glyph_index('H')
        hinted = self.face.rasterize(gi, hinting=True)
        unhinted = self.face.rasterize(gi, hinting=False)
        self.assertAlmostEqual(hinted.linear_advance,
                               unhinted.linear_advance, places=4)
        self.assertAlmostEqual(unhinted.advance,
                               unhinted.linear_advance, places=2)
        self.assertNotAlmostEqual(hinted.advance, unhinted.advance, places=2)

    def test_coverage_within_font_bbox(self):
        self.face.set_size(12.0, 96)
        scale = 12.0 * 96 / 72 / self.face.units_per_em
        max_rows = math.ceil((self.face.bbox[3] - self.face.bbox[1]) * scale)
        gi = self.face.glyph_index('H')
        g = self.face.rasterize(gi, hinting=True)
        self.assertLessEqual(g.rows, max_rows)


class TestHintingComparison(FontFixture):
    def test_hinting_changes_coverage_and_advance(self):
        self.face.set_size(10.5, 96)
        gi = self.face.glyph_index('i')
        h = self.face.rasterize(gi, hinting=True)
        u = self.face.rasterize(gi, hinting=False)
        self.assertNotEqual(h.advance, u.advance)
        self.assertNotEqual(h.coverage, u.coverage)

    def test_unhinted_advance_scales_linearly_with_size(self):
        gi = self.face.glyph_index('n')
        adv = {}
        for size in (10.0, 20.0, 15.5):
            self.face.set_size(size, 96)
            adv[size] = self.face.rasterize(gi, hinting=False).advance
        self.assertAlmostEqual(adv[20.0] / adv[10.0], 2.0, delta=0.01)
        self.assertAlmostEqual(adv[15.5] / adv[10.0], 1.55, delta=0.01)


class TestNonIntegerSizes(FontFixture):
    def test_fractional_point_sizes_render(self):
        for size in (8.25, 10.5, 12.75):
            self.face.set_size(size, 96)
            g = self.face.rasterize(self.face.glyph_index('8'), hinting=True)
            self.assertGreater(g.ink_pixels(), 0)
            self.assertGreater(g.advance, 0)

    def test_size_quantization_is_1_64_pt(self):
        s1 = self.face.set_size(10.5, 96)
        self.assertEqual(s1, 10.5 * 64)


class TestThinDigits(unittest.TestCase):
    def test_thin_digits_at_small_size(self):
        k = make_kernel()
        ann = k.add_annotation(50, 100, 9.0, "1111111")
        self.assertIsNotNone(ann)
        _, diags = k.render()
        glyphs = diags[0]["glyphs"]
        self.assertEqual(len(glyphs), 7)
        advances = {g["advance"] for g in glyphs}
        self.assertEqual(len(advances), 1)          # uniform digit spacing
        for g in glyphs:
            self.assertGreaterEqual(g["width"], 1)
            self.assertLessEqual(g["width"], 6)     # '1' incl. base serif
        # thin stems: grayscale AA present, and few fully-inked columns
        k2 = make_kernel()
        k2.add_annotation(50, 100, 9.0, "1")
        k2.render()
        from annotation.layout import layout_spans
        lay = layout_spans(k2.face, k2.cache, parse_markup("1"), 9.0,
                           k2.dpi, True, 50.0, 100.0)
        g = lay.glyphs[0].glyph
        self.assertGreaterEqual(g.distinct_grays(), 3)
        heavy = 0
        for x in range(g.width):
            col_ink = sum(g.coverage[y * g.width + x]
                          for y in range(g.rows)) // 255
            if col_ink >= g.rows // 2:
                heavy += 1
        self.assertLessEqual(heavy, 2)              # stem is 1-2 px wide
        deltas = [glyphs[i + 1]["origin"][0] - glyphs[i]["origin"][0]
                  for i in range(6)]
        self.assertLessEqual(max(deltas) - min(deltas), 1)


class TestSuperSubscript(unittest.TestCase):
    def test_markup_layout(self):
        spans = parse_markup("x^{2} + H~{2}O")
        self.assertEqual([(s.text, s.scale) for s in spans],
                         [("x", 1.0), ("2", 0.7), (" + H", 1.0),
                          ("2", 0.7), ("O", 1.0)])

    def test_super_and_sub_baseline_shifts(self):
        k = make_kernel()
        self.assertIsNotNone(k.add_annotation(50, 200, 14.0, "x^{2} H~{2}O"))
        _, diags = k.render()
        glyphs = diags[0]["glyphs"]
        by_char = {}
        for g in glyphs:
            by_char.setdefault(g["char"], []).append(g)
        base_y = by_char["x"][0]["pen"][1]
        sup = by_char["2"][0]
        sub = by_char["2"][1]
        self.assertLess(sup["pen"][1], base_y)      # raised: smaller device y
        self.assertGreater(sub["pen"][1], base_y)   # lowered
        self.assertLess(sup["size_pt"], by_char["x"][0]["size_pt"])
        self.assertAlmostEqual(sup["size_pt"], 14.0 * 0.7, places=3)

    def test_bad_markup_feeds_back(self):
        k = make_kernel()
        self.assertIsNone(k.add_annotation(10, 10, 12, "x^{2"))
        self.assertTrue(any("markup" in e["message"]
                            for e in k.errors))


class TestDensityIndependence(unittest.TestCase):
    """Device pixels, logical units and font scale are separate: hi-DPI
    must be a real re-rasterization, not an upscale of the lo-DPI pass."""

    def test_device_metrics_scale_with_dpr_logical_do_not(self):
        adv = {}
        for dpr in (1.0, 1.5, 2.0, 3.0):
            k = make_kernel(dpr=dpr, hinting=False)
            k.add_annotation(20, 60, 12.0, "Scale")
            _, diags = k.render()
            g = diags[0]["glyphs"]
            adv[dpr] = sum(x["advance"] for x in g)
        logical = {d: adv[d] / d for d in adv}
        for d in (1.5, 2.0, 3.0):
            self.assertAlmostEqual(logical[d], logical[1.0], delta=0.02)
            self.assertAlmostEqual(adv[d] / adv[1.0], d, delta=0.02)

    def test_hidpi_is_not_an_upscale(self):
        def render_A(dpr):
            k = make_kernel(dpr=dpr, hinting=False)
            k.add_annotation(20, 80, 12.0, "A")
            k.render()
            layout = layout_spans(k.face, k.cache, parse_markup("A"),
                                  12.0, k.dpi, False, 20 * dpr, 80 * dpr)
            return layout.glyphs[0].glyph

        g1 = render_A(1.0)
        g2 = render_A(2.0)
        self.assertGreaterEqual(g2.width, 2 * g1.width - 1)
        # nearest-neighbour 2x upscale of the lo-DPI coverage cannot invent
        # new gray levels; a true re-rasterization does.
        upscaled_grays = len(set(g1.coverage))
        self.assertGreater(g2.distinct_grays(), upscaled_grays)


class TestNegativeBearingAndMarks(unittest.TestCase):
    def test_combining_mark_not_clipped(self):
        # base 'e' alone: remember the top of its ink
        k_base = make_kernel(dpr=2.0)
        k_base.add_annotation(100, 100, 20.0, "e")
        k_base.render()
        base_top = k_base._last[0].ink_bbox()[1]
        # 'e' + combining acute U+0301
        k = make_kernel(dpr=2.0)
        k.add_annotation(100, 100, 20.0, "é")
        _, diags = k.render()
        marks = [g for g in diags[0]["glyphs"] if g["char"] == "́"]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["advance"], 0.0)   # zero-advance mark
        self.assertLessEqual(marks[0]["left"], 0)    # sits over the base
        # acute ink really appears above the base glyph: not clipped
        self.assertLess(k._last[0].ink_bbox()[1], base_top)

    def test_negative_left_bearing_reaches_surface(self):
        k = make_kernel(dpr=2.0)
        k.add_annotation(100, 200, 24.0, "́j")
        _, diags = k.render()
        glyphs = diags[0]["glyphs"]
        neg = [g for g in glyphs if g["left"] < 0]
        self.assertTrue(neg, "expected a glyph with negative left bearing")
        for g in neg:
            self.assertLess(g["origin"][0], g["pen"][0])
        # ink actually appears left of the pen position: not clipped away
        surf = k._last[0]
        g = neg[0]
        x = int(g["pen"][0]) - 1
        column_has_ink = any(
            surf.data[(y * surf.width + x) * 4] < 255
            for y in range(surf.height))
        self.assertTrue(column_has_ink)


class TestBlending(unittest.TestCase):
    def test_explicit_blend_formula(self):
        r, g, b, a = blend_pixel((255, 255, 255, 255), 0, 0, 0, 255, 128)
        self.assertTrue(126 <= r <= 129)
        self.assertEqual((r, g, b, a)[3], 255)
        r0, _, _, _ = blend_pixel((255, 255, 255, 255), 0, 0, 0, 255, 0)
        self.assertEqual(r0, 255)
        r1, _, _, _ = blend_pixel((255, 255, 255, 255), 0, 0, 0, 255, 255)
        self.assertEqual(r1, 0)

    def test_surface_blend_matches_formula(self):
        s = Surface(4, 4)
        cov = bytes([128])
        s.blend_coverage(cov, 1, 1, 2, 2, (0, 0, 0, 255))
        i = (2 * 4 + 2) * 4
        self.assertTrue(126 <= s.data[i] <= 129)
        self.assertEqual(s.data[(0 * 4 + 0) * 4], 255)  # untouched pixel


class TestCumulativeRounding(unittest.TestCase):
    def test_fractional_pen_and_drift_report(self):
        k = make_kernel(dpr=1.75, hinting=False)
        k.add_annotation(10, 100, 13.7, "W" * 30)
        _, diags = k.render()
        rec = diags[0]
        glyphs = rec["glyphs"]
        total = sum(g["advance"] for g in glyphs)
        self.assertAlmostEqual(rec["pen_final"], total, places=2)
        # naive per-glyph rounding would have drifted measurably
        self.assertGreater(abs(rec["rounding_drift"]), 1.0)
        naive_sum = sum(round(g["advance"]) for g in glyphs)
        self.assertEqual(rec["naive_final"], naive_sum)
        # our placement is NOT the naive one: last glyph sits at the
        # fractional pen, not at the sum of rounded advances
        last = glyphs[-1]
        naive_pen_last = sum(round(g["advance"]) for g in glyphs[:-1])
        self.assertNotAlmostEqual(last["pen"][0] - 10 * 1.75,
                                  naive_pen_last, delta=0.5)


class TestCacheAndRender(unittest.TestCase):
    def test_second_render_is_cache_hit_and_identical(self):
        k = make_kernel()
        k.add_annotation(40, 100, 12.0, "Cache me 123")
        p1 = k.render_png()
        k.cache.hits = k.cache.misses = 0
        p2 = k.render_png()
        self.assertEqual(p1, p2)
        self.assertGreater(k.cache.hits, 0)
        self.assertEqual(k.cache.misses, 0)
        _, diags = k.render()
        self.assertTrue(all(g["cache_hit"] for g in diags[0]["glyphs"]))

    def test_png_output(self):
        k = make_kernel(dpr=2.0)
        k.add_annotation(10, 50, 11.0, "PNG")
        png = k.render_png()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        w = int.from_bytes(png[16:20], "big")
        h = int.from_bytes(png[20:24], "big")
        self.assertEqual((w, h), (1600, 1000))

    def test_error_feedback(self):
        k = make_kernel()
        self.assertIsNone(k.add_annotation(10, 10, 500.0, "too big"))
        self.assertIsNone(k.add_annotation(10, 10, 12.0, ""))
        self.assertEqual(len(k.errors), 2)
        ok = k.add_annotation(10, 10, 12.0, "fine")
        self.assertIsNotNone(ok)
        self.assertIsNone(k.move_annotation(ok["id"], -9999, 0))
        ann = k.annotations[0]
        self.assertEqual(ann.status, "error")
        self.assertTrue(ann.message)
        self.assertIsNone(k.update_annotation(999, text="ghost"))
        self.assertGreaterEqual(len(k.errors), 4)


if __name__ == "__main__":
    unittest.main()
