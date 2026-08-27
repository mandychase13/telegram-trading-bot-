"""
Trade Summary Card Generator for Copy Vault.

Generates a premium 1080×1350 PNG trade summary card entirely in memory.
Fonts and static assets (QR code, logo mark) are preloaded once at import time.

Public API
----------
generate_trade_card(data: TradeCardData) -> bytes
    Returns raw PNG bytes; raises CardGenerationError on failure.
"""
from __future__ import annotations

import io
import math
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — PIL and qrcode are loaded once and cached
# ---------------------------------------------------------------------------
try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    _PIL_OK = True
except ImportError:
    _PIL_OK = False
    logger.error("Pillow not installed — trade cards are disabled")

try:
    import qrcode
    _QR_OK = True
except ImportError:
    _QR_OK = False
    logger.error("qrcode not installed — QR codes will be skipped")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
W, H = 1080, 1350

# Colour palette
BG_DARK       = (8,  12,  26)
BG_MID        = (12, 18,  38)
ACCENT_BLUE   = (0,  180, 255)
ACCENT_CYAN   = (0,  230, 200)
ACCENT_PURPLE = (140, 60, 255)
GLASS_FILL    = (20,  30,  60, 180)      # RGBA
GLASS_BORDER  = (60,  90, 160, 120)      # RGBA

GREEN_PROFIT  = (0,  230, 120)
RED_LOSS      = (255, 70,  90)
TEXT_WHITE    = (240, 245, 255)
TEXT_DIM      = (130, 150, 195)
TEXT_LABEL    = (90,  110, 155)

CHAIN_COLORS  = {
    "SOL": (155,  90, 255),
    "ETH": ( 80, 130, 255),
    "BNB": (240, 185,  10),
}

FONT_BASE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ---------------------------------------------------------------------------
# Pre-loaded font cache
# ---------------------------------------------------------------------------
_fonts: dict[str, ImageFont.FreeTypeFont] = {}

def _font(bold: bool = False, size: int = 20) -> "ImageFont.FreeTypeFont":
    """Return a cached font object."""
    if not _PIL_OK:
        raise RuntimeError("Pillow not available")
    key = f"{'b' if bold else 'r'}:{size}"
    if key not in _fonts:
        path = FONT_BOLD if bold else FONT_BASE
        try:
            _fonts[key] = ImageFont.truetype(path, size)
        except OSError:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def _preload_fonts() -> None:
    """Eagerly load the most-used font sizes to warm the cache."""
    for bold in (True, False):
        for sz in (18, 22, 26, 32, 40, 52, 80, 110):
            try:
                _font(bold, sz)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# QR code cache
# ---------------------------------------------------------------------------
_QR_CACHE: Optional["Image.Image"] = None
BOT_URL = os.environ.get("BOT_PUBLIC_URL", "https://t.me/CopyVaultBot")

def _get_qr(size: int = 180) -> Optional["Image.Image"]:
    global _QR_CACHE
    if not _QR_OK or not _PIL_OK:
        return None
    if _QR_CACHE is None:
        try:
            qr = qrcode.QRCode(
                version=2,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=6,
                border=2,
            )
            qr.add_data(BOT_URL)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#00B4FF", back_color="#080C1A")
            _QR_CACHE = img.convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception as exc:
            logger.warning("QR generation failed: %s", exc)
            return None
    else:
        _QR_CACHE = _QR_CACHE.resize((size, size), Image.LANCZOS)
    return _QR_CACHE


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class TradeCardData:
    """All fields that can appear on a trade summary card.

    Optional numerical fields should be left as None when unknown;
    the card renderer will display '—' for those slots.
    """
    # Identity
    token_name:       str   = "Unknown"
    token_symbol:     str   = "???"
    token_pair:       str   = ""           # e.g. "TOKEN/SOL"
    network:          str   = "SOL"        # SOL | ETH | BNB

    # Prices
    buy_price:        Optional[float] = None
    sell_price:       Optional[float] = None

    # Trade amounts
    amount_invested:  Optional[float] = None   # native token amount going in
    amount_received:  Optional[float] = None   # native token amount coming out

    # P&L
    gross_profit:     Optional[float] = None
    net_profit:       Optional[float] = None
    profit_pct:       Optional[float] = None
    roi_pct:          Optional[float] = None

    # Context
    trade_duration:   str            = "—"
    portfolio_before: Optional[float] = None
    portfolio_after:  Optional[float] = None
    date:             str            = "—"
    time_str:         str            = "—"

    # Meta
    is_demo:          bool           = False
    chain_currency:   str            = ""   # "SOL" / "ETH" / "BNB"  (display unit)


