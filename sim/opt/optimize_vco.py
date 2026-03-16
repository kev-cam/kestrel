#!/usr/bin/env python3
"""
VCO transistor-level optimization.

Adjusts MOSFET sizing to match a target frequency-vs-Vctrl curve
(typically from the behavioral model).  Supports pluggable simulator
and extraction backends.

Usage:
    cd /usr/local/src/kestrel/sim
    python3 opt/optimize_vco.py                        # defaults
    python3 opt/optimize_vco.py --config opt/config.yaml
    python3 opt/optimize_vco.py --method powell --max-iter 100
    python3 opt/optimize_vco.py --refit                # refit behavioral model after optimization
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml

# Add parent to path so backends can be imported
sys.path.insert(0, os.path.dirname(__file__))
from backends import get_simulator, get_extractor


# ---------------------------------------------------------------------------
# Cost functions
# ---------------------------------------------------------------------------

def cost_mse_relative(target: Dict[float, float],
                      measured: Dict[float, float],
                      penalty: float) -> float:
    """Mean squared relative error.  Penalty for missing points."""
    errors = []
    for vc, f_target in target.items():
        f_meas = measured.get(vc)
        if f_meas is None or f_meas <= 0:
            errors.append(penalty)
        else:
            errors.append(((f_meas - f_target) / f_target) ** 2)
    return np.mean(errors)


def cost_mse_absolute(target, measured, penalty):
    """Mean squared absolute error (Hz^2)."""
    errors = []
    for vc, f_target in target.items():
        f_meas = measured.get(vc)
        if f_meas is None or f_meas <= 0:
            errors.append(penalty)
        else:
            errors.append((f_meas - f_target) ** 2)
    return np.mean(errors)


def cost_max_relative(target, measured, penalty):
    """Maximum relative error."""
    worst = 0
    for vc, f_target in target.items():
        f_meas = measured.get(vc)
        if f_meas is None or f_meas <= 0:
            worst = max(worst, penalty)
        else:
            worst = max(worst, abs(f_meas - f_target) / f_target)
    return worst


COST_FUNCTIONS = {
    'mse_relative': cost_mse_relative,
    'mse_absolute': cost_mse_absolute,
    'max_relative': cost_max_relative,
}


# ---------------------------------------------------------------------------
# Optimization engine
# ---------------------------------------------------------------------------

class VCOOptimizer:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.sim = get_simulator(self.config)
        self.ext = get_extractor(self.config)

        # Target curve
        self.target = {float(k): float(v)
                       for k, v in self.config['target']['points'].items()}
        self.vctrl_points = sorted(self.target.keys())

        # Design variables
        var_cfg = self.config['variables']
        self.var_names = list(var_cfg.keys())
        self.var_init = np.array([var_cfg[n]['init'] for n in self.var_names])
        self.var_min = np.array([var_cfg[n]['min'] for n in self.var_names])
        self.var_max = np.array([var_cfg[n]['max'] for n in self.var_names])

        # Cost function
        cost_name = self.config['optimizer']['cost']
        self.cost_fn = COST_FUNCTIONS[cost_name]
        self.penalty = self.config['optimizer']['penalty_no_osc']

        # Work directory
        self.work_dir = os.path.dirname(os.path.dirname(os.path.abspath(config_path)))
        # Ensure we're in sim/
        if not os.path.exists(os.path.join(self.work_dir, 'models')):
            self.work_dir = os.path.join(self.work_dir, 'sim')

        # Logging
        self.log_path = os.path.join(self.work_dir,
                                     self.config['output']['log_file'])
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self.eval_count = 0
        self.best_cost = float('inf')
        self.best_params = None
        self.best_results = None
        self._init_log()

    def _init_log(self):
        with open(self.log_path, 'w', newline='') as f:
            writer = csv.writer(f)
            header = ['eval', 'cost'] + self.var_names + \
                     [f'f_{vc}V' for vc in self.vctrl_points]
            writer.writerow(header)

    def _log(self, cost, params_dict, results):
        with open(self.log_path, 'a', newline='') as f:
            writer = csv.writer(f)
            row = [self.eval_count, f'{cost:.6e}']
            row += [f'{params_dict[n]:.4e}' for n in self.var_names]
            row += [f'{results.get(vc, 0):.1f}' if results.get(vc) else 'FAIL'
                    for vc in self.vctrl_points]
            writer.writerow(row)

    def _params_to_dict(self, x: np.ndarray) -> Dict[str, float]:
        return {name: val for name, val in zip(self.var_names, x)}

    def evaluate(self, x: np.ndarray) -> float:
        """Run one simulation and return cost."""
        self.eval_count += 1

        # Clamp to bounds
        x = np.clip(x, self.var_min, self.var_max)
        params = self._params_to_dict(x)

        # Extraction (if configured)
        extracted = self.ext.extract(self.config, self.work_dir)

        # Generate netlist
        netlist = self.sim.generate_netlist(
            params, self.vctrl_points, self.config, self.work_dir, extracted)

        # Run simulation
        ok, msg = self.sim.run(netlist, self.config)
        if not ok:
            cost = self.penalty
            results = {vc: None for vc in self.vctrl_points}
            print(f"  [{self.eval_count}] SIM FAIL: {msg[:80]}")
        else:
            # Parse results
            results = self.sim.parse_results(
                netlist, self.vctrl_points, self.config)
            cost = self.cost_fn(self.target, results, self.penalty)

        self._log(cost, params, results)

        # Track best
        if cost < self.best_cost:
            self.best_cost = cost
            self.best_params = params.copy()
            self.best_results = results.copy()

        # Progress output
        n_osc = sum(1 for v in results.values() if v is not None and v > 0)
        print(f"  [{self.eval_count:3d}] cost={cost:.4e}  osc={n_osc}/{len(self.vctrl_points)}  "
              f"params=[{', '.join(f'{v:.2e}' for v in x)}]")
        return cost

    def optimize(self):
        """Run the optimization loop."""
        from scipy.optimize import minimize, differential_evolution

        opt_cfg = self.config['optimizer']
        method = opt_cfg['method'].lower()
        max_iter = opt_cfg['max_iter']
        ftol = opt_cfg['ftol']

        print(f"=== VCO Optimization ===")
        print(f"Method: {method}  Max iter: {max_iter}")
        print(f"Variables: {self.var_names}")
        print(f"Target points: {len(self.target)} Vctrl values")
        print(f"Simulator: {self.config['simulator']['tool']}")
        print(f"Extraction: {self.config['extraction']['tool']}")
        print()

        # Initial evaluation
        print("Initial evaluation:")
        self.evaluate(self.var_init)
        print()

        bounds = list(zip(self.var_min, self.var_max))

        if method == 'differential-evolution':
            result = differential_evolution(
                self.evaluate, bounds,
                maxiter=max_iter, tol=ftol,
                seed=42, polish=True,
                init='sobol'
            )
        else:
            result = minimize(
                self.evaluate, self.var_init,
                method=method,
                bounds=bounds if method in ('powell', 'cobyla', 'l-bfgs-b') else None,
                options={
                    'maxiter': max_iter,
                    'fatol': ftol if method == 'nelder-mead' else None,
                    'xatol': 1e-9 if method == 'nelder-mead' else None,
                    'disp': True,
                }
            )

        print(f"\n=== Optimization complete ===")
        print(f"Evaluations: {self.eval_count}")
        print(f"Best cost: {self.best_cost:.6e}")
        print(f"Best parameters:")
        for name in self.var_names:
            print(f"  {name:20s} = {self.best_params[name]:.4e}")
        print(f"\nBest frequency curve:")
        for vc in self.vctrl_points:
            f_target = self.target[vc]
            f_meas = self.best_results.get(vc)
            if f_meas:
                err = 100 * (f_meas - f_target) / f_target
                print(f"  Vctrl={vc:.1f}V  target={f_target/1e6:.1f} MHz"
                      f"  measured={f_meas/1e6:.1f} MHz  err={err:+.1f}%")
            else:
                print(f"  Vctrl={vc:.1f}V  target={f_target/1e6:.1f} MHz"
                      f"  measured=FAIL")

        # Write best netlist
        best_path = os.path.join(self.work_dir,
                                 self.config['output']['best_netlist'])
        os.makedirs(os.path.dirname(best_path), exist_ok=True)
        self.sim.generate_netlist(
            self.best_params, self.vctrl_points, self.config, self.work_dir)
        import shutil
        shutil.copy(os.path.join(self.work_dir, '_opt_vco.cir'), best_path)
        print(f"\nBest netlist saved to: {best_path}")
        print(f"Optimization log: {self.log_path}")

        return self.best_params, self.best_results


# ---------------------------------------------------------------------------
# Behavioral model refit
# ---------------------------------------------------------------------------

def refit_behavioral(best_results: Dict[float, Optional[float]],
                     config: dict, work_dir: str):
    """
    Refit the behavioral VCO model parameters (kvco, etc.) to match
    the optimized transistor-level curve.

    The Van der Pol behavioral model has effective frequency:
        f(Vctrl) ≈ kvco_eff * Vctrl

    But the transistor-level curve is typically nonlinear, so we fit
    a polynomial:
        f(Vctrl) = a0 + a1*Vctrl + a2*Vctrl^2 + a3*Vctrl^3

    Then update the Verilog-A model with a polynomial gain.
    Also report a simple linear kvco_eff for the linear range.
    """
    # Collect valid data points
    vc_data = []
    freq_data = []
    for vc in sorted(best_results.keys()):
        f = best_results[vc]
        if f is not None and f > 0:
            vc_data.append(vc)
            freq_data.append(f)

    if len(vc_data) < 2:
        print("ERROR: Not enough oscillating points to refit model.")
        return

    vc_arr = np.array(vc_data)
    f_arr = np.array(freq_data)

    # Polynomial fit (degree = min(3, n_points - 1))
    deg = min(3, len(vc_data) - 1)
    coeffs = np.polyfit(vc_arr, f_arr, deg)
    poly = np.poly1d(coeffs)

    # Linear fit for kvco_eff
    lin_coeffs = np.polyfit(vc_arr, f_arr, 1)
    kvco_eff = lin_coeffs[0]  # Hz/V
    f0_eff = lin_coeffs[1]    # Hz at Vctrl=0

    print(f"\n=== Behavioral Model Refit ===")
    print(f"Data points: {len(vc_data)}")
    print(f"Vctrl range: [{min(vc_data):.1f}, {max(vc_data):.1f}] V")
    print(f"\nLinear fit:  f = {kvco_eff/1e6:.1f} MHz/V * Vctrl + {f0_eff/1e6:.1f} MHz")
    print(f"  kvco_eff = {kvco_eff:.3e} Hz/V")
    print(f"\nPolynomial fit (degree {deg}):")
    for i, c in enumerate(coeffs):
        power = deg - i
        print(f"  Vctrl^{power}: {c:.3e} Hz/V^{power}")

    # Fit quality
    f_fit = poly(vc_arr)
    residuals = f_arr - f_fit
    rms_err = np.sqrt(np.mean(residuals**2))
    max_err = np.max(np.abs(residuals))
    print(f"\nFit quality:")
    print(f"  RMS error: {rms_err/1e6:.2f} MHz")
    print(f"  Max error: {max_err/1e6:.2f} MHz")

    print(f"\nComparison:")
    print(f"  {'Vctrl':>6s}  {'Transistor':>12s}  {'Poly fit':>12s}  {'Error':>8s}")
    for vc, f in zip(vc_data, freq_data):
        fp = poly(vc)
        err = 100 * (fp - f) / f
        print(f"  {vc:6.2f}V  {f/1e6:10.1f} MHz  {fp/1e6:10.1f} MHz  {err:+6.1f}%")

    # Generate updated Verilog-A model
    va_path = os.path.join(work_dir, 'opt', 'kes_vco_refit.va')
    os.makedirs(os.path.dirname(va_path), exist_ok=True)

    # Build polynomial expression string
    if deg >= 3:
        omega_expr = (f"6.283185307 * ({coeffs[0]:.6e} * V(vctrl)*V(vctrl)*V(vctrl)"
                      f" + {coeffs[1]:.6e} * V(vctrl)*V(vctrl)"
                      f" + {coeffs[2]:.6e} * V(vctrl)"
                      f" + {coeffs[3]:.6e})")
    elif deg >= 2:
        omega_expr = (f"6.283185307 * ({coeffs[0]:.6e} * V(vctrl)*V(vctrl)"
                      f" + {coeffs[1]:.6e} * V(vctrl)"
                      f" + {coeffs[2]:.6e})")
    else:
        omega_expr = f"6.283185307 * ({coeffs[0]:.6e} * V(vctrl) + {coeffs[1]:.6e})"

    va_content = f"""\
