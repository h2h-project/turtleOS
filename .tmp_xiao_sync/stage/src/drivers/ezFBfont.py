# ezFBfont.py — Safe Optimized Version
# Based on original Peter Hinch writer adaptation
# Only optimization: reuse palette buffer
# Everything else behaves exactly like original.

import framebuf


class ezFBfont:

    def __init__(self, device,
                 font,
                 fg=1,
                 bg=0,
                 tkey=-1,
                 halign='left',
                 valign='top',
                 vgap=0,
                 hgap=0,
                 split='\n',
                 cswap=False,
                 verbose=False):

        self._device = device
        self._font = font
        self.name = self._font.__name__

        self._font_format = framebuf.MONO_HLSB
        self._font_colors = 2
        self._palette_format = framebuf.RGB565
        self._cswap = cswap

        # Cache font metrics (safe optimization)
        self._font_height = self._font.height()
        self._font_baseline = self._font.baseline()

        # SAFE OPTIMIZATION:
        # Reuse palette buffer instead of allocating every character
        self._palette_buf = bytearray(self._font_colors * 2)
        self._palette = framebuf.FrameBuffer(
            self._palette_buf,
            self._font_colors,
            1,
            self._palette_format
        )

        self.set_default(fg, bg, tkey,
                         halign, valign,
                         hgap, vgap,
                         split, verbose)

    # -------------------------------------------------
    # Defaults (restored original behavior)
    # -------------------------------------------------

    def set_default(self, fg=None, bg=None, tkey=None,
                    halign=None, valign=None,
                    hgap=None, vgap=None,
                    split=None, verbose=None):

        # initialize attributes if first time
        if not hasattr(self, "fg"):
            self.fg = 1
            self.bg = 0
            self.tkey = -1
            self.halign = 'left'
            self.valign = 'top'
            self.hgap = 0
            self.vgap = 0
            self.split = '\n'
            self._verbose = False

        if fg is not None:
            self.fg = fg
        if bg is not None:
            self.bg = bg
        if tkey is not None:
            self.tkey = tkey
        if halign is not None:
            self.halign = halign
        if valign is not None:
            self.valign = valign
        if hgap is not None:
            self.hgap = hgap
        if vgap is not None:
            self.vgap = vgap
        if split is not None:
            self.split = split
        if verbose is not None:
            self._verbose = verbose

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def _swap_bytes(self, color):
        return ((color & 255) << 8) + (color >> 8) if self._cswap else color

    def _line_size(self, string):
        x = 0
        for char in string:
            _, _, char_width = self._font.get_ch(char)
            if char_width > 0:
                x += char_width + self.hgap
        if x != 0:
            x -= self.hgap
        return x, self._font_height

    # -------------------------------------------------
    # Character draw (SAFE + STABLE)
    # -------------------------------------------------

    def _put_char(self, char, x, y, fg, bg, tkey):

        glyph, char_height, char_width = self._font.get_ch(char)
        if glyph is None:
            return None, None

        # ORIGINAL BEHAVIOR: ensure buffer protocol
        try:
            buf = bytearray(glyph)
        except Exception:
            return None, None

        # Update palette (no allocation now)
        self._palette.pixel(0, 0, self._swap_bytes(bg))
        self._palette.pixel(self._font_colors - 1, 0, self._swap_bytes(fg))

        charbuf = framebuf.FrameBuffer(
            buf,
            char_width,
            char_height,
            self._font_format
        )

        self._device.blit(charbuf, x, y, tkey, self._palette)

        return char_width, char_height

    # -------------------------------------------------
    # size()
    # -------------------------------------------------

    def size(self, string):
        if len(string) == 0:
            return 0, 0

        lines = string.split(self.split)

        w = 0
        for line in lines:
            lw, _ = self._line_size(line)
            if lw > w:
                w = lw

        h = (len(lines) * (self._font_height + self.vgap)) - self.vgap
        return w, h

    # -------------------------------------------------
    # write()
    # -------------------------------------------------

    def write(self, string, x, y,
              fg=None, bg=None, tkey=None,
              halign=None, valign=None):

        if len(string) == 0:
            return True

        all_chars = True

        fg = self.fg if fg is None else fg
        bg = self.bg if bg is None else bg
        tkey = self.tkey if tkey is None else tkey
        halign = self.halign if halign is None else halign
        valign = self.valign if valign is None else valign

        lines = string.split(self.split)

        high = (len(lines) * (self._font_height + self.vgap)) - self.vgap

        ypos = y
        if valign == 'baseline':
            ypos = y - self._font_baseline + 1
        elif valign == 'center':
            ypos = int(y - (high / 2))
        elif valign == 'bottom':
            ypos = y - high

        for line in lines:

            wide, line_height = self._line_size(line)

            if halign == 'left':
                xpos = x
            elif halign == 'right':
                xpos = x - wide
            else:
                xpos = int(x - (wide / 2))

            for char in line:
                cx, _ = self._put_char(char, xpos, ypos, fg, bg, tkey)
                if cx is None:
                    all_chars = False
                else:
                    xpos += cx + self.hgap

            ypos += line_height + self.vgap

        return all_chars