class CardGenerationError(Exception):
    """Raised when card generation fails."""


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _draw_rounded_rect(
    draw: "ImageDraw.ImageDraw",
    xy: tuple,
    radius: int,
    fill,
    outline=None,
    width: int = 1,
) -> None:
    """Draw a rounded rectangle on *draw* using PIL primitives.

    Clamps *radius* to half the smaller dimension to avoid degenerate geometry.
    """
    x1, y1, x2, y2 = xy
    # Ensure coordinates are ordered correctly
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    # Clamp radius so corners don't overlap
    radius = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    if radius <= 0:
        draw.rectangle([x1, y1, x2, y2], fill=fill, outline=outline, width=width)
        return
    d = radius * 2
    # Fill centre cross
    if x2 - x1 > d:
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    if y2 - y1 > d:
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    # Four corner ellipses
    draw.ellipse([x1, y1, x1 + d, y1 + d], fill=fill)
    draw.ellipse([x2 - d, y1, x2, y1 + d], fill=fill)
    draw.ellipse([x1, y2 - d, x1 + d, y2], fill=fill)
    draw.ellipse([x2 - d, y2 - d, x2, y2], fill=fill)
    if outline:
        draw.arc([x1, y1, x1 + d, y1 + d], 180, 270, fill=outline, width=width)
        draw.arc([x2 - d, y1, x2, y1 + d], 270, 360, fill=outline, width=width)
        draw.arc([x1, y2 - d, x1 + d, y2], 90,  180, fill=outline, width=width)
        draw.arc([x2 - d, y2 - d, x2, y2], 0,    90, fill=outline, width=width)
        if x2 - x1 > d:
            draw.line([x1 + radius, y1,      x2 - radius, y1],      fill=outline, width=width)
            draw.line([x1 + radius, y2,      x2 - radius, y2],      fill=outline, width=width)
        if y2 - y1 > d:
            draw.line([x1,          y1 + radius, x1, y2 - radius],  fill=outline, width=width)
            draw.line([x2,          y1 + radius, x2, y2 - radius],  fill=outline, width=width)


def _draw_rounded_rect_rgba(
    canvas: "Image.Image",
    xy: tuple,
    radius: int,
    fill_rgba: tuple,
    outline_rgba: Optional[tuple] = None,
    outline_width: int = 2,
) -> None:
    """Draw an RGBA rounded rectangle over an RGBA canvas via alpha-compositing."""
    x1, y1, x2, y2 = xy
    w = max(x2 - x1, 1)
    h = max(y2 - y1, 1)
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(panel)
    _draw_rounded_rect(pdraw, (0, 0, w, h), radius, fill=fill_rgba)
    if outline_rgba:
        # Draw outline only — no fill pass (would overwrite the fill we just drew)
        _draw_rounded_rect(pdraw, (0, 0, w, h), radius,
                           fill=None, outline=outline_rgba,
                           width=outline_width)
    canvas.alpha_composite(panel, dest=(x1, y1))