// kes_vco_refit — Behavioral VCO refit to match transistor-level layout
//
// Polynomial frequency model fit from optimization:
//   f(Vctrl) = {' + '.join(f'{c:.3e}*Vctrl^{deg-i}' for i, c in enumerate(coeffs))}
//
// Linear approximation: kvco_eff = {kvco_eff:.3e} Hz/V
//
// Generated by optimize_vco.py

`include "constants.vams"
`include "disciplines.vams"

module kes_vco(vctrl, outp, outn);
    inout  vctrl, outp, outn;
    electrical vctrl, outp, outn;
    electrical x, y;

    parameter real vdd = 1.8;
    parameter real mu  = 3.0;    // Van der Pol damping

    real omega;

    analog begin
        // Polynomial frequency model (refit from transistor-level data)
        omega = {omega_expr};

        // Clamp omega to positive values
        if (omega < 6.283185307 * 1e6) omega = 6.283185307 * 1e6;

        // Van der Pol oscillator
        I(x) <+ ddt(V(x)) - omega * V(y);
        I(y) <+ ddt(V(y)) - omega * (mu * (1.0 - V(x)*V(x)) * V(y) - V(x) + 0.01);

        // Output: scale to rail-to-rail
        V(outp) <+ 0.5 * vdd * (1.0 + tanh(5.0 * V(x)));
        V(outn) <+ vdd - V(outp);
    end
