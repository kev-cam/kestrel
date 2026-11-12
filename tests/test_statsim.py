#!/usr/bin/env python3
"""Tests for the stat-sim (nvc) emitter.

Text checks always run.  When nvc and a stat-sim checkout are available the
emitted model is analysed, elaborated and run for the SG13G2 and sky130
designs: the event-driven loop must lock at N*f_ref, and a run with timing
variability switched on must still lock and report non-zero jitter.

Environment: NVC (binary; also looked for in the kev-cam build trees),
NVC_LIB (its library directory), STATSIM_DIR (default /usr/local/src/stat-sim).
"""

import os, re, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kestrel.generators.pll.engine import PLLSpec, design_pll
from kestrel.generators.pll.models.statsim import emit_statsim

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


def find_nvc():
    nvc = os.environ.get('NVC')
    lib = os.environ.get('NVC_LIB')
    if nvc and os.access(nvc, os.X_OK):
        return nvc, lib
    p = shutil.which('nvc')
    if p:
        return p, lib
    for tree in ('/usr/local/src/nvc-build', '/opt/nvc-build-e1'):
        cand = os.path.join(tree, 'bin', 'nvc')
        if os.access(cand, os.X_OK):
            return cand, lib or os.path.join(tree, 'lib')
    return None, None


NVC, NVC_LIB = find_nvc()
STATSIM_DIR = os.environ.get('STATSIM_DIR', '/usr/local/src/stat-sim')
HAVE_STATSIM = os.path.isfile(os.path.join(STATSIM_DIR, 'lib', 'statsim_disc.vhd'))

SPECS = {
    'sg13g2': PLLSpec(freq_min=840e6, freq_max=1080e6, ref_freq=30e6, loop_bw=2e6,
                      supply_voltage=1.2, process='sg13g2'),
    'sky130': PLLSpec(freq_min=800e6, freq_max=1.2e9, ref_freq=25e6, loop_bw=5e6),
}


