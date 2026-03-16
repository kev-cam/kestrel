#!/usr/bin/env python3
"""Generate KiCad 10 schematic for Maneatis VCO delay cell."""

import uuid
import re

def uid():
    return str(uuid.uuid4())

# ── MOSFET lib_symbol definitions ──────────────────────────────

def mosfet_lib_symbol(name, is_pmos=False):
    """Generate an inline lib_symbol for a 4-terminal MOSFET.
    Connection points: G at (-5.08,0), D at (0,5.08), S at (0,-5.08), B at (5.08,0)
    Pin stubs extend 2.54mm from connection point toward symbol body.
    """
    desc = "PMOS" if is_pmos else "NMOS"

    if is_pmos:
        gfx = f"""\t\t\t(symbol "{name}_0_1"
\t\t\t\t(polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 1.27) (xy -0.508 -1.27))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 0.762) (xy 0 0.762) (xy 0 2.54))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 -0.762) (xy 0 -0.762) (xy 0 -2.54))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 0) (xy 2.54 0))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -2.54 0) (xy -1.778 0))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(circle (center -1.778 0) (radius 0.254)
\t\t\t\t\t(stroke (width 0) (type default)) (fill (type outline)))
\t\t\t\t(polyline (pts (xy 0.254 0.508) (xy -0.254 0) (xy 0.254 -0.508))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t)"""
    else:
        gfx = f"""\t\t\t(symbol "{name}_0_1"
\t\t\t\t(polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 1.27) (xy -0.508 -1.27))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 0.762) (xy 0 0.762) (xy 0 2.54))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 -0.762) (xy 0 -0.762) (xy 0 -2.54))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.508 0) (xy 2.54 0))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -2.54 0) (xy -1.27 0))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t\t(polyline (pts (xy -0.254 0.508) (xy 0.254 0) (xy -0.254 -0.508))
\t\t\t\t\t(stroke (width 0.254) (type default)) (fill (type none)))
\t\t\t)"""

    ef = "(effects (font (size 1.27 1.27)))"
    efh = "(effects (font (size 1.27 1.27)) (hide yes))"
    efl = "(effects (font (size 1.27 1.27)) (justify left))"

    # Pin positions: connection point at outer end, length extends inward.
    # G: connect at (-5.08, 0), extends right 2.54 to gate at (-2.54, 0)
    # D: connect at (0, 5.08), extends down 2.54 to drain at (0, 2.54)
    # S: connect at (0, -5.08), extends up 2.54 to source at (0, -2.54)
    # B: connect at (5.08, 0), extends left 2.54 to body at (2.54, 0)
    return f"""\t\t(symbol "kestrel:{name}"
\t\t\t(pin_names (offset 0.254) (hide yes))
\t\t\t(pin_numbers (hide yes))
\t\t\t(exclude_from_sim no)
\t\t\t(in_bom yes)
\t\t\t(on_board yes)
\t\t\t(property "Reference" "M"
\t\t\t\t(at 5.08 1.27 0)
\t\t\t\t{efl}
\t\t\t)
\t\t\t(property "Value" "{name}"
\t\t\t\t(at 5.08 -1.27 0)
\t\t\t\t{efl}
\t\t\t)
\t\t\t(property "Footprint" ""
\t\t\t\t(at 0 0 0)
\t\t\t\t{efh}
\t\t\t)
\t\t\t(property "Datasheet" ""
\t\t\t\t(at 0 0 0)
\t\t\t\t{efh}
\t\t\t)
\t\t\t(property "Description" "4-terminal {desc} transistor"
\t\t\t\t(at 0 0 0)
\t\t\t\t{efh}
\t\t\t)
{gfx}
\t\t\t(symbol "{name}_1_1"
\t\t\t\t(pin passive line (at -5.08 0 0) (length 2.54)
\t\t\t\t\t(name "G" {ef}) (number "1" {ef}))
\t\t\t\t(pin passive line (at 0 5.08 270) (length 2.54)
\t\t\t\t\t(name "D" {ef}) (number "2" {ef}))
\t\t\t\t(pin passive line (at 0 -5.08 90) (length 2.54)
\t\t\t\t\t(name "S" {ef}) (number "3" {ef}))
\t\t\t\t(pin passive line (at 5.08 0 180) (length 2.54)
\t\t\t\t\t(name "B" {ef}) (number "4" {ef}))
\t\t\t)
\t\t\t(embedded_fonts no)
\t\t)"""


