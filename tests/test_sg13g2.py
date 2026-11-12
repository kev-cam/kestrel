#!/usr/bin/env python3
"""Tests for the IHP SG13G2 transistor-level path.

Covers the process calibration (validated VCO sizing, negative Kvco), the
bundled Xyce-native model files, and the self-contained generated decks.
If Xyce is on PATH the generated PLL testbench is also parse-checked
(``Xyce -norun``); otherwise that check is skipped.
"""

import os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kestrel.process import get_process_params
from kestrel.generators.pll.engine import PLLSpec, design_pll
from kestrel.generators.pll.models.spice import emit_spice
from kestrel.generators.pll.models.verilog_ams import emit_verilog_ams

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


def _emit(process, vdd):
    spec = PLLSpec(freq_min=800e6, freq_max=1.2e9, ref_freq=25e6, loop_bw=5e6,
                   process=process, supply_voltage=vdd)
    design = design_pll(spec)
    out = tempfile.mkdtemp(prefix=f'kestrel_{process}_')
    files = emit_spice(design, out)
    names = {os.path.relpath(f, out).replace(os.sep, '/') for f in files}
    return design, out, names


def test_calibration():
    proc = get_process_params('sg13g2')
    check('sg13g2 nominal supply is 1.2 V', proc['vdd'] == 1.2)
    cal = proc['vco_calibration']
    design = design_pll(PLLSpec(freq_min=800e6, freq_max=1.2e9, ref_freq=25e6,
                                loop_bw=5e6, process='sg13g2', supply_voltage=1.2))
    check('calibrated diff-pair width applied', abs(design.vco_diff_w - cal['diff_w']) < 1e-12)
    check('calibrated load width applied', abs(design.vco_load_w - cal['load_w']) < 1e-12)
    check('calibrated startup current applied', abs(design.vco_i_start - cal['i_start']) < 1e-15)
    check('negative Kvco recorded with positive magnitude',
          design.kvco_sign == -1 and design.kvco > 0)
    check('Vctrl nominal from calibration', abs(design.vctrl_nom - cal['vctrl_nom']) < 1e-9)
    # sky130 has no calibration block: square-law sizing and +Kvco unchanged
    d130 = design_pll(PLLSpec(freq_min=800e6, freq_max=1.2e9, ref_freq=25e6, loop_bw=5e6))
    check('sky130 keeps positive Kvco', d130.kvco_sign == 1)


