"""GDSII layout generator for Kestrel PLL — sky130 process.

Generates physical layout from the PLLDesign transistor netlist using
gdsfactory.  Each PLL block (VCO delay cell, bias, charge pump, PFD,
loop filter, divider) is a parameterized cell built from MOSFET
primitives with sky130 layer definitions.

Usage:
    from kestrel.design.engine import PLLSpec, design_pll
    from layout.gds_gen import generate_pll_gds

    spec = PLLSpec(freq_min=400e6, freq_max=800e6, ref_freq=10e6,
                   loop_bw=1e6, process="sky130")
    design = design_pll(spec)
    generate_pll_gds(design, "kestrel_pll.gds")
"""

import math
import gdsfactory as gf
from gdsfactory.component import Component

# ======================================================================
# Sky130 layer definitions (layer, datatype)
# From: sky130A/libs.tech/klayout/tech/sky130A.lyp
# ======================================================================

DIFF    = (65, 20)   # diffusion (active)
TAP     = (65, 44)   # substrate/well tap
NWELL   = (64, 20)   # N-well
POLY    = (66, 20)   # polysilicon gate
NSDM    = (93, 44)   # N+ source/drain implant
PSDM    = (94, 20)   # P+ source/drain implant
LICON   = (66, 44)   # local interconnect contact
LI      = (67, 20)   # local interconnect (li1)
MCON    = (67, 44)   # metal1 contact (via to li1)
MET1    = (68, 20)   # metal 1
VIA1    = (68, 44)   # via1
MET2    = (69, 20)   # metal 2
VIA2    = (69, 44)   # via2
MET3    = (70, 20)   # metal 3
VIA3    = (70, 44)   # via3
MET4    = (71, 20)   # metal 4
VIA4    = (71, 44)   # via4
MET5    = (72, 20)   # metal 5
TEXT    = (83, 44)   # text label

# ======================================================================
# Sky130 design rules (um) — conservative values
# ======================================================================

POLY_EXT_DIFF   = 0.130   # poly extension beyond diffusion
DIFF_EXT_POLY   = 0.250   # diffusion extension beyond poly (S/D)
POLY_SPACE      = 0.210   # poly-to-poly spacing
POLY_WIDTH_MIN  = 0.150   # minimum poly width
DIFF_SPACE      = 0.270   # diffusion-to-diffusion spacing
NWELL_ENC_DIFF  = 0.180   # nwell enclosure of p+ diffusion
NWELL_SPACE     = 1.270   # nwell-to-nwell spacing
LICON_SIZE      = 0.170   # licon square side
LICON_SPACE     = 0.170   # licon-to-licon spacing
LICON_ENC_DIFF  = 0.040   # diff enclosure of licon
LICON_ENC_LI    = 0.080   # li enclosure of licon
LI_WIDTH        = 0.170   # li minimum width
LI_SPACE        = 0.170   # li-to-li spacing
MCON_SIZE       = 0.170   # mcon square side
MCON_ENC_LI     = 0.000   # li enclosure of mcon (can be zero per rules)
MCON_ENC_MET1   = 0.030   # met1 enclosure of mcon
MET1_WIDTH      = 0.140   # met1 minimum width
MET1_SPACE      = 0.140   # met1-to-met1 spacing
VIA1_SIZE       = 0.150   # via1 square side
VIA1_ENC_MET1   = 0.055   # met1 enclosure of via1
VIA1_ENC_MET2   = 0.055   # met2 enclosure of via1
MET2_WIDTH      = 0.140
MET2_SPACE      = 0.140

# Derived constants
LICON_PITCH = LICON_SIZE + LICON_SPACE  # 0.34
MCON_PITCH  = MCON_SIZE + LICON_SPACE   # 0.34


def _um(meters: float) -> float:
    """Convert meters to micrometers."""
    return meters * 1e6


def _snap(val: float, grid: float = 0.005) -> float:
    """Snap a value to the manufacturing grid."""
    return round(val / grid) * grid


def _port_width(val: float) -> float:
    """Snap port width to even grid (gdsfactory requirement)."""
    # Ports require dwidth to be a multiple of 2 * dbu (0.001 um)
    # Round to nearest 0.002 um
    snapped = round(val / 0.002) * 0.002
    return max(0.002, snapped)


# ======================================================================
# Contact / via array helpers
# ======================================================================