def symbol_instance(lib_id, ref_des, value, at, mirror=None, project="kestrel_vco"):
    """Place a symbol instance."""
    ax, ay, ar = round(at[0], 4), round(at[1], 4), at[2]
    u = uid()
    ef = "(effects (font (size 1.0 1.0)) (justify left))"
    efh = "(effects (font (size 1.27 1.27)) (hide yes))"
    proj_uuid = uid()

    mirror_line = ""
    if mirror:
        mirror_line = f"\n\t\t(mirror {mirror})"

    pins = "\n".join(f'\t\t(pin "{n}" (uuid "{uid()}"))' for n in ["1","2","3","4"])

    return f"""\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {ax} {ay} {ar}){mirror_line}
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "{u}")
\t\t(property "Reference" "{ref_des}"
\t\t\t(at {ax + 3.81} {ay - 1.27} 0)
\t\t\t{ef}
\t\t)
\t\t(property "Value" "{value}"
\t\t\t(at {ax + 3.81} {ay + 1.27} 0)
\t\t\t{ef}
\t\t)
\t\t(property "Footprint" ""
\t\t\t(at {ax} {ay} 0)
\t\t\t{efh}
\t\t)
\t\t(property "Datasheet" ""
\t\t\t(at {ax} {ay} 0)
\t\t\t{efh}
\t\t)
\t\t(property "Description" ""
\t\t\t(at {ax} {ay} 0)
\t\t\t{efh}
\t\t)
{pins}
\t\t(instances
\t\t\t(project "{project}")
\t\t)
\t)"""


def wire(x1, y1, x2, y2):
    # Round to avoid floating point artifacts (e.g. 83.82000000000001)
    x1, y1, x2, y2 = round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)
    return f"""\t(wire
\t\t(pts (xy {x1} {y1}) (xy {x2} {y2}))
\t\t(stroke (width 0) (type solid))
\t\t(uuid "{uid()}")
\t)"""


def net_label(name, x, y, angle=0):
    x, y = round(x, 4), round(y, 4)
    return f"""\t(label "{name}"
\t\t(at {x} {y} {angle})
\t\t(effects (font (size 1.27 1.27)) (justify left bottom))
\t\t(uuid "{uid()}")
\t)"""


def junction(x, y):
    x, y = round(x, 4), round(y, 4)
    return f"""\t(junction
\t\t(at {x} {y})
\t\t(diameter 1.016)
\t\t(color 0 0 0 0)
\t\t(uuid "{uid()}")
\t)"""


def text_annotation(txt, x, y, sz=2.0):
    return f"""\t(text "{txt}"
\t\t(exclude_from_sim no)
\t\t(at {x} {y} 0)
\t\t(effects (font (size {sz} {sz})) (justify left bottom))
\t\t(uuid "{uid()}")
\t)"""


