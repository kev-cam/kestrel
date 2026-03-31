"""Shared SVG schematic drawing primitives for Kestrel generators."""

import math


# ---------------------------------------------------------------------------
# SVG builder
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

def arrow(svg, x1, y1, x2, y2, color="#222", width=1.5):
    """Draw a line with an arrowhead at (x2, y2)."""
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


def block(svg, x, y, w, h, label, sublabel="", fill="#e8f0fe"):
    """Draw a labeled block (rectangle with text)."""
    svg.rect(x, y, w, h, fill=fill, rx=4)
    svg.text(x + w/2, y + h/2 - (6 if sublabel else 0), label,
             size=13, weight="bold")
    if sublabel:
        svg.text(x + w/2, y + h/2 + 12, sublabel, size=9, color="#555")


def nmos(svg, x, y, w=24, label="", flip=False):
    """Draw an NMOS symbol at (x, y) = gate pin location."""
    d = -1 if flip else 1
    svg.line(x, y, x + 12*d, y)
    svg.line(x + 12*d, y - 10, x + 12*d, y + 10)
    svg.line(x + 15*d, y - 10, x + 15*d, y + 10)
    svg.line(x + 15*d, y - 8, x + 24*d, y - 8)
    svg.line(x + 24*d, y - 8, x + 24*d, y - 16)
    svg.line(x + 15*d, y + 8, x + 24*d, y + 8)
    svg.line(x + 24*d, y + 8, x + 24*d, y + 16)
    svg.polygon([
        (x + 15*d, y),
        (x + 18*d, y - 3),
        (x + 18*d, y + 3),
    ], fill="#222")
    if label:
        svg.text(x + 14*d, y - 14, label, size=8,
                 anchor="start" if not flip else "end", color="#555")


def pmos(svg, x, y, w=24, label="", flip=False):
    """Draw a PMOS symbol at (x, y) = gate pin location."""
    d = -1 if flip else 1
    svg.line(x, y, x + 9*d, y)
    svg.circle(x + 10.5*d, y, 2, fill="white", stroke="#222", sw=1.2)
    svg.line(x + 12*d, y - 10, x + 12*d, y + 10)
    svg.line(x + 15*d, y - 10, x + 15*d, y + 10)
    svg.line(x + 15*d, y - 8, x + 24*d, y - 8)
    svg.line(x + 24*d, y - 8, x + 24*d, y - 16)
    svg.line(x + 15*d, y + 8, x + 24*d, y + 8)
    svg.line(x + 24*d, y + 8, x + 24*d, y + 16)
    if label:
        svg.text(x + 14*d, y - 14, label, size=8,
                 anchor="start" if not flip else "end", color="#555")


def resistor(svg, x, y, vertical=True, label=""):
    """Draw a resistor. (x,y) is top terminal (vertical) or left terminal."""
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


def capacitor(svg, x, y, label=""):
    """Draw a capacitor vertically. (x,y) is top terminal."""
    svg.line(x, y, x, y + 12)
    svg.line(x - 8, y + 12, x + 8, y + 12)
    svg.line(x - 8, y + 16, x + 8, y + 16)
    svg.line(x, y + 16, x, y + 28)
    if label:
        svg.text(x + 12, y + 16, label, size=8, anchor="start", color="#555")


def inductor(svg, x, y, vertical=True, label=""):
    """Draw an inductor. (x,y) is top terminal (vertical) or left terminal."""
    if vertical:
        svg.line(x, y, x, y + 5)
        # Three bumps
        for i in range(3):
            cy = y + 10 + i * 8
            svg.elements.append(
                f'<path d="M {x},{cy-3} A 4,4 0 0,1 {x},{cy+5}" '
                f'fill="none" stroke="#222" stroke-width="1.2"/>')
        svg.line(x, y + 29, x, y + 34)
        if label:
            svg.text(x + 10, y + 19, label, size=8, anchor="start", color="#555")
    else:
        svg.line(x, y, x + 5, y)
        for i in range(3):
            cx = x + 10 + i * 8
            svg.elements.append(
                f'<path d="M {cx-3},{y} A 4,4 0 0,0 {cx+5},{y}" '
                f'fill="none" stroke="#222" stroke-width="1.2"/>')
        svg.line(x + 29, y, x + 34, y)
        if label:
            svg.text(x + 17, y - 8, label, size=8, color="#555")


def gnd(svg, x, y):
    """Draw a ground symbol at (x,y)."""
    svg.line(x, y, x, y + 6)
    svg.line(x - 8, y + 6, x + 8, y + 6)
    svg.line(x - 5, y + 10, x + 5, y + 10)
    svg.line(x - 2, y + 14, x + 2, y + 14)


def vdd(svg, x, y, label="VDD"):
    """Draw a VDD symbol at (x,y)."""
    svg.line(x, y, x, y - 6)
    svg.line(x - 8, y - 6, x + 8, y - 6)
    svg.text(x, y - 12, label, size=8, color="#c00")


def dot(svg, x, y):
    """Draw a junction dot."""
    svg.circle(x, y, 2.5, fill="#222", stroke="none")


def wire_label(svg, x, y, label, anchor="middle"):
    svg.text(x, y, label, size=9, anchor=anchor, color="#0066cc", weight="bold")


def switch(svg, x, y, vertical=True, label="", closed=False):
    """Draw a switch symbol. (x,y) is top/left terminal."""
    if vertical:
        svg.line(x, y, x, y + 8)
        if closed:
            svg.line(x, y + 8, x, y + 26)
        else:
            svg.line(x, y + 8, x + 8, y + 26)
        svg.circle(x, y + 8, 2, fill="#222", stroke="none")
        svg.circle(x, y + 26, 2, fill="#222", stroke="none")
        svg.line(x, y + 26, x, y + 34)
        if label:
            svg.text(x + 12, y + 19, label, size=8, anchor="start", color="#555")
    else:
        svg.line(x, y, x + 8, y)
        if closed:
            svg.line(x + 8, y, x + 26, y)
        else:
            svg.line(x + 8, y, x + 26, y - 8)
        svg.circle(x + 8, y, 2, fill="#222", stroke="none")
        svg.circle(x + 26, y, 2, fill="#222", stroke="none")
        svg.line(x + 26, y, x + 34, y)
        if label:
            svg.text(x + 17, y - 12, label, size=8, color="#555")
