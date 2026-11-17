"""Glyph raster cache. Key includes every parameter that changes the
bitmap: glyph, 26.6 char size, hinting flag and 1/64-px subpixel phase."""


class GlyphCache:
    def __init__(self):
        self._store = {}
        self.hits = 0
        self.misses = 0

    def key(self, glyph_index, size64, hinting, phase_x, phase_y):
        return (glyph_index, size64, bool(hinting),
                int(round(phase_x * 64)), int(round(phase_y * 64)))

    def get(self, key):
        g = self._store.get(key)
        if g is None:
            self.misses += 1
        else:
            self.hits += 1
        return g

    def put(self, key, glyph):
        self._store[key] = glyph
        return glyph

    def clear(self):
        self._store.clear()
        self.hits = self.misses = 0

    def stats(self):
        return {"entries": len(self._store),
                "hits": self.hits, "misses": self.misses}
