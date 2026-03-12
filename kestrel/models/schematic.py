"""SVG schematic generator for PLL designs.

Draws block-level and transistor-level schematics from computed
design parameters.
"""

import os
from ..design.engine import PLLDesign, format_eng, get_process_params


# ---------------------------------------------------------------------------
# SVG primitives
# ---------------------------------------------------------------------------

class SVG:
    """Minimal SVG builder."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.elements = []
        self.defs = []

    def line(self, x1, y1, x2, y2, color="#222", width=1.5, dash=None):
        style = f'stroke:{color};stroke-width:{width};fill:none'
        if dash:
            style += f';stroke-dasharray:{dash}'
        self.elements.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" style="{style}"/>')

    def rect(self, x, y, w, h, fill="none", stroke="#222", sw=1.5, rx=0):
        r = f' rx="{rx}"' if rx else ''
        self.elements.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{r}/>')

    def circle(self, cx, cy, r, fill="none", stroke="#222", sw=1.5):
        self.elements.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def text(self, x, y, txt, size=11, anchor="middle", color="#222",
             weight="normal", family="monospace"):
        self.elements.append(
            f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
            f'fill="{color}" font-weight="{weight}" font-family="{family}">'
            f'{_esc(txt)}</text>')

    def polyline(self, pts, color="#222", width=1.5, fill="none"):
        p = " ".join(f"{x},{y}" for x, y in pts)
        self.elements.append(
            f'<polyline points="{p}" fill="{fill}" '
            f'stroke="{color}" stroke-width="{width}"/>')

    def polygon(self, pts, fill="#222", stroke="none", sw=0):
        p = " ".join(f"{x},{y}" for x, y in pts)
        self.elements.append(
            f'<polygon points="{p}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>')

    def group(self, transform=""):
        return SVGGroup(self, transform)

    def render(self) -> str:
        defs = "\n".join(self.defs)
        body = "\n".join(self.elements)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{self.width}" height="{self.height}" '
                f'viewBox="0 0 {self.width} {self.height}">\n'
                f'<defs>\n{defs}\n</defs>\n'
                f'<rect width="100%" height="100%" fill="white"/>\n'
                f'{body}\n</svg>')


class SVGGroup:
    def __init__(self, svg, transform):
        self.svg = svg
        self.transform = transform

    def __enter__(self):
        self.svg.elements.append(f'<g transform="{self.transform}">')
        return self.svg

    def __exit__(self, *args):
        self.svg.elements.append('</g>')


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _arrow(svg, x1, y1, x2, y2, color="#222", width=1.5):
    """Draw a line with an arrowhead at (x2, y2)."""
    import math
    svg.line(x1, y1, x2, y2, color=color, width=width)
    angle = math.atan2(y2 - y1, x2 - x1)
    alen = 8
    a1 = angle + math.radians(155)
    a2 = angle - math.radians(155)
    svg.polygon([
        (x2, y2),
        (x2 + alen * math.cos(a1), y2 + alen * math.sin(a1)),
        (x2 + alen * math.cos(a2), y2 + alen * math.sin(a2)),
    ], fill=color)


def _block(svg, x, y, w, h, label, sublabel="", fill="#e8f0fe"):
    """Draw a labeled block (rectangle with text)."""
    svg.rect(x, y, w, h, fill=fill, rx=4)
    svg.text(x + w/2, y + h/2 - (6 if sublabel else 0), label,
             size=13, weight="bold")
    if sublabel:
        svg.text(x + w/2, y + h/2 + 12, sublabel, size=9, color="#555")


def _nmos(svg, x, y, w=24, label="", flip=False):
    """Draw an NMOS symbol at (x, y) = gate pin location."""
    # Gate, drain, source terminals
    d = -1 if flip else 1
    # Gate line
    svg.line(x, y, x + 12*d, y)
    # Gate plate
    svg.line(x + 12*d, y - 10, x + 12*d, y + 10)
    # Channel
    svg.line(x + 15*d, y - 10, x + 15*d, y + 10)
    # Drain (top)
    svg.line(x + 15*d, y - 8, x + 24*d, y - 8)
    svg.line(x + 24*d, y - 8, x + 24*d, y - 16)
    # Source (bottom)
    svg.line(x + 15*d, y + 8, x + 24*d, y + 8)
    svg.line(x + 24*d, y + 8, x + 24*d, y + 16)
    # Body arrow
    svg.polygon([
        (x + 15*d, y),
        (x + 18*d, y - 3),
        (x + 18*d, y + 3),
    ], fill="#222")
    if label:
        svg.text(x + 14*d, y - 14, label, size=8, anchor="start" if not flip else "end",
                 color="#555")


def _pmos(svg, x, y, w=24, label="", flip=False):
    """Draw a PMOS symbol at (x, y) = gate pin location."""
    d = -1 if flip else 1
    svg.line(x, y, x + 9*d, y)
    # Gate bubble
    svg.circle(x + 10.5*d, y, 2, fill="white", stroke="#222", sw=1.2)
    # Gate plate
    svg.line(x + 12*d, y - 10, x + 12*d, y + 10)
    # Channel
    svg.line(x + 15*d, y - 10, x + 15*d, y + 10)
    # Source (top for PMOS)
    svg.line(x + 15*d, y - 8, x + 24*d, y - 8)
    svg.line(x + 24*d, y - 8, x + 24*d, y - 16)
    # Drain (bottom)
    svg.line(x + 15*d, y + 8, x + 24*d, y + 8)
    svg.line(x + 24*d, y + 8, x + 24*d, y + 16)
    if label:
        svg.text(x + 14*d, y - 14, label, size=8, anchor="start" if not flip else "end",
                 color="#555")


def _resistor(svg, x, y, vertical=True, label=""):
    """Draw a resistor. (x,y) is top terminal."""
    if vertical:
        svg.line(x, y, x, y + 5)
        svg.polyline([(x, y+5), (x-4, y+9), (x+4, y+13), (x-4, y+17),
                       (x+4, y+21), (x-4, y+25), (x, y+29)], width=1.2)
        svg.line(x, y + 29, x, y + 34)
        if label:
            svg.text(x + 8, y + 19, label, size=8, anchor="start", color="#555")
    else:
        svg.line(x, y, x + 5, y)
        svg.polyline([(x+5, y), (x+9, y-4), (x+13, y+4), (x+17, y-4),
                       (x+21, y+4), (x+25, y-4), (x+29, y)], width=1.2)
        svg.line(x + 29, y, x + 34, y)
        if label:
            svg.text(x + 17, y - 8, label, size=8, color="#555")


def _capacitor(svg, x, y, label=""):
    """Draw a capacitor vertically. (x,y) is top terminal."""
    svg.line(x, y, x, y + 12)
    svg.line(x - 8, y + 12, x + 8, y + 12)
    svg.line(x - 8, y + 16, x + 8, y + 16)
    svg.line(x, y + 16, x, y + 28)
    if label:
        svg.text(x + 12, y + 16, label, size=8, anchor="start", color="#555")


def _gnd(svg, x, y):
    """Draw a ground symbol at (x,y)."""
    svg.line(x, y, x, y + 6)
    svg.line(x - 8, y + 6, x + 8, y + 6)
    svg.line(x - 5, y + 10, x + 5, y + 10)
    svg.line(x - 2, y + 14, x + 2, y + 14)


def _vdd(svg, x, y, label="VDD"):
    """Draw a VDD symbol at (x,y)."""
    svg.line(x, y, x, y - 6)
    svg.line(x - 8, y - 6, x + 8, y - 6)
    svg.text(x, y - 12, label, size=8, color="#c00")


def _dot(svg, x, y):
    """Draw a junction dot."""
    svg.circle(x, y, 2.5, fill="#222", stroke="none")


def _wire_label(svg, x, y, label, anchor="middle"):
    svg.text(x, y, label, size=9, anchor=anchor, color="#0066cc", weight="bold")


# ======================================================================
# Top-level PLL block diagram
# ======================================================================

def draw_pll_block(d: PLLDesign, path: str):
    """Draw the PLL block diagram showing signal flow."""
    svg = SVG(960, 520)
    proc = get_process_params(d.spec.process)

    # Title
    svg.text(480, 28, f"Kestrel PLL — {format_eng(d.spec.freq_min, 'Hz')} to "
             f"{format_eng(d.spec.freq_max, 'Hz')} on {d.spec.process}",
             size=16, weight="bold")
    svg.text(480, 46, f"Maneatis self-biased charge-pump PLL, "
             f"{d.spec.vco_stages}-stage differential ring VCO",
             size=11, color="#555")

    bw = 130   # block width
    bh = 60    # block height
    y_main = 150  # main signal path y

    # --- PFD ---
    x_pfd = 80
    _block(svg, x_pfd, y_main, bw, bh, "PFD", "tri-state", fill="#e8f0fe")
    svg.text(x_pfd + bw/2, y_main + bh + 16,
             f"NAND-based", size=8, color="#888")

    # --- Charge Pump ---
    x_cp = 260
    _block(svg, x_cp, y_main, bw, bh, "Charge Pump",
           f"Icp = {format_eng(d.icp, 'A')}", fill="#fce8e6")

    # --- Loop Filter ---
    x_lf = 440
    _block(svg, x_lf, y_main, bw, bh, "Loop Filter",
           f"R={format_eng(d.r_filter, chr(937))}", fill="#fef7e0")
    svg.text(x_lf + bw/2, y_main + bh + 16,
             f"C1={format_eng(d.c1, 'F')}  C2={format_eng(d.c2, 'F')}",
             size=8, color="#888")

    # --- VCO ---
    x_vco = 640
    _block(svg, x_vco, y_main, bw + 20, bh, "VCO",
           f"Kvco = {format_eng(d.kvco, 'Hz/V')}", fill="#e6f4ea")
    svg.text(x_vco + (bw+20)/2, y_main + bh + 16,
             f"{d.spec.vco_stages}-stage diff ring, "
             f"f0 = {format_eng(d.f_center, 'Hz')}",
             size=8, color="#888")

    # --- Divider ---
    x_div = 640
    y_div = 340
    _block(svg, x_div, y_div, bw + 20, bh, "Divider",
           f"N = {d.n_min}..{d.n_max}", fill="#f3e8fd")
    svg.text(x_div + (bw+20)/2, y_div + bh + 16,
             f"{d.div_stages}x TSPC div-by-2", size=8, color="#888")

    # --- Wires: main signal path ---
    # ref_clk -> PFD
    _arrow(svg, 40, y_main + 20, x_pfd, y_main + 20)
    _wire_label(svg, 30, y_main + 16, "ref_clk", anchor="end")

    # PFD -> CP (UP/DN)
    _arrow(svg, x_pfd + bw, y_main + 18, x_cp, y_main + 18)
    _wire_label(svg, (x_pfd + bw + x_cp) / 2, y_main + 12, "UP")
    _arrow(svg, x_pfd + bw, y_main + 42, x_cp, y_main + 42)
    _wire_label(svg, (x_pfd + bw + x_cp) / 2, y_main + 36, "DN")

    # CP -> LF
    _arrow(svg, x_cp + bw, y_main + bh/2, x_lf, y_main + bh/2)
    _wire_label(svg, (x_cp + bw + x_lf) / 2, y_main + bh/2 - 8, "Icp")

    # LF -> VCO
    _arrow(svg, x_lf + bw, y_main + bh/2, x_vco, y_main + bh/2)
    _wire_label(svg, (x_lf + bw + x_vco) / 2, y_main + bh/2 - 8, "Vctrl")

    # VCO -> output
    x_out = x_vco + bw + 20
    _arrow(svg, x_out, y_main + 20, x_out + 80, y_main + 20)
    _wire_label(svg, x_out + 90, y_main + 16, "clk_out", anchor="start")
    _arrow(svg, x_out, y_main + 40, x_out + 80, y_main + 40)
    _wire_label(svg, x_out + 90, y_main + 36, "clk_outb", anchor="start")

    # VCO output -> Divider (feedback path)
    x_fb_right = x_out + 30
    svg.line(x_out, y_main + 20, x_fb_right, y_main + 20)
    _dot(svg, x_fb_right, y_main + 20)
    svg.line(x_fb_right, y_main + 20, x_fb_right, y_div + bh/2)
    _arrow(svg, x_fb_right, y_div + bh/2, x_div + bw + 20, y_div + bh/2)

    # Divider -> PFD (feedback)
    x_fb_left = 50
    svg.line(x_div, y_div + bh/2, x_fb_left, y_div + bh/2)
    svg.line(x_fb_left, y_div + bh/2, x_fb_left, y_main + 42)
    _arrow(svg, x_fb_left, y_main + 42, x_pfd, y_main + 42)
    _wire_label(svg, x_fb_left - 6, y_main + 38, "fb_clk", anchor="end")

    # --- Parameter summary box ---
    y_info = 440
    svg.rect(30, y_info, 900, 60, fill="#f8f9fa", stroke="#ccc", rx=4)
    cols = [
        (50, [f"Process: {d.spec.process}",
              f"VDD: {d.spec.supply_voltage}V"]),
        (230, [f"Phase margin: {d.pm_actual:.1f}{chr(176)}",
               f"Damping: {d.damping:.2f}"]),
        (430, [f"Lock time: ~{format_eng(d.lock_time, 's')}",
               f"Jitter: ~{format_eng(d.jitter_est, 's')} rms"]),
        (660, [f"NFET: {proc['nfet']}",
               f"PFET: {proc['pfet']}"]),
    ]
    for cx, lines in cols:
        for i, line in enumerate(lines):
            svg.text(cx, y_info + 20 + i * 16, line, size=9,
                     anchor="start", color="#444", family="monospace")

    with open(path, "w") as f:
        f.write(svg.render())


# ======================================================================
# VCO delay cell transistor schematic
# ======================================================================

def draw_delay_cell(d: PLLDesign, path: str):
    """Draw the Maneatis delay cell transistor schematic."""
    svg = SVG(700, 500)
    proc = get_process_params(d.spec.process)

    svg.text(350, 28, "Maneatis Delay Cell — Transistor Level",
             size=15, weight="bold")
    svg.text(350, 46, f"{d.spec.process} | VDD = {d.spec.supply_voltage}V | "
             f"I_stage = {format_eng(d.vco_i_stage, 'A')}",
             size=10, color="#555")

    # Layout: symmetric around x=350
    # VDD rail at top, VSS rail at bottom
    y_vdd = 80
    y_load = 130
    y_diff_d = 210   # diff pair drain
    y_diff = 260     # diff pair gate
    y_tail_d = 310   # tail drain
    y_tail = 350     # tail gate
    y_vss = 410

    x_left = 180     # left half
    x_right = 520    # right half
    x_center = 350

    # VDD rail
    svg.line(100, y_vdd, 600, y_vdd, color="#c00", width=2)
    svg.text(80, y_vdd + 4, "VDD", size=10, color="#c00", anchor="end")

    # VSS rail
    svg.line(100, y_vss, 600, y_vss, color="#00c", width=2)
    svg.text(80, y_vss + 4, "VSS", size=10, color="#00c", anchor="end")

    # --- Left PMOS loads (Mp1a diode, Mp1b controlled) ---
    # Mp1a: diode-connected (gate=drain=outn)
    x_l_load = x_left
    svg.line(x_l_load, y_vdd, x_l_load, y_load - 16)  # VDD to source
    _pmos(svg, x_l_load - 24, y_load, label="Mp1a")
    svg.line(x_l_load, y_load + 16, x_l_load, y_load + 30)  # drain down
    # Diode connection
    svg.line(x_l_load, y_load + 30, x_l_load - 30, y_load + 30)
    svg.line(x_l_load - 30, y_load + 30, x_l_load - 30, y_load)
    svg.line(x_l_load - 30, y_load, x_l_load - 24, y_load)

    # Mp1b: controlled by Vctrl
    y_load2 = y_load + 50
    svg.line(x_l_load, y_load + 30, x_l_load, y_load2 - 16)
    _pmos(svg, x_l_load - 24, y_load2, label="Mp1b")
    svg.line(x_l_load, y_load2 + 16, x_l_load, y_diff_d)
    # Vctrl label on gate
    svg.line(x_l_load - 24, y_load2, x_l_load - 50, y_load2)
    _wire_label(svg, x_l_load - 56, y_load2 + 4, "Vctrl", anchor="end")

    # --- Right PMOS loads (Mp2a diode, Mp2b controlled) ---
    x_r_load = x_right
    svg.line(x_r_load, y_vdd, x_r_load, y_load - 16)
    _pmos(svg, x_r_load - 24, y_load, label="Mp2a", flip=False)
    svg.line(x_r_load, y_load + 16, x_r_load, y_load + 30)
    svg.line(x_r_load, y_load + 30, x_r_load + 30, y_load + 30)
    svg.line(x_r_load + 30, y_load + 30, x_r_load + 30, y_load)
    svg.line(x_r_load + 30, y_load, x_r_load + 24, y_load)

    y_load2r = y_load + 50
    svg.line(x_r_load, y_load + 30, x_r_load, y_load2r - 16)
    _pmos(svg, x_r_load - 24, y_load2r, label="Mp2b")
    svg.line(x_r_load, y_load2r + 16, x_r_load, y_diff_d)
    svg.line(x_r_load - 24, y_load2r, x_r_load - 50, y_load2r)
    # Connect to same Vctrl
    svg.line(x_l_load - 50, y_load2, x_l_load - 50, y_load2r)
    svg.line(x_l_load - 50, y_load2r, x_r_load - 50, y_load2r)
    svg.line(x_r_load - 50, y_load2r, x_r_load - 24, y_load2r)

    # --- Differential pair ---
    # Mn1 (left): gate=inp, drain=outn
    svg.line(x_left, y_diff_d, x_left, y_diff - 16)
    _nmos(svg, x_left - 24, y_diff, label="Mn1")
    svg.line(x_left - 24, y_diff, x_left - 50, y_diff)
    _wire_label(svg, x_left - 56, y_diff + 4, "inp", anchor="end")
    svg.line(x_left, y_diff + 16, x_left, y_tail_d)

    # Mn2 (right): gate=inn, drain=outp
    svg.line(x_right, y_diff_d, x_right, y_diff - 16)
    _nmos(svg, x_right - 24, y_diff, label="Mn2")
    svg.line(x_right + 24, y_diff, x_right + 50, y_diff)
    _wire_label(svg, x_right + 56, y_diff + 4, "inn", anchor="start")
    svg.line(x_right, y_diff + 16, x_right, y_tail_d)

    # Common source (tail) node
    svg.line(x_left, y_tail_d, x_right, y_tail_d)
    _dot(svg, x_center, y_tail_d)

    # --- Tail current source ---
    svg.line(x_center, y_tail_d, x_center, y_tail - 16)
    _nmos(svg, x_center - 24, y_tail, label="Mtail")
    svg.line(x_center, y_tail + 16, x_center, y_vss)
    # Bias gate
    svg.line(x_center - 24, y_tail, x_center - 55, y_tail)
    _wire_label(svg, x_center - 60, y_tail + 4, "Vbn", anchor="end")

    # --- Output labels ---
    _dot(svg, x_left, y_diff_d)
    svg.line(x_left, y_diff_d, x_left - 40, y_diff_d)
    _wire_label(svg, x_left - 46, y_diff_d + 4, "outn", anchor="end")

    _dot(svg, x_right, y_diff_d)
    svg.line(x_right, y_diff_d, x_right + 40, y_diff_d)
    _wire_label(svg, x_right + 46, y_diff_d + 4, "outp", anchor="start")

    # --- Sizing annotations ---
    y_annot = 450
    svg.rect(30, y_annot, 640, 40, fill="#f8f9fa", stroke="#ccc", rx=3)
    sizing = (
        f"Diff pair: W/L = {_um(d.vco_diff_w)}/{_um(d.vco_diff_l)}    "
        f"Tail: W/L = {_um(d.vco_tail_w)}/{_um(d.vco_tail_l)}    "
        f"Load: W/L = {_um(d.vco_load_w)}/{_um(d.vco_load_l)}"
    )
    svg.text(350, y_annot + 24, sizing, size=9, color="#444", family="monospace")

    with open(path, "w") as f:
        f.write(svg.render())


def _um(val):
    return f"{val*1e6:.2f}um"


# ======================================================================
# Charge pump transistor schematic
# ======================================================================

def draw_charge_pump(d: PLLDesign, path: str):
    """Draw the charge pump transistor schematic."""
    svg = SVG(500, 450)
    proc = get_process_params(d.spec.process)

    svg.text(250, 28, "Charge Pump — Transistor Level", size=15, weight="bold")
    svg.text(250, 46, f"{d.spec.process} | Icp = {format_eng(d.icp, 'A')}",
             size=10, color="#555")

    y_vdd = 80
    y_vss = 380
    x_center = 250

    # VDD / VSS rails
    svg.line(80, y_vdd, 420, y_vdd, color="#c00", width=2)
    svg.text(65, y_vdd + 4, "VDD", size=10, color="#c00", anchor="end")
    svg.line(80, y_vss, 420, y_vss, color="#00c", width=2)
    svg.text(65, y_vss + 4, "VSS", size=10, color="#00c", anchor="end")

    # UP current source (PMOS mirror)
    y_p_src = 130
    svg.line(x_center, y_vdd, x_center, y_p_src - 16)
    _pmos(svg, x_center - 24, y_p_src, label="Mp_src")
    # Gate connected to Pbias
    svg.line(x_center - 24, y_p_src, x_center - 60, y_p_src)
    _wire_label(svg, x_center - 66, y_p_src + 4, "Pbias", anchor="end")

    # UP switch (PMOS)
    y_p_sw = 200
    svg.line(x_center, y_p_src + 16, x_center, y_p_sw - 16)
    _pmos(svg, x_center - 24, y_p_sw, label="Mp_sw")
    svg.line(x_center - 24, y_p_sw, x_center - 60, y_p_sw)
    _wire_label(svg, x_center - 66, y_p_sw + 4, "UP_b", anchor="end")

    # Output node
    y_out = 260
    svg.line(x_center, y_p_sw + 16, x_center, y_out)
    _dot(svg, x_center, y_out)
    svg.line(x_center, y_out, x_center + 80, y_out)
    _wire_label(svg, x_center + 86, y_out + 4, "OUT", anchor="start")

    # DN switch (NMOS)
    y_n_sw = 300
    svg.line(x_center, y_out, x_center, y_n_sw - 16)
    _nmos(svg, x_center - 24, y_n_sw, label="Mn_sw")
    svg.line(x_center - 24, y_n_sw, x_center - 60, y_n_sw)
    _wire_label(svg, x_center - 66, y_n_sw + 4, "DN", anchor="end")

    # DN current source (NMOS mirror)
    y_n_src = 350
    svg.line(x_center, y_n_sw + 16, x_center, y_n_src - 16)
    _nmos(svg, x_center - 24, y_n_src, label="Mn_src")
    svg.line(x_center, y_n_src + 16, x_center, y_vss)
    svg.line(x_center - 24, y_n_src, x_center - 60, y_n_src)
    _wire_label(svg, x_center - 66, y_n_src + 4, "Nbias", anchor="end")

    # Sizing box
    svg.rect(30, 400, 440, 35, fill="#f8f9fa", stroke="#ccc", rx=3)
    svg.text(250, 422,
             f"UP PMOS: {_um(d.cp_up_w)}/{_um(d.cp_up_l)}   "
             f"DN NMOS: {_um(d.cp_dn_w)}/{_um(d.cp_dn_l)}   "
             f"Switch: {_um(d.cp_sw_w)}/{_um(d.cp_sw_l)}",
             size=9, color="#444", family="monospace")

    with open(path, "w") as f:
        f.write(svg.render())


# ======================================================================
# Loop filter schematic
# ======================================================================

def draw_loop_filter(d: PLLDesign, path: str):
    """Draw the loop filter schematic."""
    svg = SVG(400, 300)

    svg.text(200, 28, "Loop Filter", size=15, weight="bold")

    x_in = 40
    y_mid = 140

    # Input
    svg.line(x_in, y_mid, x_in + 40, y_mid)
    _wire_label(svg, x_in - 6, y_mid + 4, "IN", anchor="end")
    _dot(svg, x_in + 40, y_mid)

    # C2 branch (shunt from input)
    svg.line(x_in + 40, y_mid, x_in + 40, y_mid + 10)
    _capacitor(svg, x_in + 40, y_mid + 10, label=f"C2\n{format_eng(d.c2, 'F')}")
    svg.line(x_in + 40, y_mid + 38, x_in + 40, y_mid + 50)
    _gnd(svg, x_in + 40, y_mid + 50)

    # R1 series
    svg.line(x_in + 40, y_mid, x_in + 80, y_mid)
    _resistor(svg, x_in + 80, y_mid, vertical=False,
              label=f"R1 = {format_eng(d.r_filter, chr(937))}")
    svg.line(x_in + 114, y_mid, x_in + 160, y_mid)

    # C1 (main integrating cap)
    _dot(svg, x_in + 160, y_mid)
    svg.line(x_in + 160, y_mid, x_in + 160, y_mid + 10)
    _capacitor(svg, x_in + 160, y_mid + 10, label=f"C1\n{format_eng(d.c1, 'F')}")
    svg.line(x_in + 160, y_mid + 38, x_in + 160, y_mid + 50)
    _gnd(svg, x_in + 160, y_mid + 50)

    # Output
    svg.line(x_in + 160, y_mid, x_in + 240, y_mid)
    _wire_label(svg, x_in + 246, y_mid + 4, "Vctrl", anchor="start")

    with open(path, "w") as f:
        f.write(svg.render())


# ======================================================================
# Public API
# ======================================================================

def emit_schematics(design: PLLDesign, output_dir: str) -> list:
    """Generate all schematic SVGs.  Returns list of paths."""
    os.makedirs(output_dir, exist_ok=True)
    files = []

    p = os.path.join(output_dir, "kestrel_pll_block.svg")
    draw_pll_block(design, p)
    files.append(p)

    p = os.path.join(output_dir, "kestrel_delay_cell.svg")
    draw_delay_cell(design, p)
    files.append(p)

    p = os.path.join(output_dir, "kestrel_charge_pump.svg")
    draw_charge_pump(design, p)
    files.append(p)

    p = os.path.join(output_dir, "kestrel_loop_filter.svg")
    draw_loop_filter(design, p)
    files.append(p)

    return files
