#!/usr/bin/env python3
"""Tests for the behavioral (event-driven ngspice) testbench and KiCad schematic emitters."""

import os, re, sys, tempfile, shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kestrel.generators.pll.engine import PLLSpec, PLLDesign, design_pll
from kestrel.generators.pll.models.behavioral import (
    _fill_template, _design_to_params, emit_behavioral, emit_kicad_sch,
)

PASS = 0
FAIL = 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ''))


def _make_design():
    """Create a design using the engine's defaults."""
    spec = PLLSpec(
        freq_min=800e6, freq_max=1.2e9,
        ref_freq=25e6, loop_bw=5e6,
    )
    return design_pll(spec)


# -- Test: design_to_params produces all required keys ----

def test_params_complete():
    design = _make_design()
    params = _design_to_params(design)
    required = {
        'FREQ_MIN', 'FREQ_MAX', 'REF_AMPLITUDE', 'REF_FREQ',
        'F_CENTER', 'KVCO', 'R_FILTER', 'C1', 'C2', 'N_NOM',
        'TRAN_STEP', 'TRAN_STOP',
    }
    missing = required - set(params)
    check('params has all required keys', len(missing) == 0,
          f'missing: {missing}')
    for k, v in params.items():
        check(f'param {k} is non-empty string', isinstance(v, str) and len(v) > 0)


# -- Test: no stale @PARAM@ in generated output ----

def test_no_stale_placeholders():
    design = _make_design()
    params = _design_to_params(design)
    for tpl_name in ['pll_behavioral.cir-template',
                     'pll_behavioral.kicad_sch-template']:
        text = _fill_template(tpl_name, params)
        stale = re.findall(r'@\w+@', text)
        check(f'{tpl_name}: no stale placeholders', len(stale) == 0,
              f'found: {stale}')


# -- Test: .cir has required SPICE directives ----

def test_spice_directives():
    design = _make_design()
    params = _design_to_params(design)
    text = _fill_template('pll_behavioral.cir-template', params)
    check('cir: has .TRAN', '.TRAN' in text)
    check('cir: has .END', '.END' in text)
    check('cir: VCO is the event-driven d_osc', 'd_osc(' in text and 'Avco vctrl clk_d vco_mod' in text)
    check('cir: no frequency-integrating VCO (SDT/sin) left', 'SDT(' not in text and 'Bvco' not in text)
    check('cir: PFD is two d_dff cleared through a d_and',
          'Aup_ff d_hi ref_d' in text and 'Adn_ff d_hi fb_d' in text and 'd_and(' in text)
    check('cir: divider is d_fdiv with the design N', f'div_factor={design.n_nom} ' in text)
    check('cir: charge pump is a current source into the pump node', 'Bcp 0 vctrl I =' in text)
    check('cir: control voltage is the pump node (R1 to mid, C2 at vctrl)',
          'R1 vctrl mid' in text and 'C2 vctrl 0' in text)
    check('cir: VCO output bridged for a breakpoint per edge', 'Aclk_dac [clk_d] [clk_out] dac_brg' in text)
    check('cir: reference released late', re.search(r'^Vref ref_clk 0 PULSE\(0 [0-9.]+ 0 ', text, re.M) is None)


# -- Test: .kicad_sch has balanced parentheses ----

def test_kicad_balanced():
    design = _make_design()
    params = _design_to_params(design)
    text = _fill_template('pll_behavioral.kicad_sch-template', params)
    depth = 0
    for ch in text:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if depth < 0:
            break
    check('kicad_sch: balanced parens', depth == 0, f'depth={depth}')


# -- Test: emit_behavioral writes file ----

def test_emit_behavioral():
    design = _make_design()
    tmpdir = tempfile.mkdtemp(prefix='kestrel_test_')
    try:
        files = emit_behavioral(design, tmpdir)
        check('emit_behavioral: returns 1 file', len(files) == 1)
        check('emit_behavioral: file exists', os.path.exists(files[0]))
        text = open(files[0]).read()
        check('emit_behavioral: contains the design N and the d_osc VCO',
              f'div_factor={design.n_nom} ' in text and 'd_osc(' in text)
    finally:
        shutil.rmtree(tmpdir)


# -- Test: emit_kicad_sch writes file ----

def test_emit_kicad_sch():
    design = _make_design()
    tmpdir = tempfile.mkdtemp(prefix='kestrel_test_')
    try:
        files = emit_kicad_sch(design, tmpdir)
        check('emit_kicad_sch: returns 1 file', len(files) == 1)
        check('emit_kicad_sch: file exists', os.path.exists(files[0]))
        check('emit_kicad_sch: .kicad_sch extension',
              files[0].endswith('.kicad_sch'))
    finally:
        shutil.rmtree(tmpdir)


# -- Test: design values are physically reasonable ----

def test_design_values_reasonable():
    design = _make_design()
    params = _design_to_params(design)
    check('N_NOM is integer > 0', int(params['N_NOM']) > 0)
    check('f_center param present', len(params['F_CENTER']) > 0)
    check('ref_freq param present', len(params['REF_FREQ']) > 0)


# -- Test: missing param raises ValueError ----

def test_missing_param():
    try:
        _fill_template('pll_behavioral.cir-template', {'FREQ_MIN': '1G'})
        check('missing params raises ValueError', False, 'no exception')
    except ValueError:
        check('missing params raises ValueError', True)


# -- Run ----

if __name__ == '__main__':
    print("=== kestrel behavioral emitter tests ===\n")
    test_params_complete()
    test_no_stale_placeholders()
    test_spice_directives()
    test_kicad_balanced()
    test_emit_behavioral()
    test_emit_kicad_sch()
    test_design_values_reasonable()
    test_missing_param()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