def generate():
    """
    Maneatis delay cell.  KiCad Y increases downward on screen.

    MOSFET default (0 deg, no mirror):
        G at (x-2.54, y)  D at (x, y-2.54)  S at (x, y+2.54)  B at (x+2.54, y)
        → drain UP, source DOWN  — correct for NFET (drain toward output, source toward VSS)

    PFET with (mirror x):
        Flips about X axis: Y coords of pins invert relative to symbol center.
        G at (x-5.08, y)  D at (x, y+5.08)  S at (x, y-5.08)  B at (x+5.08, y)
        → source UP (toward VDD), drain DOWN (toward output)  — correct for PFET
    """

    # All coordinates on 2.54mm grid (multiples of 2.54)
    g = 2.54
    # Y coordinates (top to bottom)
    # Pin stubs are 5.08mm (2*g) from symbol center
    VDD_Y   = 10 * g   # 25.4
    PFET_Y  = 18 * g   # 45.72  (source at y-2g=35.56, drain at y+2g=55.88)
    OUT_Y   = 26 * g   # 66.04  (output node between PFETs and NFETs)
    NFET_Y  = 34 * g   # 86.36  (drain at y-2g=76.2, source at y+2g=96.52)
    TAIL_Y  = 40 * g   # 101.6  (tail node)
    MTAIL_Y = 44 * g   # 111.76 (drain at y-2g=101.6=TAIL_Y, source at y+2g=121.92)
    VSS_Y   = 50 * g   # 127.0

    # X coordinates — wider spacing to accommodate body pins at x+2g
    MP1A_X  = 40 * g   # 101.6   left diode-connected PFET
    MP1B_X  = 50 * g   # 127.0   left vctrl PFET (need room for Mp1a body at 40g+2g=42g)
    MP2A_X  = 68 * g   # 172.72  right diode-connected PFET
    MP2B_X  = 78 * g   # 198.12  right vctrl PFET
    MN1_X   = 44 * g   # 111.76  diff pair left
    MN2_X   = 74 * g   # 187.96  diff pair right
    MTAIL_X = 58 * g   # 147.32  tail current source

    parts = []     # all s-expression items
    wires_list = []
    junc_set = set()  # (x,y) to deduplicate

    def W(x1, y1, x2, y2):
        wires_list.append(wire(x1, y1, x2, y2))

    def J(x, y):
        junc_set.add((round(x, 2), round(y, 2)))

    def L(name, x, y, angle=0):
        parts.append(net_label(name, x, y, angle))

    # ── Place symbols ──
    # PFETs (mirror y): S at (x, y-2.54), D at (x, y+2.54)
    parts.append(symbol_instance("kestrel:PMOS_4T", "Mp1a", "PMOS_4T", (MP1A_X, PFET_Y, 0), mirror="x"))
    parts.append(symbol_instance("kestrel:PMOS_4T", "Mp1b", "PMOS_4T", (MP1B_X, PFET_Y, 0), mirror="x"))
    parts.append(symbol_instance("kestrel:PMOS_4T", "Mp2a", "PMOS_4T", (MP2A_X, PFET_Y, 0), mirror="x"))
    parts.append(symbol_instance("kestrel:PMOS_4T", "Mp2b", "PMOS_4T", (MP2B_X, PFET_Y, 0), mirror="x"))

    # NFETs (no mirror): D at (x, y-2.54), S at (x, y+2.54)
    parts.append(symbol_instance("kestrel:NMOS_4T", "Mn1",   "NMOS_4T", (MN1_X, NFET_Y, 0)))
    parts.append(symbol_instance("kestrel:NMOS_4T", "Mn2",   "NMOS_4T", (MN2_X, NFET_Y, 0)))
    parts.append(symbol_instance("kestrel:NMOS_4T", "Mtail", "NMOS_4T", (MTAIL_X, MTAIL_Y, 0)))

    # Pin offset from symbol center to connection point
    p = 2 * g  # 5.08mm — pins have (length 2.54) starting at +/-5.08

    # NFET pin positions (no mirror):
    #   G at (x-p, y), D at (x, y-p), S at (x, y+p), B at (x+p, y)
    # PFET pin positions (mirror x — flips Y):
    #   G at (x-p, y), D at (x, y+p), S at (x, y-p), B at (x+p, y)

    # ── VDD rail ── (extend past Mp2b body at MP2B_X+p)
    W(MP1A_X, VDD_Y, MP2B_X + p, VDD_Y)

    # PFET sources to VDD  (S at y-p for mirror x PFET)
    for px in [MP1A_X, MP1B_X, MP2A_X, MP2B_X]:
        W(px, PFET_Y - p, px, VDD_Y)
        J(px, VDD_Y)

    # PFET bodies to VDD  (B at x+p)
    for px in [MP1A_X, MP1B_X, MP2A_X, MP2B_X]:
        bx = px + p
        W(bx, PFET_Y, bx, VDD_Y)
        J(bx, VDD_Y)

    # ── PFET drains to output nodes ──  (D at y+p for mirror x PFET)
    # Left output (outn)
    W(MP1A_X, PFET_Y + p, MP1A_X, OUT_Y)
    W(MP1B_X, PFET_Y + p, MP1B_X, OUT_Y)
    W(MP1A_X, OUT_Y, MP1B_X, OUT_Y)
    J(MP1A_X, OUT_Y)
    J(MP1B_X, OUT_Y)

    # Right output (outp)
    W(MP2A_X, PFET_Y + p, MP2A_X, OUT_Y)
    W(MP2B_X, PFET_Y + p, MP2B_X, OUT_Y)
    W(MP2A_X, OUT_Y, MP2B_X, OUT_Y)
    J(MP2A_X, OUT_Y)
    J(MP2B_X, OUT_Y)

    # ── Diode connections ──
    # Mp1a: gate to outn   (G at x-p)
    W(MP1A_X - p, PFET_Y, MP1A_X - p, OUT_Y)
    W(MP1A_X - p, OUT_Y, MP1A_X, OUT_Y)
    J(MP1A_X, OUT_Y)

    # Mp2a: gate to outp
    W(MP2A_X - p, PFET_Y, MP2A_X - p, OUT_Y)
    W(MP2A_X - p, OUT_Y, MP2A_X, OUT_Y)
    J(MP2A_X, OUT_Y)

    # ── Vctrl gates ──
    # Mp1b gate at (MP1B_X-p, PFET_Y), Mp2b gate at (MP2B_X-p, PFET_Y)
    VCTRL_Y = 14 * g  # 35.56
    g1x = MP1B_X - p
    g2x = MP2B_X - p
    W(g1x, PFET_Y, g1x, VCTRL_Y)
    W(g2x, PFET_Y, g2x, VCTRL_Y)
    W(g1x, VCTRL_Y, g2x, VCTRL_Y)
    L("vctrl", g1x - 3*g, VCTRL_Y)
    W(g1x - 3*g, VCTRL_Y, g1x, VCTRL_Y)

    # ── NFET drains to output nodes ──  (D at y-p)
    # Mn1 → outn
    W(MN1_X, NFET_Y - p, MN1_X, OUT_Y)
    W(MN1_X, OUT_Y, MP1A_X, OUT_Y)
    J(MN1_X, OUT_Y)
    J(MP1A_X, OUT_Y)

    # Mn2 → outp
    W(MN2_X, NFET_Y - p, MN2_X, OUT_Y)
    W(MN2_X, OUT_Y, MP2A_X, OUT_Y)
    J(MN2_X, OUT_Y)
    J(MP2A_X, OUT_Y)

    # ── NFET sources to tail ──  (S at y+p)
    W(MN1_X, NFET_Y + p, MN1_X, TAIL_Y)
    W(MN2_X, NFET_Y + p, MN2_X, TAIL_Y)
    # Single horizontal tail bus from Mn1 to Mn2
    W(MN1_X, TAIL_Y, MN2_X, TAIL_Y)
    # Vertical from tail bus down to Mtail drain
    W(MTAIL_X, TAIL_Y, MTAIL_X, MTAIL_Y - p)
    J(MN1_X, TAIL_Y)
    J(MN2_X, TAIL_Y)
    J(MTAIL_X, TAIL_Y)
    L("tail", MTAIL_X + g, TAIL_Y)
    W(MTAIL_X, TAIL_Y, MTAIL_X + g, TAIL_Y)

    # ── NFET bodies to VSS ──
    for nx in [MN1_X, MN2_X, MTAIL_X]:
        bx = nx + p
        W(bx, NFET_Y if nx != MTAIL_X else MTAIL_Y, bx, VSS_Y)
        J(bx, VSS_Y)

    # ── Mtail source to VSS ──
    W(MTAIL_X, MTAIL_Y + p, MTAIL_X, VSS_Y)
    J(MTAIL_X, VSS_Y)

    # ── VSS rail ── (from leftmost body to rightmost body)
    W(MN1_X + p, VSS_Y, MN2_X + p, VSS_Y)

    # ── Gate labels ──
    L("inp", MN1_X - p - 3*g, NFET_Y)
    W(MN1_X - p - 3*g, NFET_Y, MN1_X - p, NFET_Y)

    L("inn", MN2_X - p - 3*g, NFET_Y)
    W(MN2_X - p - 3*g, NFET_Y, MN2_X - p, NFET_Y)

    L("vbn", MTAIL_X - p - 3*g, MTAIL_Y)
    W(MTAIL_X - p - 3*g, MTAIL_Y, MTAIL_X - p, MTAIL_Y)

    # ── Output labels ──
    L("outn", MP1A_X - p - 3*g, OUT_Y)
    W(MP1A_X - p - 3*g, OUT_Y, MP1A_X - p, OUT_Y)

    L("outp", MP2B_X + 3*g, OUT_Y)
    W(MP2B_X, OUT_Y, MP2B_X + 3*g, OUT_Y)

    # ── Power labels ──
    L("vdd", MP1A_X - 3*g, VDD_Y)
    W(MP1A_X - 3*g, VDD_Y, MP1A_X, VDD_Y)
    J(MP1A_X, VDD_Y)

    L("vss", MN1_X + p - 3*g, VSS_Y)
    W(MN1_X + p - 3*g, VSS_Y, MN1_X + p, VSS_Y)
    J(MN1_X + p, VSS_Y)

    # ── Title ──
    title = text_annotation("Kestrel VCO \\u2014 Maneatis Delay Cell", 36*g, 8*g, 2.5)

    # ── Build junction items ──
    junctions_str = "\n".join(junction(x, y) for x, y in sorted(junc_set))

    # ── Assemble schematic ──
    sch_uuid = uid()
    lib_nmos = mosfet_lib_symbol("NMOS_4T", is_pmos=False)
    lib_pmos = mosfet_lib_symbol("PMOS_4T", is_pmos=True)

    sch = f"""(kicad_sch
\t(version 20250114)
\t(generator "python")
\t(generator_version "10.0")
\t(uuid "{sch_uuid}")
\t(paper "A3")
\t(lib_symbols
{lib_nmos}
{lib_pmos}
\t)
{title}
{junctions_str}
{chr(10).join(wires_list)}
{chr(10).join(parts)}
)
"""
    return sch


if __name__ == "__main__":
    sch = generate()
    outpath = "/usr/local/src/kestrel/sch/kes_vco_delay_cell.kicad_sch"
    with open(outpath, 'w') as f:
        f.write(sch)
    print(f"Written to {outpath}")