def test_emitted_files():
    design, out, names = _emit('sg13g2', 1.2)
    try:
        for want in ('models/sg13g2_psp103_tt.lib', 'models/sg13g2_lv.lib',
                     'kestrel_vco_tb.sp', 'kestrel_tb.sp'):
            check(f'emits {want}', want in names and os.path.isfile(os.path.join(out, want)))
        tb = open(os.path.join(out, 'kestrel_tb.sp')).read()
        a = tb.find('.INCLUDE "models/sg13g2_psp103_tt.lib"')
        b = tb.find('.INCLUDE "models/sg13g2_lv.lib"')
        check('testbench includes model files, cards before wrappers', 0 <= a < b)
        check('testbench supply is 1.2 V', re.search(r'^Vdd vdd 0 1\.2\s*$', tb, re.M) is not None)
        check('testbench starts the loop filter at the calibrated minimum (0.2 V)',
              re.search(r'^\.IC V\(Xpll:vctrl\)=0\.2\s*$', tb, re.M) is not None)
        check('design warns that a 0 V start outruns the divider',
              any('divider chain is verified only to 844 MHz' in w for w in design.warnings),
              '; '.join(design.warnings))
        check('acquisition estimate exceeds the linear lock time',
              design.acq_time > design.lock_time > 0,
              f'acq {design.acq_time:.3g} s, lock {design.lock_time:.3g} s')
        m = re.search(r'^\.TRAN \S+ ([0-9.]+)([pnum]?)\s*$', tb, re.M)
        tstop = float(m.group(1)) * {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3, '': 1.0}[m.group(2)] if m else 0
        check('testbench transient covers the acquisition estimate',
              tstop >= design.acq_time, f'.TRAN stop {tstop:.3g} s vs acq {design.acq_time:.3g} s')
        vco = open(os.path.join(out, 'kestrel_vco.sp')).read()
        devs = re.findall(r'^([MX])\S+ \S+ \S+ \S+ \S+ (\S+) W=', vco, re.M)
        check('VCO devices are X-instances of sg13_lv_* wrappers',
              bool(devs) and all(p == 'X' and m.startswith('sg13_lv_') for p, m in devs),
              str(devs[:3]))
        check('bias replica Vctrl PFET drives vbn',
              re.search(r'^Xrep_pc vbn vctrl vdd vdd sg13_lv_pmos', vco, re.M) is not None)
        check('calibrated sizing in VCO netlist', 'W=2.000u L=0.340u' in vco and 'W=3.000u L=0.500u' in vco)
        check('VCO outputs go through differential-to-CMOS buffers',
              re.search(r'^Xbufp outp dp_3 dn_3 vbn vdd vss kestrel_vco_buf', vco, re.M) is not None)
        pfd = open(os.path.join(out, 'kestrel_pfd.sp')).read()
        check('flip-flop reset_b latch (G3) is free of rst_b',
              re.search(r'^Xg3 c  b   clk d4 vdd vss nand3', pfd, re.M) is not None)
        cp = open(os.path.join(out, 'kestrel_charge_pump.sp')).read()
        check('charge-pump mirror references have the right polarity',
              re.search(r'^Ibias_p pbias vss ', cp, re.M) is not None and
              re.search(r'^Ibias_n vdd nbias ', cp, re.M) is not None)
        lf = open(os.path.join(out, 'kestrel_loop_filter.sp')).read()
        check('loop filter output is the charge-pump node (zero visible to the VCO)',
              re.search(r'^Rout in out 1\s*$', lf, re.M) is not None)
        emit_verilog_ams(design, out)
        vams = open(os.path.join(out, 'kestrel_loop_filter.vams')).read()
        check('VAMS loop filter output is the charge-pump node',
              'V(vctrl) <+ V(inp);' in vams and 'V(vctrl) <+ V(mid);' not in vams)
        top = open(os.path.join(out, 'kestrel_pll_top.sp')).read()
        check('charge pump UP/DN swapped for negative Kvco',
              re.search(r'^Xcp cp_out dn up pbias nbias', top, re.M) is not None)
        vtb = open(os.path.join(out, 'kestrel_vco_tb.sp')).read()
        check('VCO testbench sweeps the calibrated Vctrl range',
              re.search(r'^\.STEP vctrl_val 0\.2 0\.5 ', vtb, re.M) is not None)
        xyce = shutil.which('Xyce')
        if xyce:
            r = subprocess.run([xyce, '-norun', 'kestrel_tb.sp'], cwd=out,
                               capture_output=True, text=True, timeout=1200)
            log = r.stdout + r.stderr
            check('Xyce -norun accepts the generated sg13g2 testbench',
                  r.returncode == 0 and 'Netlist error' not in log, log[-400:])
        else:
            print('  SKIP  Xyce not on PATH: netlist parse check skipped')
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_sky130_unaffected():
    design, out, names = _emit('sky130', 1.8)
    try:
        check('sky130 emits no bundled models', not any(n.startswith('models/') for n in names))
        tb = open(os.path.join(out, 'kestrel_tb.sp')).read()
        check('sky130 testbench keeps the PDK placeholder', '<path_to_pdk>' in tb)
        check('sky130 testbench starts the loop filter discharged',
              re.search(r'^\.IC V\(Xpll:vctrl\)=0\s*$', tb, re.M) is not None)
        top = open(os.path.join(out, 'kestrel_pll_top.sp')).read()
        check('sky130 charge pump keeps UP/DN order',
              re.search(r'^Xcp cp_out up dn pbias nbias', top, re.M) is not None)
        check('sky130 also gets the VCO testbench', 'kestrel_vco_tb.sp' in names)
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == '__main__':
    print("=== kestrel SG13G2 path tests ===\n")
    test_calibration()
    test_emitted_files()
    test_sky130_unaffected()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