endmodule
"""
    with open(va_path, 'w') as f:
        f.write(va_content)
    print(f"\nRefit Verilog-A model: {va_path}")

    # Also save coefficients as YAML for reference
    refit_path = os.path.join(work_dir, 'opt', 'refit_params.yaml')
    refit_data = {
        'linear': {
            'kvco_eff_hz_per_v': float(kvco_eff),
            'f0_hz': float(f0_eff),
        },
        'polynomial': {
            'degree': deg,
            'coefficients': [float(c) for c in coeffs],
            'description': f"f(Vctrl) = sum(coeff[i] * Vctrl^(deg-i))"
        },
        'fit_quality': {
            'rms_error_hz': float(rms_err),
            'max_error_hz': float(max_err),
            'n_points': len(vc_data),
            'vctrl_range': [float(min(vc_data)), float(max(vc_data))],
        },
        'data': {str(vc): float(f) for vc, f in zip(vc_data, freq_data)},
    }
    with open(refit_path, 'w') as f:
        yaml.dump(refit_data, f, default_flow_style=False)
    print(f"Refit parameters: {refit_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='VCO transistor-level sizing optimization')
    parser.add_argument('--config', default='opt/config.yaml',
                        help='Configuration YAML file')
    parser.add_argument('--method', default=None,
                        help='Override optimizer method')
    parser.add_argument('--max-iter', type=int, default=None,
                        help='Override max iterations')
    parser.add_argument('--refit', action='store_true',
                        help='Refit behavioral model after optimization')
    parser.add_argument('--refit-only', action='store_true',
                        help='Skip optimization, just refit from last best')
    args = parser.parse_args()

    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.getcwd(), config_path)

    opt = VCOOptimizer(config_path)

    if args.method:
        opt.config['optimizer']['method'] = args.method
    if args.max_iter:
        opt.config['optimizer']['max_iter'] = args.max_iter

    if args.refit_only:
        # Load best results from log
        log_path = opt.log_path
        best_cost = float('inf')
        best_row = None
        with open(log_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                cost = float(row['cost'])
                if cost < best_cost:
                    best_cost = cost
                    best_row = row
        if best_row is None:
            print("ERROR: No data in optimization log.")
            return
        results = {}
        for vc in opt.vctrl_points:
            key = f'f_{vc}V'
            val = best_row.get(key, 'FAIL')
            results[vc] = float(val) if val != 'FAIL' else None
        refit_behavioral(results, opt.config, opt.work_dir)
    else:
        best_params, best_results = opt.optimize()
        if args.refit:
            refit_behavioral(best_results, opt.config, opt.work_dir)


if __name__ == '__main__':
    main()
