"""Measured layout primitives for the Arabic infographic.

The previous card was a long sequence of manual ``y += ...`` statements, which is
why labels ellipsised and sections drifted. Here every component **measures
itself first**: it reports the height it needs for a given width, then renders
into the rectangle it is given, and reports whether it overflowed.

That makes the page composable — a section can be measured, budgeted and placed
without the caller tracking coordinates by hand — and it makes clipping an
explicit, testable condition rather than a silent visual bug.

Arabic shaping and bidi come from ``core.analysis_card_generator`` (already
proven). Fonts are resolved from the system; none are bundled or redistributed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from core.analysis_card_generator import (
    _Fonts,
    display_text,
    resolve_font_family,
)

# --------------------------------------------------------------------------- #
# Design tokens
# --------------------------------------------------------------------------- #

PALETTE = {
    "bg": (11, 18, 32),
    "surface": (19, 28, 48),
    "surface_2": (23, 34, 59),
    "border": (34, 48, 73),
    "text": (230, 237, 247),
    "muted": (163, 180, 205),
    "meta": (128, 146, 175),
    "green": (52, 211, 153),
    "red": (248, 113, 113),
    "amber": (251, 191, 36),
    "blue": (96, 165, 250),
    "purple": (192, 132, 252),
    "gray": (148, 163, 184),
}

#: Semantic role -> colour. Support is ALWAYS green, resistance amber, breakout
#: blue, invalidation red, research-only purple, on every surface.
ROLE_COLOUR = {
    "support": PALETTE["green"], "resistance": PALETTE["amber"],
    "breakout": PALETTE["blue"], "invalidation": PALETTE["red"],
    "research": PALETTE["purple"], "neutral": PALETTE["blue"],
    "positive": PALETTE["green"], "negative": PALETTE["red"],
    "muted": PALETTE["muted"], "text": PALETTE["text"],
}

SPACING = {"xs": 6, "sm": 10, "md": 16, "lg": 22, "xl": 32}
RADIUS = 18
PANEL_PAD = 22


@dataclass(frozen=True)
class TypeScale:
    """Four clearly separated steps. Floor is 18px at 1080 — never smaller."""

    scale: float = 1.0

    def __call__(self, base: int) -> int:
        # Nothing on the card falls below 18px at 1080 — the phone-readable floor.
        return max(int(round(18 * self.scale)), int(round(base * self.scale)))

    price = 108        # +29%
    ticker = 78        # +26%
    title = 32         # +28%
    tile_value = 38    # +23%
    body = 27          # +23%
    label = 26         # +24%
    caption = 21       # +17%
    footer = 18


def tint(colour, alpha: float, over=None):
    """Blend ``colour`` toward the background — a flat stand-in for translucency."""

    base = over or PALETTE["bg"]
    return tuple(int(round(c * alpha + b * (1 - alpha))) for c, b in zip(colour, base))


# --------------------------------------------------------------------------- #
# Canvas
# --------------------------------------------------------------------------- #

class Canvas:
    """Drawing surface with RTL/LTR text, measurement and shape helpers."""

    def __init__(self, width: int, height: int, *, font_path: str | None = None,
                 scale: float = 1.0):
        self.image = Image.new("RGB", (width, height), PALETTE["bg"])
        self.draw = ImageDraw.Draw(self.image)
        regular, bold = resolve_font_family(font_path)
        self.fonts = _Fonts(regular, bold)
        self.width, self.height = width, height
        self.type = TypeScale(scale)
        self.scale = scale

    # -- measurement -------------------------------------------------------
    def text_width(self, text: str, size: int, bold: bool = False) -> float:
        return self.draw.textlength(display_text(str(text)),
                                    font=self.fonts.get(size, bold))

    def line_height(self, size: int) -> int:
        # Arabic needs more leading than Latin; 1.42 keeps descenders clear.
        return int(round(size * 1.42))

    def wrap(self, text: str, size: int, max_width: int,
             max_lines: int | None = None) -> list[str]:
        """Greedy word wrap measured on the RENDERED form.

        Unlike the old ``fit_rtl``, nothing is ellipsised unless ``max_lines`` is
        given AND the text genuinely exceeds it — callers that must not truncate
        simply pass ``None`` and size their rectangle from the result.
        """
        words = str(text).split()
        if not words:
            return []
        lines, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and self.text_width(candidate, size) > max_width:
                lines.append(current)
                current = word
                if max_lines and len(lines) == max_lines:
                    return self._ellipsise(lines, size, max_width)
            else:
                current = candidate
        if current:
            lines.append(current)
        if max_lines and len(lines) > max_lines:
            return self._ellipsise(lines[:max_lines], size, max_width)
        return lines

    def _ellipsise(self, lines, size, max_width):
        if not lines:
            return lines
        last = lines[-1]
        while last and self.text_width(last + "…", size) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
        return lines

    # -- text --------------------------------------------------------------
    def text_rtl(self, right_x: int, y: int, text: str, size: int, colour,
                 bold: bool = False):
        self.draw.text((right_x, y), display_text(str(text)),
                       font=self.fonts.get(size, bold), fill=colour, anchor="ra")

    def text_ltr(self, left_x: int, y: int, text: str, size: int, colour,
                 bold: bool = False):
        # base_rtl=False keeps a ticker or a number in logical LTR order.
        self.draw.text((left_x, y), display_text(str(text), base_rtl=False),
                       font=self.fonts.get(size, bold), fill=colour, anchor="la")

    def text_centre(self, centre_x: int, y: int, text: str, size: int, colour,
                    bold: bool = False, rtl: bool = True):
        self.draw.text((centre_x, y), display_text(str(text), base_rtl=rtl),
                       font=self.fonts.get(size, bold), fill=colour, anchor="ma")

    # -- shapes ------------------------------------------------------------
    def panel(self, box, *, fill=None, outline=None, radius=RADIUS, accent=None,
              accent_width=5):
        self.draw.rounded_rectangle(box, radius=radius, fill=fill or PALETTE["surface"],
                                    outline=outline or PALETTE["border"], width=2)
        if accent:
            x0, y0, x1, y1 = box
            self.draw.rounded_rectangle(
                (x1 - accent_width - 3, y0 + 10, x1 - 3, y1 - 10),
                radius=accent_width // 2 or 1, fill=accent)

    def badge(self, right_x: int, y: int, text: str, size: int, colour,
              *, solid: bool = False) -> int:
        pad_x, pad_y = int(size * 0.72), int(size * 0.40)
        width = int(self.text_width(text, size, bold=True) + pad_x * 2)
        height = int(size + pad_y * 2)
        left = right_x - width
        self.draw.rounded_rectangle(
            (left, y, right_x, y + height), radius=height // 2,
            fill=colour if solid else tint(colour, 0.20),
            outline=colour if solid else tint(colour, 0.55), width=2)
        self.draw.text((right_x - pad_x, y + pad_y), display_text(text),
                       font=self.fonts.get(size, True),
                       fill=PALETTE["bg"] if solid else colour, anchor="ra")
        return left

    def badge_height(self, size: int) -> int:
        return int(size + int(size * 0.40) * 2)

    def divider(self, x0: int, x1: int, y: int, colour=None):
        self.draw.line((x0, y, x1, y), fill=colour or PALETTE["border"], width=2)

    def to_png(self) -> bytes:
        import io
        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG")
        return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #

@dataclass
class Component:
    """Base contract: measure, then render into a rect, and report overflow."""

    overflowed: bool = field(default=False, init=False)

    def measure(self, canvas: Canvas, width: int) -> int:
        raise NotImplementedError

    def render(self, canvas: Canvas, box) -> None:
        raise NotImplementedError


@dataclass
class SectionHeader(Component):
    title: str
    accent: tuple = PALETTE["text"]

    def measure(self, canvas: Canvas, width: int) -> int:
        return canvas.line_height(canvas.type(TypeScale.title)) + SPACING["sm"]

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, _ = box
        size = canvas.type(TypeScale.title)
        canvas.text_rtl(x1, y0, self.title, size, self.accent, bold=True)
        rule_y = y0 + canvas.line_height(size) - SPACING["xs"]
        text_w = canvas.text_width(self.title, size, bold=True)
        canvas.divider(x0, int(x1 - text_w - SPACING["md"]), rule_y)


@dataclass
class Row(Component):
    """One label/value line with a lossless stacked fallback.

    Labels and values are never ellipsised.  When the two columns cannot hold
    the complete text, the row measures and renders a labelled full-width value
    underneath instead.  ``span`` is consumed by :class:`RowGrid` only.
    """

    label: str
    value: str
    value_colour: tuple = PALETTE["text"]
    label_sub: str = ""
    font_size: int | None = None
    span: int = 1

    def _layout(self, canvas: Canvas, width: int):
        body = canvas.type(self.font_size or TypeScale.body)
        label_size = canvas.type(min(self.font_size or TypeScale.label,
                                     TypeScale.label))
        value_w = canvas.text_width(self.value, body, bold=True)
        label_w = canvas.text_width(self.label, label_size)
        same_line = value_w + label_w + SPACING["md"] <= width
        if same_line:
            return body, label_size, True, (str(self.value),), (str(self.label),)
        value_lines = tuple(canvas.wrap(self.value, body, width)) or (str(self.value),)
        label_lines = tuple(canvas.wrap(self.label, label_size, width)) or (str(self.label),)
        return body, label_size, False, value_lines, label_lines

    def measure(self, canvas: Canvas, width: int) -> int:
        body, label_size, same_line, value_lines, label_lines = self._layout(canvas, width)
        if same_line:
            height = max(canvas.line_height(body), canvas.line_height(label_size))
        else:
            height = (len(label_lines) * canvas.line_height(label_size)
                      + len(value_lines) * canvas.line_height(body)
                      + SPACING["xs"])
        if self.label_sub:
            height += canvas.line_height(canvas.type(TypeScale.caption))
        return height

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, y1 = box
        caption = canvas.type(TypeScale.caption)
        body, label_size, same_line, value_lines, label_lines = self._layout(
            canvas, x1 - x0)
        y = y0
        if same_line:
            canvas.text_ltr(x0, y, value_lines[0], body, self.value_colour, bold=True)
            canvas.text_rtl(x1, y + 2, label_lines[0], label_size, PALETTE["muted"])
            y += max(canvas.line_height(body), canvas.line_height(label_size))
        else:
            for line in label_lines:
                canvas.text_rtl(x1, y, line, label_size, PALETTE["muted"])
                y += canvas.line_height(label_size)
            y += SPACING["xs"]
            for line in value_lines:
                canvas.text_rtl(x1, y, line, body, self.value_colour, bold=True)
                y += canvas.line_height(body)

        if self.label_sub:
            canvas.text_rtl(x1, y, self.label_sub, caption, PALETTE["muted"])


@dataclass
class Tile(Component):
    """A compact metric box: value above, label below."""

    label: str
    value: str
    value_colour: tuple = PALETTE["text"]

    def measure(self, canvas: Canvas, width: int) -> int:
        return (canvas.line_height(canvas.type(TypeScale.tile_value))
                + canvas.line_height(canvas.type(TypeScale.caption))
                + SPACING["md"] * 2)

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, y1 = box
        canvas.panel(box, fill=PALETTE["surface_2"], radius=14)
        centre = (x0 + x1) // 2
        value_size = canvas.type(TypeScale.tile_value)
        value = self.value
        while (canvas.text_width(value, value_size, bold=True) > (x1 - x0) - 16
               and value_size > canvas.type(TypeScale.caption)):
            value_size -= 2
        canvas.text_centre(centre, y0 + SPACING["md"], value, value_size,
                           self.value_colour, bold=True, rtl=False)
        canvas.text_centre(centre, y0 + SPACING["md"] + canvas.line_height(value_size),
                           self.label, canvas.type(TypeScale.caption), PALETTE["muted"])


@dataclass
class BulletList(Component):
    items: tuple
    colour: tuple = PALETTE["text"]
    max_items: int = 5
    #: Lines each bullet may occupy. Short deterministic reasons use 1; a summary
    #: paragraph uses more. Capping here is what keeps a panel's height bounded.
    max_lines_per_item: int | None = 2
    font_size: int | None = None

    def _lines(self, canvas: Canvas, width: int):
        size = canvas.type(self.font_size or TypeScale.body)
        out = []
        for item in list(self.items)[: self.max_items]:
            out.append(canvas.wrap(f"• {item}", size, width - SPACING["md"],
                                   max_lines=self.max_lines_per_item))
        return out

    def measure(self, canvas: Canvas, width: int) -> int:
        size = canvas.type(self.font_size or TypeScale.body)
        return sum(len(block) * canvas.line_height(size) + SPACING["xs"]
                   for block in self._lines(canvas, width))

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, y1 = box
        size = canvas.type(self.font_size or TypeScale.body)
        y = y0
        for block in self._lines(canvas, x1 - x0):
            for line in block:
                if y + canvas.line_height(size) > y1:
                    self.overflowed = True
                    return
                canvas.text_rtl(x1, y, line, size, self.colour)
                y += canvas.line_height(size)
            y += SPACING["xs"]


@dataclass
class RowGrid(Component):
    """Lossless two-column row grid; full-span rows keep long diagnostics clear."""

    rows: tuple
    columns: int = 2

    def _bands(self):
        bands, current = [], []
        for row in self.rows:
            if getattr(row, "span", 1) >= self.columns:
                if current:
                    bands.append(tuple(current))
                    current = []
                bands.append((row,))
                continue
            current.append(row)
            if len(current) == self.columns:
                bands.append(tuple(current))
                current = []
        if current:
            bands.append(tuple(current))
        return tuple(bands)

    def measure(self, canvas: Canvas, width: int) -> int:
        gap = SPACING["md"]
        each = (width - gap * (self.columns - 1)) // self.columns
        return sum(max(row.measure(canvas, width if len(band) == 1 and row.span >= self.columns
                                   else each) for row in band)
                   for band in self._bands())

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, y1 = box
        gap = SPACING["md"]
        each = (x1 - x0 - gap * (self.columns - 1)) // self.columns
        y = y0
        for band in self._bands():
            full = len(band) == 1 and band[0].span >= self.columns
            need = max(row.measure(canvas, x1 - x0 if full else each) for row in band)
            if y + need > y1:
                self.overflowed = True
                return
            for index, row in enumerate(band):
                if full:
                    child_box = (x0, y, x1, y + need)
                else:
                    right = x1 - index * (each + gap)
                    child_box = (right - each, y, right, y + need)
                row.render(canvas, child_box)
                if row.overflowed:
                    self.overflowed = True
            y += need


@dataclass
class BulletGrid(Component):
    """A concise bullet grid that wraps completely and never adds an ellipsis."""

    items: tuple
    columns: int = 2
    max_items: int = 4
    font_size: int = 18

    def _bands(self, canvas: Canvas, width: int):
        gap = SPACING["sm"]
        each = (width - gap * (self.columns - 1)) // self.columns
        size = canvas.type(self.font_size)
        blocks = [tuple(canvas.wrap(f"• {item}", size, each))
                  for item in self.items[:self.max_items]]
        return tuple(tuple(blocks[i:i + self.columns])
                     for i in range(0, len(blocks), self.columns)), each, size

    def measure(self, canvas: Canvas, width: int) -> int:
        bands, _, size = self._bands(canvas, width)
        return sum(max((len(block) for block in band), default=0)
                   * canvas.line_height(size) + SPACING["xs"] for band in bands)

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, y1 = box
        bands, each, size = self._bands(canvas, x1 - x0)
        y = y0
        for band in bands:
            band_h = max((len(block) for block in band), default=0) * canvas.line_height(size)
            if y + band_h > y1:
                self.overflowed = True
                return
            for index, block in enumerate(band):
                right = x1 - index * (each + SPACING["sm"])
                line_y = y
                for line in block:
                    canvas.text_rtl(right, line_y, line, size, PALETTE["text"])
                    line_y += canvas.line_height(size)
            y += band_h + SPACING["xs"]


@dataclass
class Panel(Component):
    """A titled frame that measures and lays out its own children."""

    title: str
    children: tuple
    accent: tuple = PALETTE["blue"]
    frame_colour: tuple | None = None
    title_badge: str = ""
    compact: bool = False

    def _pad(self) -> int:
        return 12 if self.compact else PANEL_PAD

    def _title_size(self, canvas: Canvas) -> int:
        return canvas.type(TypeScale.label if self.compact else TypeScale.title)

    @staticmethod
    def _gap(child) -> int:
        # A Row carries its own leading; only non-row children need a section gap.
        return 0 if isinstance(child, Row) else SPACING["sm"]

    def measure(self, canvas: Canvas, width: int) -> int:
        pad = self._pad()
        inner = width - pad * 2
        height = pad
        if self.title:
            height += canvas.line_height(self._title_size(canvas)) + SPACING["sm"]
        last = 0
        for child in self.children:
            last = self._gap(child)
            height += child.measure(canvas, inner) + last
        return height + pad - last

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, y1 = box
        canvas.panel(box, outline=self.frame_colour or PALETTE["border"],
                     accent=self.accent)
        pad = self._pad()
        inner_left, inner_right = x0 + pad, x1 - pad
        y = y0 + pad
        if self.title:
            size = self._title_size(canvas)
            canvas.text_rtl(inner_right, y, self.title, size, self.accent, bold=True)
            if self.title_badge:
                badge_size = canvas.type(16)
                pad_x = int(badge_size * 0.72)
                badge_w = int(canvas.text_width(self.title_badge, badge_size, bold=True)
                              + pad_x * 2)
                canvas.badge(inner_left + badge_w, y, self.title_badge, badge_size,
                             self.accent)
            y += canvas.line_height(size) + SPACING["sm"]
        for child in self.children:
            need = child.measure(canvas, inner_right - inner_left)
            if y + need > y1 - pad + SPACING["sm"]:
                self.overflowed = True
                return
            child.render(canvas, (inner_left, y, inner_right, y + need))
            if child.overflowed:
                self.overflowed = True
            y += need + self._gap(child)


@dataclass
class TileRow(Component):
    """Evenly spaced tiles across the full width; empty tiles are dropped."""

    tiles: tuple

    def measure(self, canvas: Canvas, width: int) -> int:
        if not self.tiles:
            return 0
        return max(tile.measure(canvas, width) for tile in self.tiles)

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, y1 = box
        if not self.tiles:
            return
        gap = SPACING["sm"]
        each = (x1 - x0 - gap * (len(self.tiles) - 1)) // len(self.tiles)
        for index, tile in enumerate(self.tiles):
            # Laid out right-to-left: the first tile sits on the right.
            right = x1 - index * (each + gap)
            tile.render(canvas, (right - each, y0, right, y1))


@dataclass
class Divider(Component):
    def measure(self, canvas: Canvas, width: int) -> int:
        return SPACING["md"]

    def render(self, canvas: Canvas, box) -> None:
        x0, y0, x1, _ = box
        canvas.divider(x0, x1, y0 + SPACING["xs"])


__all__ = [
    "PALETTE", "PANEL_PAD", "RADIUS", "ROLE_COLOUR", "SPACING", "BulletGrid",
    "BulletList", "Canvas", "Component", "Divider", "Panel", "Row", "RowGrid",
    "SectionHeader", "Tile", "TileRow", "TypeScale", "tint",
]
