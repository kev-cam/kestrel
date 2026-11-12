"""Behavioral SPICE testbench emitter for the charge-pump PLL (ngspice).

Generates an event-driven closed loop on stock ngspice: the VCO is the
XSPICE d_osc code model (every output edge scheduled from the control
voltage through a Vctrl -> frequency table), the PFD and the divider are
XSPICE digital (d_dff/d_and, d_fdiv), the charge pump a B-source and the
loop filter the R1-C1/C2 network with the control voltage at the pump
node.  The behavioral models of record are the Verilog-AMS set
(verilog_ams.py) and their nvc/stat-sim form (statsim.py); this deck is a
quick check of the same loop values on a simulator everyone has.

The component values (Kvco, Icp, R, C1, C2, N) come from the design engine;
the VCO table comes from the process calibration when there is one.
"""

import os
import re

from kestrel.process import format_eng, get_process_params
from ..engine import PLLDesign
from .spice import _eng
from .verilog_ams import reference_release_delay, simulation_time


_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'templates')


def _fill_template(template_name: str, params: dict) -> str:
    """Read a template, replace @PARAM@ placeholders, return text."""
    path = os.path.join(_TEMPLATE_DIR, template_name)
    with open(path) as f:
        text = f.read()
    found = set(re.findall(r'@(\w+)@', text))
    missing = found - set(params)
    if missing:
        raise ValueError(f"{template_name}: undefined parameters: {missing}")
    for key, val in params.items():
        text = text.replace(f'@{key}@', str(val))
    return text


def vco_table(design: PLLDesign):
    """(control voltages, frequencies) for the event-driven VCO.

    The engine's linear law f = f_center + Kvco*(V - vctrl_nom) over the
    calibrated control range, held flat outside it (the real VCO saturates;
    d_osc would extrapolate the end segments).  Without a calibration the
    law spans 0..Vdd, floored at f_center/20.
    """
    s = design.spec
    vdd = s.supply_voltage
    kv = design.kvco * design.kvco_sign
    f_floor = design.f_center / 20.0
    cal = get_process_params(s.process).get("vco_calibration") or {}

    def f_at(v):
        return max(f_floor, design.f_center + kv * (v - design.vctrl_nom))

    if cal.get("vctrl_min") is not None and cal.get("vctrl_max") is not None:
        v_lo, v_hi = cal["vctrl_min"], cal["vctrl_max"]
        # below the window the measured 0 V point (if calibrated) anchors a
        # power-up start; above it the law is held flat
        f0 = cal.get("f_vctrl0") or f_at(v_lo)
        pts = [(0.0, f0), (v_lo, f_at(v_lo)), (design.vctrl_nom, design.f_center),
               (v_hi, f_at(v_hi)), (vdd, f_at(v_hi))]
    else:
        pts = [(0.0, f_at(0.0)), (design.vctrl_nom, design.f_center), (vdd, f_at(vdd))]
    # strictly increasing control voltages, no duplicates
    out = []
    for v, f in pts:
        if not out or v > out[-1][0] + 1e-6:
            out.append((v, f))
    return [p[0] for p in out], [p[1] for p in out]


def _design_to_params(design: PLLDesign) -> dict:
    """Extract template parameters from a PLLDesign."""
    s = design.spec
    ref_period = 1.0 / s.ref_freq
    ref_delay = reference_release_delay(design)
    tran_stop = simulation_time(design)
    # the analog solver only needs to resolve the loop filter; the VCO's
    # edges are breakpoints of their own (clk_out bridge)
    tran_step = ref_period / 20
    vdd = s.supply_voltage
    cntl, freq = vco_table(design)
    cal = get_process_params(s.process).get("vco_calibration") or {}
    if cal.get("vctrl_min") is not None:
        table_note = (f"linear law over the calibrated {cal['vctrl_min']:.2f}-"
                      f"{cal['vctrl_max']:.2f} V range, held flat above it"
                      + (f", the measured {cal['f_vctrl0']/1e6:.0f} MHz at 0 V below it"
                         if cal.get("f_vctrl0") else ", held flat below it"))
    else:
        table_note = "engine's linear law over 0-Vdd"
    cp_note = ("UP sinks: negative Kvco, f falls as Vctrl rises" if design.kvco_sign < 0
               else "UP sources: positive Kvco")

    return {
        'PROCESS':        s.process,
        'FREQ_MIN':       format_eng(s.freq_min, 'Hz'),
        'FREQ_MAX':       format_eng(s.freq_max, 'Hz'),
        'REF_AMPLITUDE':  f'{vdd:g}',
        'REF_FREQ':       _eng(s.ref_freq),
        'REF_PERIOD':     _eng(ref_period),
        'REF_HALF':       _eng(ref_period / 2),
        'REF_DELAY':      _eng(ref_delay),
        'F_CENTER':       _eng(design.f_center),
        'F_TARGET':       f"{design.n_nom * s.ref_freq / 1e6:.4f} MHz (N*f_ref)",
        'KVCO':           _eng(design.kvco),
        'KVCO_SIGNED':    _eng(design.kvco * design.kvco_sign),
        'VCTRL_NOM':      f'{design.vctrl_nom:.3f}',
        'VCTRL_START':    f'{design.vctrl_start:.3f}',
        'ICP':            _eng(design.icp),
        'CP_SIGN':        str(design.kvco_sign),
        'CP_NOTE':        cp_note,
        'R_FILTER':       _eng(design.r_filter),
        'C1':             _eng(design.c1),
        'C2':             _eng(design.c2),
        'N_NOM':          str(design.n_nom),
        'N_HALF':         str(max(1, design.n_nom // 2)),
        'VDD':            f'{vdd:g}',
        'VMID':           f'{vdd / 2:g}',
        'VLO':            f'{0.45 * vdd:g}',
        'VHI':            f'{0.55 * vdd:g}',
        'VCO_CNTL':       ' '.join(f'{v:.4g}' for v in cntl),
        'VCO_FREQ':       ' '.join(f'{f:.6g}' for f in freq),
        'VCO_TABLE_NOTE': table_note,
        'ACQ_TIME':       format_eng(design.acq_time, 's'),
        'TRAN_STEP':      _eng(tran_step),
        'TRAN_STOP':      _eng(tran_stop),
        'T_END':          _eng(tran_stop - ref_period),
        'T_MEAS':         _eng(0.75 * tran_stop),
    }


def emit_behavioral(design: PLLDesign, output_dir: str) -> list:
    """Generate the event-driven behavioral ngspice testbench for a PLL.

    Returns list of generated file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    params = _design_to_params(design)
    files = []

    path = os.path.join(output_dir, 'kestrel_pll_behavioral.cir')
    with open(path, 'w') as f:
        f.write(_fill_template('pll_behavioral.cir-template', params))
    files.append(path)

    return files


def emit_kicad_sch(design: PLLDesign, output_dir: str) -> list:
    """Generate behavioral KiCad schematic for a PLL.

    Returns list of generated file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    params = _design_to_params(design)
    files = []

    path = os.path.join(output_dir, 'kestrel_pll_behavioral.kicad_sch')
    with open(path, 'w') as f:
        f.write(_fill_template('pll_behavioral.kicad_sch-template', params))
    files.append(path)

    return files