def test_text():
    design = design_pll(SPECS['sg13g2'])
    out = tempfile.mkdtemp(prefix='kestrel_statsim_')
    try:
        files = emit_statsim(design, out)
        names = {os.path.basename(f) for f in files}
        check('emits model, testbench and runner',
              names == {'kestrel_pll_statsim.vhd', 'kestrel_pll_statsim_tb.vhd', 'run_statsim.sh'}, str(names))
        vhd = open(os.path.join(out, 'kestrel_pll_statsim.vhd')).read()
        check('model uses the stat-sim prob_load discipline',
              'use statsim.statsim_disc_pkg.all;' in vhd and 'resolved_pl' in vhd)
        check('VCO is the travelling edge with a per-stage delay (wait for)',
              'entity kestrel_vco_pl' in vhd and 'wait for sec(td);' in vhd and 'stage := stage + 1;' in vhd)
        check('no frequency integration anywhere', 'phase' not in vhd.lower().replace('phase error', '')
              .replace('phase_err', '').replace('static phase', '').replace('ref->fb phase', ''))
        check('loop filter is the closed-form event model', 'function lf_advance' in vhd and 'LF_TAU' in vhd)
        check('timing variability generics present',
              all(g in vhd for g in ('SIGMA_MM', 'SIGMA_J', 'SIGMA_ICP')) and 'procedure gauss' in vhd)
        check('negative Kvco wired through the pump SIGN generic',
              f'SIGN      : integer := {design.kvco_sign};' in vhd and design.kvco_sign == -1)
        tb = open(os.path.join(out, 'kestrel_pll_statsim_tb.vhd')).read()
        check('testbench releases the reference late and reports the summary line',
              'wait for sec(REF_DELAY);' in tb and 'KESTREL_STATSIM seed=' in tb)
        run = open(os.path.join(out, 'run_statsim.sh')).read()
        check('runner supports Monte-Carlo over seeds', '--mc' in run and 'gSEED=$seed' in run)
        check('runner is executable', os.access(os.path.join(out, 'run_statsim.sh'), os.X_OK))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _run_nvc(label, spec, env_extra, stop_ns):
    design = design_pll(spec)
    out = tempfile.mkdtemp(prefix=f'kestrel_statsim_{label}_')
    try:
        emit_statsim(design, out)
        env = dict(os.environ, NVC=NVC, STATSIM_DIR=STATSIM_DIR, **env_extra)
        if NVC_LIB:
            env['NVC_LIB'] = NVC_LIB
        r = subprocess.run(['bash', 'run_statsim.sh'], cwd=out, env=env,
                           capture_output=True, text=True, timeout=900)
        m = re.search(r'KESTREL_STATSIM seed=(\d+) f_MHz=([-0-9.e+]+) t_lock_us=([-0-9.e+]+) '
                      r'jit_ps=([-0-9.e+]+) pe_rms_ps=([-0-9.e+]+) pe_max_ps=([-0-9.e+]+) '
                      r'vctrl=([-0-9.e+]+)', r.stdout + r.stderr)
        check(f'{label}: nvc analyses, elaborates and runs the model', r.returncode == 0 and m is not None,
              (r.stdout + r.stderr).strip()[-800:])
        if not m:
            return None, design
        vals = dict(f=float(m.group(2)), t_lock=float(m.group(3)), jit=float(m.group(4)),
                    pe_rms=float(m.group(5)), pe_max=float(m.group(6)), vctrl=float(m.group(7)))
        print(f"    {label}: f = {vals['f']:.4f} MHz, lock at {vals['t_lock']:.3f} us, period jitter "
              f"{vals['jit']:.2f} ps rms, ref->fb {vals['pe_rms']:.1f} ps rms / {vals['pe_max']:.1f} ps max, "
              f"vctrl {vals['vctrl']:.4f} V")
        return vals, design
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _run_mc(label, spec, env_extra, seeds):
    """run_statsim.sh --mc N: every seed runs and the awk summary prints."""
    design = design_pll(spec)
    out = tempfile.mkdtemp(prefix=f'kestrel_statsim_mc_{label}_')
    try:
        emit_statsim(design, out)
        env = dict(os.environ, NVC=NVC, STATSIM_DIR=STATSIM_DIR, **env_extra)
        if NVC_LIB:
            env['NVC_LIB'] = NVC_LIB
        r = subprocess.run(['bash', 'run_statsim.sh', '--mc', str(seeds)], cwd=out, env=env,
                           capture_output=True, text=True, timeout=900)
        text = r.stdout + r.stderr
        runs = re.findall(r'^KESTREL_STATSIM seed=(\d+) ', text, re.M)
        check(f'{label}: --mc {seeds} runs every seed', r.returncode == 0 and len(runs) == seeds,
              f'rc {r.returncode}, {len(runs)} result lines')
        m = re.search(r'^\s*f_MHz\s+([0-9.]+) \+/- ([0-9.e+-]+)\s+\(n=(\d+)\)', text, re.M)
        check(f'{label}: Monte-Carlo summary prints mean +/- sd per metric',
              'awk:' not in text and m is not None and int(m.group(3)) == seeds, text.strip()[-600:])
        if m:
            f_target = design.n_nom * spec.ref_freq / 1e6
            check(f'{label}: Monte-Carlo mean frequency within 0.1 % of N*f_ref',
                  abs(float(m.group(1)) - f_target) < 1e-3 * f_target, m.group(0))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_nvc():
    for label, spec in SPECS.items():
        vals, design = _run_nvc(label, spec, {}, 0)
        if not vals:
            continue
        f_target = design.n_nom * spec.ref_freq / 1e6
        check(f'{label}: locks at N*f_ref within 0.05 %', abs(vals['f'] - f_target) < 5e-4 * f_target,
              f"{vals['f']} vs {f_target} MHz")
        check(f'{label}: lock time reported inside the run', 0 < vals['t_lock'] < 1e3, str(vals['t_lock']))
        check(f'{label}: deterministic run has negligible period jitter', vals['jit'] < 1.0, f"{vals['jit']} ps")
        check(f'{label}: control voltage at the design point',
              abs(vals['vctrl'] - design.vctrl_nom) < 0.05, f"{vals['vctrl']} vs {design.vctrl_nom}")
    vals, design = _run_nvc('sg13g2 with variability', SPECS['sg13g2'],
                            {'SIGMA_J': '0.01', 'SIGMA_MM': '0.02', 'SIGMA_ICP': '0.05', 'SIGMA_REF': '5e-12'}, 0)
    if vals:
        f_target = design.n_nom * SPECS['sg13g2'].ref_freq / 1e6
        check('variability run still locks (within 0.1 %)', abs(vals['f'] - f_target) < 1e-3 * f_target,
              f"{vals['f']} MHz")
        check('variability run shows period jitter (1 % stage jitter -> a few ps rms)',
              1.0 < vals['jit'] < 50.0, f"{vals['jit']} ps")
    _run_mc('sg13g2', SPECS['sg13g2'],
            {'SIGMA_J': '0.01', 'SIGMA_MM': '0.02', 'SIGMA_ICP': '0.05', 'SIGMA_REF': '5e-12'}, 3)


if __name__ == '__main__':
    print("=== kestrel stat-sim (nvc) emitter tests ===\n")
    test_text()
    if NVC and HAVE_STATSIM:
        print(f"  using {NVC} (libs {NVC_LIB or 'default'}), stat-sim at {STATSIM_DIR}")
        test_nvc()
    else:
        print(f"  SKIP  nvc runs (nvc: {NVC or 'not found'}; stat-sim discipline: "
              f"{'found' if HAVE_STATSIM else 'not found at ' + STATSIM_DIR})")
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