def _contact_array(c: Component, layer: tuple, enc_layer: tuple,
                   size: float, pitch: float, enc: float,
                   x0: float, y0: float, w: float, h: float):
    """Place an array of square contacts within a bounding box.

    Also draws the enclosure rectangle on enc_layer.
    """
    nx = max(1, int((w - 2 * enc) / pitch))
    ny = max(1, int((h - 2 * enc) / pitch))
    # center the array
    arr_w = (nx - 1) * pitch + size
    arr_h = (ny - 1) * pitch + size
    sx = x0 + (w - arr_w) / 2
    sy = y0 + (h - arr_h) / 2
    for ix in range(nx):
        for iy in range(ny):
            cx = sx + ix * pitch
            cy = sy + iy * pitch
            c.add_polygon(
                [(cx, cy), (cx + size, cy),
                 (cx + size, cy + size), (cx, cy + size)],
                layer=layer,
            )
    # enclosure
    c.add_polygon(
        [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
        layer=enc_layer,
    )


def _licon_stack(c: Component, x: float, y: float,
                 w: float, h: float):
    """Draw licon array + LI rectangle + MCON array + MET1 rectangle."""
    enc_li = LICON_ENC_LI
    # LI rectangle (enclosing licons)
    c.add_polygon(
        [(x - enc_li, y - enc_li),
         (x + w + enc_li, y - enc_li),
         (x + w + enc_li, y + h + enc_li),
         (x - enc_li, y + h + enc_li)],
        layer=LI,
    )
    # licon contacts
    nx = max(1, int(w / LICON_PITCH))
    ny = max(1, int(h / LICON_PITCH))
    arr_w = (nx - 1) * LICON_PITCH + LICON_SIZE
    arr_h = (ny - 1) * LICON_PITCH + LICON_SIZE
    sx = x + (w - arr_w) / 2
    sy = y + (h - arr_h) / 2
    for ix in range(nx):
        for iy in range(ny):
            cx = sx + ix * LICON_PITCH
            cy = sy + iy * LICON_PITCH
            c.add_polygon(
                [(cx, cy), (cx + LICON_SIZE, cy),
                 (cx + LICON_SIZE, cy + LICON_SIZE),
                 (cx, cy + LICON_SIZE)],
                layer=LICON,
            )
    # MCON + MET1 (for routing access)
    m1_enc = MCON_ENC_MET1
    c.add_polygon(
        [(x - m1_enc, y - m1_enc),
         (x + w + m1_enc, y - m1_enc),
         (x + w + m1_enc, y + h + m1_enc),
         (x - m1_enc, y + h + m1_enc)],
        layer=MET1,
    )
    nx_m = max(1, int(w / MCON_PITCH))
    ny_m = max(1, int(h / MCON_PITCH))
    arr_w_m = (nx_m - 1) * MCON_PITCH + MCON_SIZE
    arr_h_m = (ny_m - 1) * MCON_PITCH + MCON_SIZE
    sx_m = x + (w - arr_w_m) / 2
    sy_m = y + (h - arr_h_m) / 2
    for ix in range(nx_m):
        for iy in range(ny_m):
            cx = sx_m + ix * MCON_PITCH
            cy = sy_m + iy * MCON_PITCH
            c.add_polygon(
                [(cx, cy), (cx + MCON_SIZE, cy),
                 (cx + MCON_SIZE, cy + MCON_SIZE),
                 (cx, cy + MCON_SIZE)],
                layer=MCON,
            )


# ======================================================================
# MOSFET primitive
# ======================================================================

def nfet(w_um: float, l_um: float, nf: int = 1,
         name: str = None) -> Component:
    """Sky130 NFET layout primitive.

    Args:
        w_um: total gate width in um
        l_um: gate length in um (drawn)
        nf: number of fingers
        name: cell name (auto-generated if None)
    """
    w_finger = _snap(w_um / nf)
    l = _snap(max(l_um, POLY_WIDTH_MIN))
    if name is None:
        name = f"nfet_W{w_um:.3f}_L{l_um:.3f}_nf{nf}"

    c = gf.Component(name)

    # Each finger: poly gate over diffusion, S/D contacts on sides
    sd_width = DIFF_EXT_POLY  # source/drain diffusion extension
    finger_pitch = l + 2 * sd_width

    total_w = nf * finger_pitch + sd_width  # one extra S/D on the right
    total_h = w_finger + 2 * POLY_EXT_DIFF

    # Diffusion rectangle (continuous across all fingers)
    diff_x0 = 0.0
    diff_y0 = POLY_EXT_DIFF
    diff_w = total_w
    diff_h = w_finger
    c.add_polygon(
        [(diff_x0, diff_y0), (diff_x0 + diff_w, diff_y0),
         (diff_x0 + diff_w, diff_y0 + diff_h),
         (diff_x0, diff_y0 + diff_h)],
        layer=DIFF,
    )

    # N+ implant (covers diffusion with enclosure)
    nsdm_enc = 0.125
    c.add_polygon(
        [(diff_x0 - nsdm_enc, diff_y0 - nsdm_enc),
         (diff_x0 + diff_w + nsdm_enc, diff_y0 - nsdm_enc),
         (diff_x0 + diff_w + nsdm_enc, diff_y0 + diff_h + nsdm_enc),
         (diff_x0 - nsdm_enc, diff_y0 + diff_h + nsdm_enc)],
        layer=NSDM,
    )

    # Poly gates and S/D contacts
    for i in range(nf):
        # Poly gate
        gx = sd_width + i * finger_pitch
        c.add_polygon(
            [(gx, 0), (gx + l, 0), (gx + l, total_h), (gx, total_h)],
            layer=POLY,
        )

    # S/D contact regions (between and outside gates)
    sd_regions = []
    for i in range(nf + 1):
        sx = i * finger_pitch
        sd_regions.append((sx, diff_y0, sd_width, diff_h))

    for sx, sy, sw, sh in sd_regions:
        ct_margin = LICON_ENC_DIFF
        ct_x = sx + ct_margin
        ct_y = sy + ct_margin
        ct_w = max(LICON_SIZE, sw - 2 * ct_margin)
        ct_h = max(LICON_SIZE, sh - 2 * ct_margin)
        _licon_stack(c, ct_x, ct_y, ct_w, ct_h)

    # Ports: gate, source (even S/D), drain (odd S/D)
    # Gate port on poly extending above diffusion
    gate_cx = sd_width + l / 2
    c.add_port(name="G", center=(gate_cx, total_h),
               width=_port_width(l), orientation=90, layer=MET1)

    # Source port (leftmost S/D)
    src_cx = sd_width / 2
    c.add_port(name="S", center=(src_cx, diff_y0 + diff_h / 2),
               width=_port_width(diff_h), orientation=180, layer=MET1)

    # Drain port (second S/D for single finger, rightmost for multi)
    drain_x = finger_pitch if nf == 1 else nf * finger_pitch
    drain_cx = drain_x + sd_width / 2
    c.add_port(name="D", center=(drain_cx, diff_y0 + diff_h / 2),
               width=_port_width(diff_h), orientation=0, layer=MET1)

    return c


def pfet(w_um: float, l_um: float, nf: int = 1,
         name: str = None) -> Component:
    """Sky130 PFET layout primitive (in N-well).

    Same structure as NFET but with PSDM implant and NWELL.
    """
    w_finger = _snap(w_um / nf)
    l = _snap(max(l_um, POLY_WIDTH_MIN))
    if name is None:
        name = f"pfet_W{w_um:.3f}_L{l_um:.3f}_nf{nf}"

    c = gf.Component(name)

    sd_width = DIFF_EXT_POLY
    finger_pitch = l + 2 * sd_width
    total_w = nf * finger_pitch + sd_width
    total_h = w_finger + 2 * POLY_EXT_DIFF

    # N-well (encloses everything)
    nw_enc = NWELL_ENC_DIFF
    c.add_polygon(
        [(-nw_enc, -nw_enc),
         (total_w + nw_enc, -nw_enc),
         (total_w + nw_enc, total_h + nw_enc),
         (-nw_enc, total_h + nw_enc)],
        layer=NWELL,
    )

    # Diffusion
    diff_y0 = POLY_EXT_DIFF
    diff_h = w_finger
    c.add_polygon(
        [(0, diff_y0), (total_w, diff_y0),
         (total_w, diff_y0 + diff_h), (0, diff_y0 + diff_h)],
        layer=DIFF,
    )

    # P+ implant
    psdm_enc = 0.125
    c.add_polygon(
        [(-psdm_enc, diff_y0 - psdm_enc),
         (total_w + psdm_enc, diff_y0 - psdm_enc),
         (total_w + psdm_enc, diff_y0 + diff_h + psdm_enc),
         (-psdm_enc, diff_y0 + diff_h + psdm_enc)],
        layer=PSDM,
    )

    # Poly gates
    for i in range(nf):
        gx = sd_width + i * finger_pitch
        c.add_polygon(
            [(gx, 0), (gx + l, 0), (gx + l, total_h), (gx, total_h)],
            layer=POLY,
        )

    # S/D contacts
    sd_regions = []
    for i in range(nf + 1):
        sx = i * finger_pitch
        sd_regions.append((sx, diff_y0, sd_width, diff_h))

    for sx, sy, sw, sh in sd_regions:
        ct_margin = LICON_ENC_DIFF
        ct_x = sx + ct_margin
        ct_y = sy + ct_margin
        ct_w = max(LICON_SIZE, sw - 2 * ct_margin)
        ct_h = max(LICON_SIZE, sh - 2 * ct_margin)
        _licon_stack(c, ct_x, ct_y, ct_w, ct_h)

    # Ports
    gate_cx = sd_width + l / 2
    c.add_port(name="G", center=(gate_cx, total_h),
               width=_port_width(l), orientation=90, layer=MET1)
    src_cx = sd_width / 2
    c.add_port(name="S", center=(src_cx, diff_y0 + diff_h / 2),
               width=_port_width(diff_h), orientation=180, layer=MET1)
    drain_x = finger_pitch if nf == 1 else nf * finger_pitch
    drain_cx = drain_x + sd_width / 2
    c.add_port(name="D", center=(drain_cx, diff_y0 + diff_h / 2),
               width=_port_width(diff_h), orientation=0, layer=MET1)

    return c


# ======================================================================
# Finger count heuristic
# ======================================================================

def _nfingers(w_um: float, max_finger_w: float = 5.0) -> int:
    """Choose number of fingers so each finger <= max_finger_w um."""
    if w_um <= max_finger_w:
        return 1
    return math.ceil(w_um / max_finger_w)


# ======================================================================
# Maneatis delay cell
# ======================================================================

def delay_cell(design) -> Component:
    """Maneatis delay cell: diff pair + symmetric load + tail.

    Ports: inp, inn, outp, outn, vctrl, vbn, vdd, vss
    """
    c = gf.Component("kestrel_delay_cell")

    # Transistor dimensions (um)
    diff_w = _um(design.vco_diff_w)
    diff_l = _um(design.vco_diff_l)
    tail_w = _um(design.vco_tail_w)
    tail_l = _um(design.vco_tail_l)
    load_w = _um(design.vco_load_w)
    load_l = _um(design.vco_load_l)

    nf_diff = _nfingers(diff_w)
    nf_tail = _nfingers(tail_w)
    nf_load = _nfingers(load_w)

    # --- Build transistors ---
    mn1 = nfet(diff_w, diff_l, nf_diff, "Mn1_diff")
    mn2 = nfet(diff_w, diff_l, nf_diff, "Mn2_diff")
    mtail = nfet(tail_w, tail_l, nf_tail, "Mtail")

    mp1a = pfet(load_w, load_l, nf_load, "Mp1a_diode")
    mp1b = pfet(load_w, load_l, nf_load, "Mp1b_ctrl")
    mp2a = pfet(load_w, load_l, nf_load, "Mp2a_diode")
    mp2b = pfet(load_w, load_l, nf_load, "Mp2b_ctrl")

    # --- Place transistors ---
    # Layout: symmetric about vertical center
    #
    #   Mp1a Mp1b  |  Mp2b Mp2a       (PMOS loads, top)
    #              |
    #     Mn1      |      Mn2          (NMOS diff pair, middle)
    #              |
    #           Mtail                  (NMOS tail, bottom)

    spacing_h = 2.0   # horizontal gap between left/right halves
    spacing_v = 3.0   # vertical gap between rows

    # Get bounding boxes for sizing
    mn1_ref = c.add_ref(mn1)
    mn1_bb = mn1_ref.dbbox()
    mn1_w = mn1_bb.right - mn1_bb.left
    mn1_h = mn1_bb.top - mn1_bb.bottom

    mtail_ref = c.add_ref(mtail)
    mtail_bb = mtail_ref.dbbox()
    mtail_w = mtail_bb.right - mtail_bb.left
    mtail_h = mtail_bb.top - mtail_bb.bottom

    mp1a_ref = c.add_ref(mp1a)
    mp1a_bb = mp1a_ref.dbbox()
    mp_w = mp1a_bb.right - mp1a_bb.left
    mp_h = mp1a_bb.top - mp1a_bb.bottom

    mn2_ref = c.add_ref(mn2)
    mp1b_ref = c.add_ref(mp1b)
    mp2a_ref = c.add_ref(mp2a)
    mp2b_ref = c.add_ref(mp2b)

    # Tail at bottom center
    mtail_ref.dmove((-mtail_w / 2, 0))

    # Diff pair above tail
    diff_y = mtail_h + spacing_v
    mn1_ref.dmove((-mn1_w - spacing_h / 2, diff_y))
    mn2_ref.dmove((spacing_h / 2, diff_y))

    # PMOS loads above diff pair
    load_y = diff_y + mn1_h + spacing_v
    # Left half: Mp1a (diode) and Mp1b (ctrl) side by side
    mp1a_ref.dmove((-2 * mp_w - spacing_h / 2, load_y))
    mp1b_ref.dmove((-mp_w - spacing_h / 2 + 0.5, load_y))
    # Right half: Mp2b (ctrl) and Mp2a (diode) side by side
    mp2b_ref.dmove((spacing_h / 2, load_y))
    mp2a_ref.dmove((mp_w + spacing_h / 2 + 0.5, load_y))

    # --- Ports (on MET1) ---
    cell_bb = c.dbbox()
    mid_x = 0.0

    # Gate ports for external connections
    c.add_port(name="inp", center=mn1_ref.ports["G"].dcenter,
               width=1.0, orientation=90, layer=MET1)
    c.add_port(name="inn", center=mn2_ref.ports["G"].dcenter,
               width=1.0, orientation=90, layer=MET1)
    c.add_port(name="vbn", center=mtail_ref.ports["G"].dcenter,
               width=1.0, orientation=90, layer=MET1)

    # Output ports at top
    outn_x = (mp1a_ref.ports["D"].dcenter[0] + mp1b_ref.ports["D"].dcenter[0]) / 2
    outp_x = (mp2a_ref.ports["D"].dcenter[0] + mp2b_ref.ports["D"].dcenter[0]) / 2
    c.add_port(name="outn", center=(outn_x, load_y + mp_h),
               width=2.0, orientation=90, layer=MET1)
    c.add_port(name="outp", center=(outp_x, load_y + mp_h),
               width=2.0, orientation=90, layer=MET1)

    # Control voltage port
    vctrl_x = (mp1b_ref.ports["G"].dcenter[0] + mp2b_ref.ports["G"].dcenter[0]) / 2
    c.add_port(name="vctrl", center=(vctrl_x, load_y + mp_h + 1.0),
               width=1.0, orientation=90, layer=MET2)

    # Supply ports
    c.add_port(name="vdd", center=(mid_x, cell_bb.top + 0.5),
               width=4.0, orientation=90, layer=MET2)
    c.add_port(name="vss", center=(mid_x, cell_bb.bottom - 0.5),
               width=4.0, orientation=270, layer=MET2)

    # --- Internal wiring ---
    # Route discipline:
    #   MET1 — transistor contacts and short local hops (same row)
    #   MET2 — inter-row vertical runs (diff↔tail, diff↔load)
    #          Source and drain use DIFFERENT y-offsets on MET2 so they
    #          can't merge into one polygon.
    #   MET3 — VCO feedback (handled in vco())
    #
    # Every MET1↔MET2 transition gets a VIA1.

    # Y-offsets for source vs drain MET2 jog — keep them apart
    src_jog_y = diff_y - 1.0    # source routes jog below diff pair row
    drn_jog_y = diff_y + mn1_h + 1.0  # drain routes jog above diff pair row

    # Tail source to VSS rail (straight down on MET1, no crossings)
    _hroute(c, mtail_ref.ports["S"].dcenter,
            (mtail_ref.ports["S"].dcenter[0], cell_bb.bottom - 0.5),
            MET1, 1.0)
    _via1_at(c, mtail_ref.ports["S"].dcenter[0], cell_bb.bottom - 0.5)

    # --- Diff pair SOURCE to tail DRAIN (on MET2, jogged at src_jog_y) ---
    tail_d = mtail_ref.ports["D"].dcenter
    for mn_ref in [mn1_ref, mn2_ref]:
        sx, sy = mn_ref.ports["S"].dcenter
        # VIA1 up from MET1 at source contact
        _via1_at(c, sx, sy)
        # MET2 vertical down from source to jog y
        c.add_polygon(
            [(sx - 0.2, src_jog_y), (sx + 0.2, src_jog_y),
             (sx + 0.2, sy), (sx - 0.2, sy)],
            layer=MET2)
        # MET2 horizontal from jog to tail.D x
        c.add_polygon(
            [(min(sx, tail_d[0]) - 0.2, src_jog_y - 0.2),
             (max(sx, tail_d[0]) + 0.2, src_jog_y - 0.2),
             (max(sx, tail_d[0]) + 0.2, src_jog_y + 0.2),
             (min(sx, tail_d[0]) - 0.2, src_jog_y + 0.2)],
            layer=MET2)
    # MET2 vertical from jog down to tail.D
    c.add_polygon(
        [(tail_d[0] - 0.2, tail_d[1]), (tail_d[0] + 0.2, tail_d[1]),
         (tail_d[0] + 0.2, src_jog_y), (tail_d[0] - 0.2, src_jog_y)],
        layer=MET2)
    _via1_at(c, tail_d[0], tail_d[1])

    # --- Diff pair DRAIN to output nodes (on MET2, jogged at drn_jog_y) ---
    for mn_ref, out_x in [(mn1_ref, outn_x), (mn2_ref, outp_x)]:
        dx, dy = mn_ref.ports["D"].dcenter
        # VIA1 up from MET1 at drain contact
        _via1_at(c, dx, dy)
        # MET2 vertical up from drain to jog y
        c.add_polygon(
            [(dx - 0.2, dy), (dx + 0.2, dy),
             (dx + 0.2, drn_jog_y), (dx - 0.2, drn_jog_y)],
            layer=MET2)
        # MET2 horizontal from jog to output x
        c.add_polygon(
            [(min(dx, out_x) - 0.2, drn_jog_y - 0.2),
             (max(dx, out_x) + 0.2, drn_jog_y - 0.2),
             (max(dx, out_x) + 0.2, drn_jog_y + 0.2),
             (min(dx, out_x) - 0.2, drn_jog_y + 0.2)],
            layer=MET2)
        # MET2 vertical from jog up to load connection
        c.add_polygon(
            [(out_x - 0.2, drn_jog_y), (out_x + 0.2, drn_jog_y),
             (out_x + 0.2, load_y + mp_h), (out_x - 0.2, load_y + mp_h)],
            layer=MET2)
        _via1_at(c, out_x, load_y + mp_h)

    # VIA1 at input gate pins (MET1 gate → MET2 for inter-stage routing)
    _via1_at(c, mn1_ref.ports["G"].dcenter[0], mn1_ref.ports["G"].dcenter[1])
    _via1_at(c, mn2_ref.ports["G"].dcenter[0], mn2_ref.ports["G"].dcenter[1])

    # VIA1 at vbn gate (for bias distribution on MET2)
    _via1_at(c, mtail_ref.ports["G"].dcenter[0], mtail_ref.ports["G"].dcenter[1])

    # VIA1 at vctrl gate pins (PMOS ctrl gates → MET2 vctrl bus)
    _via1_at(c, mp1b_ref.ports["G"].dcenter[0], mp1b_ref.ports["G"].dcenter[1])
    _via1_at(c, mp2b_ref.ports["G"].dcenter[0], mp2b_ref.ports["G"].dcenter[1])

    # VIA1 at VDD connection (PMOS sources to MET2 supply)
    _via1_at(c, mp1a_ref.ports["S"].dcenter[0], mp1a_ref.ports["S"].dcenter[1])
    _via1_at(c, mp2a_ref.ports["S"].dcenter[0], mp2a_ref.ports["S"].dcenter[1])

    return c


def _via1_at(c: Component, x: float, y: float, n: int = 1):
    """Draw VIA1 stack (MET1 pad + VIA1 cuts + MET2 pad) at (x, y).

    Places n×1 array of via cuts centered at (x, y).
    """
    via_pitch = VIA1_SIZE + 0.170  # via spacing
    arr_w = (n - 1) * via_pitch + VIA1_SIZE
    sx = x - arr_w / 2
    for i in range(n):
        vx = sx + i * via_pitch
        c.add_polygon(
            [(vx, y - VIA1_SIZE / 2),
             (vx + VIA1_SIZE, y - VIA1_SIZE / 2),
             (vx + VIA1_SIZE, y + VIA1_SIZE / 2),
             (vx, y + VIA1_SIZE / 2)],
            layer=VIA1,
        )
    # MET1 pad enclosing vias
    enc1 = VIA1_ENC_MET1
    c.add_polygon(
        [(sx - enc1, y - VIA1_SIZE / 2 - enc1),
         (sx + arr_w + enc1, y - VIA1_SIZE / 2 - enc1),
         (sx + arr_w + enc1, y + VIA1_SIZE / 2 + enc1),
         (sx - enc1, y + VIA1_SIZE / 2 + enc1)],
        layer=MET1,
    )
    # MET2 pad enclosing vias
    enc2 = VIA1_ENC_MET2
    c.add_polygon(
        [(sx - enc2, y - VIA1_SIZE / 2 - enc2),
         (sx + arr_w + enc2, y - VIA1_SIZE / 2 - enc2),
         (sx + arr_w + enc2, y + VIA1_SIZE / 2 + enc2),
         (sx - enc2, y + VIA1_SIZE / 2 + enc2)],
        layer=MET2,
    )


def _hroute(c: Component, p1: tuple, p2: tuple, layer: tuple,
            width: float):
    """Draw an L-shaped route between two points on the given layer.

    If layer is MET2, also drops VIA1 at both endpoints to connect
    down to MET1 (where transistor pins live).
    """
    x1, y1 = p1[0], p1[1]
    x2, y2 = p2[0], p2[1]
    hw = width / 2
    # vertical segment from p1 to corner
    c.add_polygon(
        [(x1 - hw, y1), (x1 + hw, y1),
         (x1 + hw, y2), (x1 - hw, y2)],
        layer=layer,
    )
    # horizontal segment from corner to p2
    if abs(x2 - x1) > 0.01:
        yc = y2
        c.add_polygon(
            [(x1, yc - hw), (x2, yc - hw),
             (x2, yc + hw), (x1, yc + hw)],
            layer=layer,
        )
    # Drop VIA1 at endpoints when routing on MET2
    if layer == MET2:
        _via1_at(c, x1, y1)
        _via1_at(c, x2, y2)


# ======================================================================
# VCO — ring of delay cells
# ======================================================================

def vco(design) -> Component:
    """4-stage differential ring VCO.

    Places delay cells in a row with cross-coupled feedback from last
    stage to first.
    """
    n_stg = design.spec.vco_stages
    c = gf.Component("kestrel_vco")

    cell = delay_cell(design)
    cell_bb = cell.dbbox()
    cell_w = cell_bb.right - cell_bb.left
    cell_pitch = cell_w + 4.0  # gap between stages

    refs = []
    for i in range(n_stg):
        ref = c.add_ref(cell, name=f"stage{i}")
        ref.dmove((i * cell_pitch, 0))
        refs.append(ref)

    # Wire stage-to-stage on MET3 (not MET2) to avoid merging with
    # the internal delay cell MET2 routing
    def _via2_at_pt(comp, x, y):
        """VIA2 stack at a point."""
        comp.add_polygon(
            [(x - VIA1_SIZE / 2, y - VIA1_SIZE / 2),
             (x + VIA1_SIZE / 2, y - VIA1_SIZE / 2),
             (x + VIA1_SIZE / 2, y + VIA1_SIZE / 2),
             (x - VIA1_SIZE / 2, y + VIA1_SIZE / 2)],
            layer=VIA2)
        enc = VIA1_ENC_MET2
        for lyr in [MET2, MET3]:
            comp.add_polygon(
                [(x - VIA1_SIZE / 2 - enc, y - VIA1_SIZE / 2 - enc),
                 (x + VIA1_SIZE / 2 + enc, y - VIA1_SIZE / 2 - enc),
                 (x + VIA1_SIZE / 2 + enc, y + VIA1_SIZE / 2 + enc),
                 (x - VIA1_SIZE / 2 - enc, y + VIA1_SIZE / 2 + enc)],
                layer=lyr)

    for i in range(n_stg - 1):
        p1 = refs[i].ports["outp"].dcenter
        p2 = refs[i + 1].ports["inp"].dcenter
        _via2_at_pt(c, p1[0], p1[1])
        _via2_at_pt(c, p2[0], p2[1])
        _hroute(c, p1, p2, MET3, 0.5)

        p1 = refs[i].ports["outn"].dcenter
        p2 = refs[i + 1].ports["inn"].dcenter
        _via2_at_pt(c, p1[0], p1[1])
        _via2_at_pt(c, p2[0], p2[1])
        _hroute(c, p1, p2, MET3, 0.5)

    # Cross-coupled feedback: last stage outputs (inverted) to first inputs
    # Route on MET3 to avoid shorting with MET2 inter-stage and supply routes
    last = refs[-1]
    first = refs[0]
    fb_y_top = cell_bb.top + 3.0
    fb_y_bot = cell_bb.bottom - 3.0

    def _via2_at(comp, x, y):
        """VIA2 stack: MET2 pad + VIA2 cut + MET3 pad."""
        comp.add_polygon(
            [(x - VIA1_SIZE / 2, y - VIA1_SIZE / 2),
             (x + VIA1_SIZE / 2, y - VIA1_SIZE / 2),
             (x + VIA1_SIZE / 2, y + VIA1_SIZE / 2),
             (x - VIA1_SIZE / 2, y + VIA1_SIZE / 2)],
            layer=VIA2)
        enc = VIA1_ENC_MET2
        comp.add_polygon(
            [(x - VIA1_SIZE / 2 - enc, y - VIA1_SIZE / 2 - enc),
             (x + VIA1_SIZE / 2 + enc, y - VIA1_SIZE / 2 - enc),
             (x + VIA1_SIZE / 2 + enc, y + VIA1_SIZE / 2 + enc),
             (x - VIA1_SIZE / 2 - enc, y + VIA1_SIZE / 2 + enc)],
            layer=MET2)
        comp.add_polygon(
            [(x - VIA1_SIZE / 2 - enc, y - VIA1_SIZE / 2 - enc),
             (x + VIA1_SIZE / 2 + enc, y - VIA1_SIZE / 2 - enc),
             (x + VIA1_SIZE / 2 + enc, y + VIA1_SIZE / 2 + enc),
             (x - VIA1_SIZE / 2 - enc, y + VIA1_SIZE / 2 + enc)],
            layer=MET3)

    # Top feedback: last.outp → first.inn (on MET3)
    lp = last.ports["outp"].dcenter
    fi = first.ports["inn"].dcenter
    _via2_at(c, lp[0], lp[1])
    _via2_at(c, fi[0], fi[1])
    c.add_polygon(  # up from last.outp
        [(lp[0] - 0.25, lp[1]), (lp[0] + 0.25, lp[1]),
         (lp[0] + 0.25, fb_y_top), (lp[0] - 0.25, fb_y_top)],
        layer=MET3)
    c.add_polygon(  # horizontal
        [(fi[0], fb_y_top - 0.25), (lp[0], fb_y_top - 0.25),
         (lp[0], fb_y_top + 0.25), (fi[0], fb_y_top + 0.25)],
        layer=MET3)
    c.add_polygon(  # down to first.inn
        [(fi[0] - 0.25, fb_y_top), (fi[0] + 0.25, fb_y_top),
         (fi[0] + 0.25, fi[1]), (fi[0] - 0.25, fi[1])],
        layer=MET3)

    # Bottom feedback: last.outn → first.inp (on MET3)
    ln = last.ports["outn"].dcenter
    fp = first.ports["inp"].dcenter
    _via2_at(c, ln[0], ln[1])
    _via2_at(c, fp[0], fp[1])
    c.add_polygon(
        [(ln[0] - 0.25, ln[1]), (ln[0] + 0.25, ln[1]),
         (ln[0] + 0.25, fb_y_bot), (ln[0] - 0.25, fb_y_bot)],
        layer=MET3)
    c.add_polygon(
        [(fp[0], fb_y_bot - 0.25), (ln[0], fb_y_bot - 0.25),
         (ln[0], fb_y_bot + 0.25), (fp[0], fb_y_bot + 0.25)],
        layer=MET3)
    c.add_polygon(
        [(fp[0] - 0.25, fb_y_bot), (fp[0] + 0.25, fb_y_bot),
         (fp[0] + 0.25, fp[1]), (fp[0] - 0.25, fp[1])],
        layer=MET3)

    # Supply rails across the top/bottom on MET2
    full_bb = c.dbbox()
    rail_w = 2.0
    # VDD rail
    c.add_polygon(
        [(full_bb.left, full_bb.top + 1.0),
         (full_bb.right, full_bb.top + 1.0),
         (full_bb.right, full_bb.top + 1.0 + rail_w),
         (full_bb.left, full_bb.top + 1.0 + rail_w)],
        layer=MET2)
    # VSS rail
    c.add_polygon(
        [(full_bb.left, full_bb.bottom - 1.0 - rail_w),
         (full_bb.right, full_bb.bottom - 1.0 - rail_w),
         (full_bb.right, full_bb.bottom - 1.0),
         (full_bb.left, full_bb.bottom - 1.0)],
        layer=MET2)

    # Ports
    c.add_port(name="outp", center=last.ports["outp"].dcenter,
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="outn", center=last.ports["outn"].dcenter,
               width=1.0, orientation=0, layer=MET1)
    vctrl_x = (full_bb.left + full_bb.right) / 2
    c.add_port(name="vctrl", center=(vctrl_x, full_bb.top + 2.0),
               width=2.0, orientation=90, layer=MET2)
    c.add_port(name="vdd", center=((full_bb.left + full_bb.right) / 2,
               full_bb.top + 1.0 + rail_w / 2),
               width=rail_w, orientation=90, layer=MET2)
    c.add_port(name="vss", center=((full_bb.left + full_bb.right) / 2,
               full_bb.bottom - 1.0 - rail_w / 2),
               width=rail_w, orientation=270, layer=MET2)

    # Label
    c.add_label("VCO", position=(vctrl_x, full_bb.top + 4.0), layer=TEXT)

    return c


# ======================================================================
# Charge pump
# ======================================================================

def charge_pump(design) -> Component:
    """Symmetric charge pump: UP (PMOS) and DN (NMOS) current sources."""
    c = gf.Component("kestrel_charge_pump")

    up_w = _um(design.cp_up_w)
    up_l = _um(design.cp_up_l)
    dn_w = _um(design.cp_dn_w)
    dn_l = _um(design.cp_dn_l)
    sw_w = _um(design.cp_sw_w)
    sw_l = _um(design.cp_sw_l)

    # PMOS mirror + switch (top)
    mp_mir = pfet(up_w, up_l, _nfingers(up_w), "Mp_mirror")
    mp_src = pfet(up_w, up_l, _nfingers(up_w), "Mp_source")
    mp_sw = pfet(sw_w, sw_l, _nfingers(sw_w), "Mp_switch")

    # NMOS mirror + switch (bottom)
    mn_mir = nfet(dn_w, dn_l, _nfingers(dn_w), "Mn_mirror")
    mn_src = nfet(dn_w, dn_l, _nfingers(dn_w), "Mn_source")
    mn_sw = nfet(sw_w * 0.5, sw_l, _nfingers(sw_w * 0.5), "Mn_switch")

    # Place: PMOS on top, NMOS on bottom
    spacing = 2.0
    pmos_y = 15.0
    nmos_y = 0.0

    mp_mir_ref = c.add_ref(mp_mir)
    mp_src_ref = c.add_ref(mp_src)
    mp_sw_ref = c.add_ref(mp_sw)
    mn_mir_ref = c.add_ref(mn_mir)
    mn_src_ref = c.add_ref(mn_src)
    mn_sw_ref = c.add_ref(mn_sw)

    # PMOS row
    mp_mir_bb = mp_mir_ref.dbbox()
    mp_w = mp_mir_bb.right - mp_mir_bb.left
    mp_mir_ref.dmove((0, pmos_y))
    mp_src_ref.dmove((mp_w + spacing, pmos_y))
    mp_sw_ref.dmove((2 * (mp_w + spacing), pmos_y))

    # NMOS row
    mn_mir_bb = mn_mir_ref.dbbox()
    mn_w_cell = mn_mir_bb.right - mn_mir_bb.left
    mn_mir_ref.dmove((0, nmos_y))
    mn_src_ref.dmove((mn_w_cell + spacing, nmos_y))
    mn_sw_ref.dmove((2 * (mn_w_cell + spacing), nmos_y))

    # Ports
    bb = c.dbbox()
    mid_x = (bb.left + bb.right) / 2
    c.add_port(name="out", center=(mid_x, (pmos_y + nmos_y + 5) / 2),
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="up", center=(bb.left - 1.0, pmos_y + 2),
               width=1.0, orientation=180, layer=MET1)
    c.add_port(name="dn", center=(bb.left - 1.0, nmos_y + 2),
               width=1.0, orientation=180, layer=MET1)
    c.add_port(name="vdd", center=(mid_x, bb.top + 1.0),
               width=4.0, orientation=90, layer=MET2)
    c.add_port(name="vss", center=(mid_x, bb.bottom - 1.0),
               width=4.0, orientation=270, layer=MET2)

    c.add_label("CP", position=(mid_x, bb.top + 2.0), layer=TEXT)
    return c


# ======================================================================
# Loop filter (passive R-C1-C2) — MIM cap placeholders
# ======================================================================

def loop_filter(design) -> Component:
    """Loop filter as MET1/MET2 MIM capacitor placeholders + poly resistor."""
    c = gf.Component("kestrel_loop_filter")

    # Capacitor area estimation: C = eps0 * eps_r * A / d
    # sky130 MIM cap ~ 2 fF/um^2
    cap_density = 2e-15 / 1e-12  # F/um^2
    c1_area = design.c1 / cap_density  # um^2
    c2_area = design.c2 / cap_density  # um^2
    c1_side = _snap(math.sqrt(c1_area))
    c2_side = _snap(math.sqrt(c2_area))

    # Clamp to reasonable range for layout
    c1_side = max(5.0, min(100.0, c1_side))
    c2_side = max(3.0, min(80.0, c2_side))

    # C2 (shunt, smaller)
    c.add_polygon(
        [(0, 0), (c2_side, 0), (c2_side, c2_side), (0, c2_side)],
        layer=MET1)
    c.add_polygon(
        [(0.5, 0.5), (c2_side - 0.5, 0.5),
         (c2_side - 0.5, c2_side - 0.5), (0.5, c2_side - 0.5)],
        layer=MET2)
    c.add_label("C2", position=(c2_side / 2, c2_side / 2), layer=TEXT)

    # Poly resistor placeholder (between C2 and C1)
    r_x = c2_side + 3.0
    r_len = _snap(min(30.0, max(5.0, design.r_filter / 500)))  # rough scaling
    r_w = 1.0
    c.add_polygon(
        [(r_x, c2_side / 2 - r_w / 2), (r_x + r_len, c2_side / 2 - r_w / 2),
         (r_x + r_len, c2_side / 2 + r_w / 2), (r_x, c2_side / 2 + r_w / 2)],
        layer=POLY)
    c.add_label("R1", position=(r_x + r_len / 2, c2_side / 2 + 1.5), layer=TEXT)

    # C1 (main, larger)
    c1_x = r_x + r_len + 3.0
    c.add_polygon(
        [(c1_x, 0), (c1_x + c1_side, 0),
         (c1_x + c1_side, c1_side), (c1_x, c1_side)],
        layer=MET1)
    c.add_polygon(
        [(c1_x + 0.5, 0.5), (c1_x + c1_side - 0.5, 0.5),
         (c1_x + c1_side - 0.5, c1_side - 0.5),
         (c1_x + 0.5, c1_side - 0.5)],
        layer=MET2)
    c.add_label("C1", position=(c1_x + c1_side / 2, c1_side / 2), layer=TEXT)

    # Ports
    bb = c.dbbox()
    c.add_port(name="in", center=(0, c2_side / 2),
               width=2.0, orientation=180, layer=MET1)
    c.add_port(name="out", center=(c1_x + c1_side, c1_side / 2),
               width=2.0, orientation=0, layer=MET1)
    c.add_port(name="vss", center=((bb.left + bb.right) / 2, bb.bottom - 1.0),
               width=4.0, orientation=270, layer=MET2)

    c.add_label("LF", position=((bb.left + bb.right) / 2, bb.top + 1.5), layer=TEXT)
    return c


# ======================================================================
# PFD — simplified standard-cell style
# ======================================================================

def pfd(design) -> Component:
    """Phase-frequency detector — NAND-based tri-state.

    Places a block of standard cells (NAND2 + INV + DFF) as a black-box
    with labeled ports.  Transistor-level detail inside each gate.
    """
    c = gf.Component("kestrel_pfd")

    nw = _um(design.pfd_nw)
    nl = _um(design.pfd_nl)
    pw = _um(design.pfd_pw)
    pl = _um(design.pfd_pl)

    # Build an inverter cell
    def _inv_cell(name):
        inv = gf.Component(name)
        mp = pfet(pw, pl, 1, f"{name}_p")
        mn = nfet(nw, nl, 1, f"{name}_n")
        mp_ref = inv.add_ref(mp)
        mn_ref = inv.add_ref(mn)
        mn_bb = mn_ref.dbbox()
        mp_ref.dmove((0, mn_bb.top - mn_bb.bottom + 1.0))
        bb = inv.dbbox()
        inv.add_port(name="in", center=(bb.left - 0.5, (bb.top + bb.bottom) / 2),
                     width=1.0, orientation=180, layer=MET1)
        inv.add_port(name="out", center=(bb.right + 0.5, (bb.top + bb.bottom) / 2),
                     width=1.0, orientation=0, layer=MET1)
        inv.add_port(name="vdd", center=((bb.left + bb.right) / 2, bb.top + 0.5),
                     width=2.0, orientation=90, layer=MET2)
        inv.add_port(name="vss", center=((bb.left + bb.right) / 2, bb.bottom - 0.5),
                     width=2.0, orientation=270, layer=MET2)
        return inv

    # Build a NAND2 cell
    def _nand2_cell(name):
        nd = gf.Component(name)
        mp1 = pfet(pw, pl, 1, f"{name}_p1")
        mp2 = pfet(pw, pl, 1, f"{name}_p2")
        mn1 = nfet(nw, nl, 1, f"{name}_n1")
        mn2 = nfet(nw, nl, 1, f"{name}_n2")
        mn1_ref = nd.add_ref(mn1)
        mn2_ref = nd.add_ref(mn2)
        mp1_ref = nd.add_ref(mp1)
        mp2_ref = nd.add_ref(mp2)
        mn1_bb = mn1_ref.dbbox()
        cell_h = mn1_bb.top - mn1_bb.bottom
        mn2_ref.dmove((cell_h + 1.0, 0))
        mp1_ref.dmove((0, cell_h + 1.5))
        mp2_ref.dmove((cell_h + 1.0, cell_h + 1.5))
        bb = nd.dbbox()
        nd.add_port(name="a", center=(bb.left - 0.5, cell_h / 2),
                    width=1.0, orientation=180, layer=MET1)
        nd.add_port(name="b", center=(bb.left - 0.5, cell_h + 2.0),
                    width=1.0, orientation=180, layer=MET1)
        nd.add_port(name="out", center=(bb.right + 0.5, (bb.top + bb.bottom) / 2),
                    width=1.0, orientation=0, layer=MET1)
        nd.add_port(name="vdd", center=((bb.left + bb.right) / 2, bb.top + 0.5),
                    width=2.0, orientation=90, layer=MET2)
        nd.add_port(name="vss", center=((bb.left + bb.right) / 2, bb.bottom - 0.5),
                    width=2.0, orientation=270, layer=MET2)
        return nd

    # Place PFD as rows of gates
    inv1 = _inv_cell("pfd_inv1")
    inv2 = _inv_cell("pfd_inv2")
    inv3 = _inv_cell("pfd_inv3")
    nand1 = _nand2_cell("pfd_nand1")
    nand2 = _nand2_cell("pfd_nand2")
    nand_rst = _nand2_cell("pfd_nand_rst")

    row_pitch = 12.0
    col_pitch = 8.0

    # Row 0: input inverters + NAND for UP path
    refs = {}
    refs["inv1"] = c.add_ref(inv1)
    refs["nand1"] = c.add_ref(nand1)
    refs["nand1"].dmove((col_pitch, 0))

    # Row 1: DN path
    refs["inv2"] = c.add_ref(inv2)
    refs["inv2"].dmove((0, row_pitch))
    refs["nand2"] = c.add_ref(nand2)
    refs["nand2"].dmove((col_pitch, row_pitch))

    # Row 2: reset NAND + output inverter
    refs["nand_rst"] = c.add_ref(nand_rst)
    refs["nand_rst"].dmove((2 * col_pitch, row_pitch / 2))
    refs["inv3"] = c.add_ref(inv3)
    refs["inv3"].dmove((3 * col_pitch, row_pitch / 2))

    # Ports
    bb = c.dbbox()
    mid_x = (bb.left + bb.right) / 2
    c.add_port(name="ref_clk", center=(bb.left - 1.0, 2.0),
               width=1.0, orientation=180, layer=MET1)
    c.add_port(name="fb_clk", center=(bb.left - 1.0, row_pitch + 2.0),
               width=1.0, orientation=180, layer=MET1)
    c.add_port(name="up", center=(bb.right + 1.0, 2.0),
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="dn", center=(bb.right + 1.0, row_pitch + 2.0),
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="vdd", center=(mid_x, bb.top + 1.0),
               width=4.0, orientation=90, layer=MET2)
    c.add_port(name="vss", center=(mid_x, bb.bottom - 1.0),
               width=4.0, orientation=270, layer=MET2)

    c.add_label("PFD", position=(mid_x, bb.top + 2.0), layer=TEXT)
    return c


# ======================================================================
# Divider — chain of TSPC divide-by-2
# ======================================================================

def divider(design) -> Component:
    """Programmable divider — cascade of TSPC div-by-2 cells."""
    c = gf.Component("kestrel_divider")

    nw = _um(design.div_nw)
    nl = _um(design.div_nl)
    pw = _um(design.div_pw)
    pl = _um(design.div_pl)
    n_stages = design.div_stages

    # Build one TSPC div2 stage (6 transistors)
    def _div2_cell(name):
        d2 = gf.Component(name)
        # 3 PMOS + 3 NMOS
        transistors = []
        for i in range(3):
            mp = pfet(pw, pl, 1, f"{name}_p{i}")
            mn = nfet(nw, nl, 1, f"{name}_n{i}")
            mp_ref = d2.add_ref(mp)
            mn_ref = d2.add_ref(mn)
            mn_bb = mn_ref.dbbox()
            cell_h = mn_bb.top - mn_bb.bottom
            x_off = i * 4.0
            mn_ref.dmove((x_off, 0))
            mp_ref.dmove((x_off, cell_h + 1.0))
            transistors.extend([mp_ref, mn_ref])

        bb = d2.dbbox()
        d2.add_port(name="in", center=(bb.left - 0.5, (bb.top + bb.bottom) / 2),
                    width=1.0, orientation=180, layer=MET1)
        d2.add_port(name="out", center=(bb.right + 0.5, (bb.top + bb.bottom) / 2),
                    width=1.0, orientation=0, layer=MET1)
        d2.add_port(name="vdd", center=((bb.left + bb.right) / 2, bb.top + 0.5),
                    width=2.0, orientation=90, layer=MET2)
        d2.add_port(name="vss", center=((bb.left + bb.right) / 2, bb.bottom - 0.5),
                    width=2.0, orientation=270, layer=MET2)
        return d2

    # Place div-by-2 stages in a row
    refs = []
    stage_pitch = 0
    for i in range(n_stages):
        d2 = _div2_cell(f"div2_stage{i}")
        ref = c.add_ref(d2, name=f"div{i}")
        if i == 0:
            d2_bb = ref.dbbox()
            stage_pitch = d2_bb.right - d2_bb.left + 3.0
        ref.dmove((i * stage_pitch, 0))
        refs.append(ref)

    # Wire stage to stage
    for i in range(n_stages - 1):
        _hroute(c, refs[i].ports["out"].dcenter,
                refs[i + 1].ports["in"].dcenter, MET1, 0.5)

    # Ports
    bb = c.dbbox()
    mid_x = (bb.left + bb.right) / 2
    c.add_port(name="in", center=refs[0].ports["in"].dcenter,
               width=1.0, orientation=180, layer=MET1)
    c.add_port(name="out", center=refs[-1].ports["out"].dcenter,
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="vdd", center=(mid_x, bb.top + 1.0),
               width=4.0, orientation=90, layer=MET2)
    c.add_port(name="vss", center=(mid_x, bb.bottom - 1.0),
               width=4.0, orientation=270, layer=MET2)

    c.add_label("DIV", position=(mid_x, bb.top + 2.0), layer=TEXT)
    return c


# ======================================================================
# Top-level PLL assembly
# ======================================================================

def pll_top(design) -> Component:
    """Assemble complete PLL: VCO + PFD + CP + LF + Divider."""
    c = gf.Component("kestrel_pll_top")

    # Generate sub-blocks
    vco_cell = vco(design)
    cp_cell = charge_pump(design)
    lf_cell = loop_filter(design)
    pfd_cell = pfd(design)
    div_cell = divider(design)

    # Place blocks in a floor plan:
    #
    #   ┌─────────────────────────────────────┐
    #   │              VCO                     │
    #   ├────────┬──────────┬─────────────────┤
    #   │  PFD   │    CP    │   Loop Filter   │
    #   ├────────┴──────────┴─────────────────┤
    #   │            Divider                   │
    #   └─────────────────────────────────────┘

    gap = 10.0  # inter-block gap

    # VCO at top
    vco_ref = c.add_ref(vco_cell, name="VCO")
    vco_bb = vco_ref.dbbox()
    vco_w = vco_bb.right - vco_bb.left
    vco_h = vco_bb.top - vco_bb.bottom

    # Middle row: PFD | CP | LF — placed below VCO
    pfd_ref = c.add_ref(pfd_cell, name="PFD")
    pfd_bb = pfd_ref.dbbox()
    pfd_w = pfd_bb.right - pfd_bb.left
    pfd_h = pfd_bb.top - pfd_bb.bottom

    cp_ref = c.add_ref(cp_cell, name="CP")
    cp_bb = cp_ref.dbbox()
    cp_w = cp_bb.right - cp_bb.left

    lf_ref = c.add_ref(lf_cell, name="LF")
    lf_bb = lf_ref.dbbox()
    lf_w = lf_bb.right - lf_bb.left

    mid_y = -(pfd_h + gap)

    pfd_ref.dmove((-pfd_bb.left, mid_y - pfd_bb.bottom))
    cp_ref.dmove((pfd_w + gap - cp_bb.left, mid_y - cp_bb.bottom))
    lf_ref.dmove((pfd_w + cp_w + 2 * gap - lf_bb.left, mid_y - lf_bb.bottom))

    # Divider below middle row
    div_ref = c.add_ref(div_cell, name="DIV")
    div_bb = div_ref.dbbox()
    div_h = div_bb.top - div_bb.bottom
    div_y = mid_y - div_h - gap
    div_ref.dmove((-div_bb.left, div_y - div_bb.bottom))

    # --- Inter-block routing on MET2/MET3 ---
    # PFD.up → CP.up
    _hroute(c, pfd_ref.ports["up"].dcenter,
            cp_ref.ports["up"].dcenter, MET2, 0.5)
    # PFD.dn → CP.dn
    _hroute(c, pfd_ref.ports["dn"].dcenter,
            cp_ref.ports["dn"].dcenter, MET2, 0.5)
    # CP.out → LF.in
    _hroute(c, cp_ref.ports["out"].dcenter,
            lf_ref.ports["in"].dcenter, MET2, 0.5)
    # LF.out → VCO.vctrl
    _hroute(c, lf_ref.ports["out"].dcenter,
            vco_ref.ports["vctrl"].dcenter, MET2, 0.5)
    # VCO.outp → DIV.in
    _hroute(c, vco_ref.ports["outp"].dcenter,
            div_ref.ports["in"].dcenter, MET2, 0.5)
    # DIV.out → PFD.fb_clk (feedback)
    _hroute(c, div_ref.ports["out"].dcenter,
            pfd_ref.ports["fb_clk"].dcenter, MET2, 0.5)

    # Top-level ports
    bb = c.dbbox()
    mid_x = (bb.left + bb.right) / 2
    c.add_port(name="clk_out", center=vco_ref.ports["outp"].dcenter,
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="clk_outb", center=vco_ref.ports["outn"].dcenter,
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="ref_clk", center=pfd_ref.ports["ref_clk"].dcenter,
               width=1.0, orientation=180, layer=MET1)
    c.add_port(name="vctrl_mon", center=lf_ref.ports["out"].dcenter,
               width=1.0, orientation=0, layer=MET1)
    c.add_port(name="vdd", center=(mid_x, bb.top + 2.0),
               width=4.0, orientation=90, layer=MET2)
    c.add_port(name="vss", center=(mid_x, bb.bottom - 2.0),
               width=4.0, orientation=270, layer=MET2)

    c.add_label("KESTREL_PLL", position=(mid_x, bb.top + 4.0), layer=TEXT)

    return c


# ======================================================================
# Public API
# ======================================================================

def generate_pll_gds(design, output_path: str = "kestrel_pll.gds") -> str:
    """Generate complete PLL GDSII from a PLLDesign.

    Args:
        design: PLLDesign from the design engine
        output_path: path for the output .gds file

    Returns:
        Path to the written GDS file.
    """
    top = pll_top(design)
    top.write_gds(output_path)
    return output_path


# ======================================================================
# Standalone entry point
# ======================================================================

if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/usr/local/src/kestrel")
    from kestrel.design.engine import PLLSpec, design_pll, summarize

    spec = PLLSpec(
        freq_min=400e6,
        freq_max=800e6,
        ref_freq=10e6,
        loop_bw=1e6,
        process="sky130",
    )
    design = design_pll(spec)
    print(summarize(design))
    print()

    out = generate_pll_gds(design, "/usr/local/src/kestrel/layout/kestrel_pll.gds")
    print(f"GDS written: {out}")