def _glow_circle(canvas: "Image.Image", cx: int, cy: int, r: int, color: tuple, alpha: int = 60) -> None:
    """Paint a soft radial glow blob."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=r // 2))
    canvas.alpha_composite(layer)


def _gradient_bg(size: tuple[int, int]) -> "Image.Image":
    """Create a dark gradient background image."""
    img = Image.new("RGBA", size, (*BG_DARK, 255))
    draw = ImageDraw.Draw(img)
    # Subtle bottom-left purple glow
    for i in range(200, 0, -20):
        a = max(0, 12 - (200 - i) // 18)
        draw.ellipse([-50, H - 50 - i, i * 2, H + 50], fill=(*ACCENT_PURPLE, a))
    # Top-right blue glow
    for i in range(180, 0, -20):
        a = max(0, 10 - (180 - i) // 18)
        draw.ellipse([W - i * 2 + 50, -50, W + 50, i * 2 - 50], fill=(*ACCENT_BLUE, a))
    return img


def _text_width(draw: "ImageDraw.ImageDraw", text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _centered_text(draw: "ImageDraw.ImageDraw", y: int, text: str, font, fill, max_w: int = W) -> None:
    tw = _text_width(draw, text, font)
    x = (max_w - tw) // 2
    draw.text((x, y), text, font=font, fill=fill)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _draw_header(canvas: "Image.Image", draw: "ImageDraw.ImageDraw", data: TradeCardData) -> None:
    """Top strip: logo mark + bot name on left; status badge on right."""
    pad = 54

    # ── Logo mark (hexagon-ish circle with gradient) ──────────────────────
    logo_r = 30
    lx, ly = pad, 54
    _glow_circle(canvas, lx + logo_r, ly + logo_r, logo_r + 10, ACCENT_BLUE, alpha=70)
    draw.ellipse([lx, ly, lx + logo_r * 2, ly + logo_r * 2],
                 fill=(16, 28, 64), outline=(*ACCENT_BLUE, 200), width=2)
    # "CM" inside logo circle
    lf = _font(bold=True, size=20)
    draw.text((lx + 9, ly + 7), "CM", font=lf, fill=ACCENT_BLUE)

    # Brand name
    draw.text((lx + logo_r * 2 + 14, ly + 5), "COPY", font=_font(bold=True, size=22), fill=TEXT_WHITE)
    draw.text((lx + logo_r * 2 + 14, ly + 28), "MIRROR", font=_font(bold=False, size=16), fill=ACCENT_CYAN)

    # ── Status badge ───────────────────────────────────────────────────────
    label = "🧪 DEMO TRADE SUMMARY" if data.is_demo else "✅ TRADE COMPLETED"
    badge_color = (255, 200, 0) if data.is_demo else GREEN_PROFIT
    bf = _font(bold=True, size=18)
    bw = _text_width(draw, label, bf) + 32
    bx = W - pad - bw
    by = ly + 8
    _draw_rounded_rect_rgba(canvas, (bx, by, bx + bw, by + 34), 17,
                             fill_rgba=(*badge_color, 28),
                             outline_rgba=(*badge_color, 160))
    draw.text((bx + 16, by + 7), label, font=bf, fill=badge_color)

    # Separator line
    y_sep = ly + logo_r * 2 + 22
    for xi in range(pad, W - pad):
        alpha = int(60 + 80 * math.sin(math.pi * (xi - pad) / (W - 2 * pad)))
        draw.point((xi, y_sep), fill=(*ACCENT_BLUE, alpha))


def _draw_token_section(canvas: "Image.Image", draw: "ImageDraw.ImageDraw", data: TradeCardData) -> int:
    """Token avatar + name + network badge. Returns bottom y."""
    y0 = 162
    cx = W // 2

    # Token avatar circle with glow
    av_r = 48
    chain_col = CHAIN_COLORS.get(data.network, ACCENT_BLUE)
    _glow_circle(canvas, cx, y0 + av_r + 8, av_r + 18, chain_col, alpha=55)

    # Gradient fill for avatar
    av_img = Image.new("RGBA", (av_r * 2, av_r * 2), (0, 0, 0, 0))
    av_draw = ImageDraw.Draw(av_img)
    for i in range(av_r):
        t = i / av_r
        r = int(ACCENT_PURPLE[0] * (1 - t) + chain_col[0] * t)
        g = int(ACCENT_PURPLE[1] * (1 - t) + chain_col[1] * t)
        b = int(ACCENT_PURPLE[2] * (1 - t) + chain_col[2] * t)
        av_draw.ellipse([i, i, av_r * 2 - i, av_r * 2 - i], fill=(r, g, b, 255))
    # Token initial letter
    sym = (data.token_symbol or "?")[:3].upper()
    sf = _font(bold=True, size=26 if len(sym) <= 2 else 22)
    sw = _text_width(av_draw, sym, sf)
    av_draw.text((av_r - sw // 2, av_r - 16), sym, font=sf, fill=(255, 255, 255, 230))
    av_draw.ellipse([2, 2, av_r * 2 - 2, av_r * 2 - 2],
                    fill=None, outline=(*chain_col, 180), width=3)
    canvas.alpha_composite(av_img, dest=(cx - av_r, y0 + 8))

    draw_y = y0 + av_r * 2 + 22

    # Token name
    name_text = data.token_name if data.token_name not in ("Unknown", "") else data.token_symbol
    _centered_text(draw, draw_y, name_text.upper(), _font(bold=True, size=32), TEXT_WHITE)
    draw_y += 42

    # Token pair
    if data.token_pair:
        _centered_text(draw, draw_y, data.token_pair, _font(bold=False, size=22), TEXT_DIM)
        draw_y += 34

    # Network badge
    badge_col = chain_col
    badge_text = f"  {data.network}  "
    bf = _font(bold=True, size=18)
    bw = _text_width(draw, badge_text, bf) + 8
    bx = cx - bw // 2
    _draw_rounded_rect_rgba(canvas, (bx, draw_y, bx + bw, draw_y + 30), 15,
                             fill_rgba=(*badge_col, 35),
                             outline_rgba=(*badge_col, 200))
    draw.text((bx + (bw - _text_width(draw, badge_text, bf)) // 2, draw_y + 5),
              badge_text, font=bf, fill=badge_col)
    return draw_y + 46


def _draw_profit_section(canvas: "Image.Image", draw: "ImageDraw.ImageDraw",
                          data: TradeCardData, y0: int) -> int:
    """Giant profit percentage in the center. Returns bottom y."""
    if data.profit_pct is not None:
        pct = data.profit_pct
        sign = "+" if pct >= 0 else ""
        label = f"{sign}{pct:.1f}%"
        color = GREEN_PROFIT if pct >= 0 else RED_LOSS
    else:
        label = "—"
        color = TEXT_DIM

    y0 += 10
    # Glow behind the number
    _glow_circle(canvas, W // 2, y0 + 70, 130, color, alpha=35)

    # Big number
    big_f = _font(bold=True, size=110)
    _centered_text(draw, y0, label, big_f, color)

    sub_y = y0 + 128
    _centered_text(draw, sub_y, "PROFIT / LOSS", _font(bold=False, size=20), TEXT_LABEL)

    # Thin line accent under subtitle
    lw = 120
    lx = W // 2 - lw // 2
    ly = sub_y + 30
    for xi in range(lx, lx + lw):
        t = (xi - lx) / lw
        r = int(color[0] * (1 - t) + ACCENT_PURPLE[0] * t)
        g = int(color[1] * (1 - t) + ACCENT_PURPLE[1] * t)
        b = int(color[2] * (1 - t) + ACCENT_PURPLE[2] * t)
        draw.point((xi, ly), fill=(r, g, b, 200))

    return ly + 24


def _fmt_val(value: Optional[float], prefix: str = "", suffix: str = "",
             decimals: int = 4) -> str:
    """Format an optional float for display on the card."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000:
        return f"{prefix}{value / 1_000_000:.2f}M{suffix}"
    if abs(value) >= 1_000:
        return f"{prefix}{value:,.2f}{suffix}"
    return f"{prefix}{value:.{decimals}f}{suffix}"


