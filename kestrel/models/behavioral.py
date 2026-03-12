"""Behavioral SPICE netlist emitter for charge-pump PLL.

Generates a system-level behavioral model using B-source expressions
(analog multiplier phase detector, SDT-based VCO, divider).  Suitable
for fast lock-time verification and loop dynamics exploration before
committing to transistor-level simulation.

The component values (Kvco, R, C1, C2, N) come from the design engine.
"""

import os
import re

from ..design.engine import PLLDesign, format_eng
from .spice import _eng


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

    for name, value in params.items():
        text = text.replace(f'@{name}@', str(value))

    return text


def _design_to_params(design: PLLDesign) -> dict:
    """Extract template parameters from a PLLDesign."""
    s = design.spec
    # Simulation: 20 lock times or 10x the ref period, whichever is longer
    ref_period = 1.0 / s.ref_freq
    tran_stop = max(design.lock_time * 20, ref_period * 200)
    # Step: ~50 points per fastest period (VCO output)
    tran_step = 1.0 / (design.f_center * 50)

    return {
        'FREQ_MIN':      format_eng(s.freq_min, 'Hz'),
        'FREQ_MAX':      format_eng(s.freq_max, 'Hz'),
        'REF_AMPLITUDE': '1',
        'REF_FREQ':      _eng(s.ref_freq),
        'F_CENTER':      _eng(design.f_center),
        'KVCO':          _eng(design.kvco),
        'R_FILTER':      _eng(design.r_filter),
        'C1':            _eng(design.c1),
        'C2':            _eng(design.c2),
        'N_NOM':         str(design.n_nom),
        'TRAN_STEP':     _eng(tran_step),
        'TRAN_STOP':     _eng(tran_stop),
    }


def emit_behavioral(design: PLLDesign, output_dir: str) -> list:
    """Generate behavioral SPICE netlist for a PLL.

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