def _draw_stat_cell(
    draw: "ImageDraw.ImageDraw",
    x: int, y: int, w: int, h: int,
    label: str, value: str, value_color: tuple = TEXT_WHITE,
) -> None:
    """Draw a single label+value cell (no background — background drawn by caller)."""
    lf = _font(bold=False, size=17)
    vf = _font(bold=True,  size=22)

    draw.text((x + 14, y + 10), label.upper(), font=lf, fill=TEXT_LABEL)
    draw.text((x + 14, y + 32), value, font=vf, fill=value_color)


def _draw_details_panel(canvas: "Image.Image", draw: "ImageDraw.ImageDraw",
                         data: TradeCardData, y0: int) -> int:
    """Glassmorphism panel with stat grid. Returns bottom y."""
    pad = 40
    panel_x = pad
    panel_w = W - pad * 2
    cell_cols = 2
    cell_rows = 8
    cell_h = 70
    panel_h = cell_h * cell_rows + 30
    panel_y = y0

    _draw_rounded_rect_rgba(canvas, (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
                             radius=22,
                             fill_rgba=GLASS_FILL,
                             outline_rgba=GLASS_BORDER,
                             outline_width=1)

    ccy = data.chain_currency or data.network
    gross_color = (GREEN_PROFIT if (data.gross_profit or 0) >= 0 else RED_LOSS) if data.gross_profit is not None else TEXT_WHITE
    net_color   = (GREEN_PROFIT if (data.net_profit   or 0) >= 0 else RED_LOSS) if data.net_profit   is not None else TEXT_WHITE

    stats = [
        ("Invested",         _fmt_val(data.amount_invested,  suffix=f" {ccy}"),  TEXT_WHITE),
        ("Sold",             _fmt_val(data.amount_received,  suffix=f" {ccy}"),  TEXT_WHITE),
        ("Gross Profit",     _fmt_val(data.gross_profit,     suffix=f" {ccy}"),  gross_color),
        ("Net Profit",       _fmt_val(data.net_profit,       suffix=f" {ccy}"),  net_color),
        ("Buy Price",        _fmt_val(data.buy_price,        prefix="$"),         TEXT_WHITE),
        ("Sell Price",       _fmt_val(data.sell_price,       prefix="$"),         TEXT_WHITE),
        ("ROI",              _fmt_val(data.roi_pct,          suffix="%"),         TEXT_WHITE),
        ("Duration",         data.trade_duration,                                 TEXT_WHITE),
        ("Portfolio Before", _fmt_val(data.portfolio_before, prefix="$"),         TEXT_WHITE),
        ("Portfolio After",  _fmt_val(data.portfolio_after,  prefix="$"),         TEXT_WHITE),
        ("Date",             data.date,                                            TEXT_DIM),
        ("Time",             data.time_str,                                        TEXT_DIM),
        ("Network",          data.network,                                         CHAIN_COLORS.get(data.network, ACCENT_BLUE)),
        ("Wallet",           "Yours",                                              TEXT_DIM),
        ("Via",              "Copy Vault",                                         ACCENT_CYAN),
        ("Status",           "Completed ✓",                                       GREEN_PROFIT),
    ]

    cell_w = panel_w // cell_cols
    for i, (lbl, val, vc) in enumerate(stats):
        col = i % cell_cols
        row = i // cell_cols
        cx = panel_x + col * cell_w
        cy = panel_y + 15 + row * cell_h

        # Subtle vertical divider between columns
        if col == 1:
            for dy in range(cell_h - 16):
                draw.point((cx, cy + 8 + dy), fill=(*GLASS_BORDER[:3], 80))

        _draw_stat_cell(draw, cx, cy, cell_w, cell_h, lbl, str(val), vc)

    return panel_y + panel_h + 20


def _draw_footer(canvas: "Image.Image", draw: "ImageDraw.ImageDraw", y0: int) -> None:
    """QR code + tagline footer."""
    qr_size = 140
    qr = _get_qr(qr_size)

    pad = 54
    qr_x = pad
    qr_y = y0 + 10

    if qr:
        # QR container
        _draw_rounded_rect_rgba(canvas,
                                 (qr_x - 8, qr_y - 8, qr_x + qr_size + 8, qr_y + qr_size + 8),
                                 radius=12,
                                 fill_rgba=(16, 24, 52, 200),
                                 outline_rgba=(*ACCENT_BLUE, 100))
        canvas.alpha_composite(qr, dest=(qr_x, qr_y))

    # Tagline on the right side of QR
    tx = qr_x + qr_size + 30
    ty = qr_y + 10

    _glow_circle(canvas, tx + 120, ty + 50, 70, ACCENT_PURPLE, alpha=30)

    draw.text((tx, ty),      "Trade Smarter", font=_font(bold=True,  size=26), fill=TEXT_WHITE)
    draw.text((tx, ty + 34), "with Copy Vault", font=_font(bold=False, size=22), fill=ACCENT_CYAN)
    draw.text((tx, ty + 68), BOT_URL, font=_font(bold=False, size=17), fill=TEXT_DIM)

    # Bottom watermark
    wm = "© Copy Vault  •  All trades carry risk"
    wf = _font(bold=False, size=15)
    ww = _text_width(draw, wm, wf)
    draw.text(((W - ww) // 2, y0 + qr_size + 28), wm, font=wf, fill=TEXT_LABEL)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_trade_card(data: TradeCardData) -> bytes:
    """Generate a 1080×1350 PNG trade summary card.

    Parameters
    ----------
    data : TradeCardData
        All fields needed for the card; missing optional fields render as '—'.

    Returns
    -------
    bytes
        Raw PNG bytes suitable for sending as a Telegram photo/document.

    Raises
    ------
    CardGenerationError
        If image generation fails for any reason.
    """
    if not _PIL_OK:
        raise CardGenerationError("Pillow is not installed")

    try:
        canvas = _gradient_bg((W, H))
        draw = ImageDraw.Draw(canvas)

        _draw_header(canvas, draw, data)
        y = _draw_token_section(canvas, draw, data)
        y = _draw_profit_section(canvas, draw, data, y)
        y += 18
        y = _draw_details_panel(canvas, draw, data, y)
        _draw_footer(canvas, draw, y)

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True, compress_level=6)
        buf.seek(0)
        return buf.read()

    except Exception as exc:
        logger.exception("Card generation failed: %s", exc)
        raise CardGenerationError(str(exc)) from exc


# Warm the font cache at import time (non-blocking — runs synchronously once)
if _PIL_OK:
    try:
        _preload_fonts()
        _get_qr()          # also warm QR cache
        logger.info("Trade card generator ready (fonts + QR preloaded)")
    except Exception as _e:
        logger.warning("Card preload warning: %s", _e)
