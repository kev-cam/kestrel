"""PLL design engine — computes component values from specifications."""

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PLLSpec:
    """User-facing PLL specification."""
    freq_min: float          # Hz — minimum output frequency
    freq_max: float          # Hz — maximum output frequency
    ref_freq: float          # Hz — reference clock frequency
    loop_bw: float           # Hz — target loop bandwidth
    phase_margin: float = 60.0   # degrees
    jitter_target: Optional[float] = None  # seconds rms (None = don't constrain)
    vco_type: str = "ring"   # "ring" or "lc"
    vco_stages: int = 4      # number of ring oscillator stages (ring only)
    supply_voltage: float = 1.8  # V
    process: str = "sky130"


@dataclass
class PLLDesign:
    """Computed PLL design parameters."""
    spec: PLLSpec

    # Divider
    n_min: int = 0
    n_max: int = 0
    n_nom: int = 0           # nominal divider ratio

    # VCO
    kvco: float = 0.0        # Hz/V — VCO gain
    f_center: float = 0.0    # Hz — VCO center frequency
    vctrl_nom: float = 0.0   # V — nominal control voltage

    # Charge pump
    icp: float = 0.0         # A — charge pump current

    # Loop filter (second-order: R, C1, C2)
    r_filter: float = 0.0    # ohms
    c1: float = 0.0          # farads
    c2: float = 0.0          # farads

    # Derived performance
    natural_freq: float = 0.0    # rad/s
    damping: float = 0.0
    lock_time: float = 0.0       # seconds (estimated)
    pm_actual: float = 0.0       # degrees — actual phase margin
    jitter_est: float = 0.0      # seconds rms — estimated jitter

    # Status
    warnings: list = field(default_factory=list)


def design_pll(spec: PLLSpec) -> PLLDesign:
    """Compute all PLL parameters from a specification.

    Uses the standard charge-pump PLL design methodology:
    1. Determine divider range from freq range and ref clock
    2. Compute VCO gain from frequency range and control voltage swing
    3. Set charge pump current and loop filter for target bandwidth
       and phase margin
    4. Estimate jitter from linear noise model
    """
    d = PLLDesign(spec=spec)

    # --- Divider ---
    d.n_min = max(1, int(math.floor(spec.freq_min / spec.ref_freq)))
    d.n_max = max(d.n_min, int(math.ceil(spec.freq_max / spec.ref_freq)))
    d.n_nom = round((d.n_min + d.n_max) / 2)
    if d.n_nom < 1:
        d.n_nom = 1

    # --- VCO ---
    d.f_center = (spec.freq_min + spec.freq_max) / 2.0
    # Control voltage swing: assume VCO tunes over 0.2*Vdd to 0.8*Vdd
    v_swing = 0.6 * spec.supply_voltage
    d.vctrl_nom = spec.supply_voltage / 2.0
    d.kvco = (spec.freq_max - spec.freq_min) / v_swing  # Hz/V
    if d.kvco <= 0:
        d.kvco = d.f_center * 0.3 / (spec.supply_voltage / 2)
        d.warnings.append("freq_min == freq_max; estimated Kvco from center freq")

    kvco_rad = d.kvco * 2 * math.pi  # rad/s/V

    # --- Loop bandwidth and phase margin ---
    wbw = 2 * math.pi * spec.loop_bw  # target bandwidth in rad/s
    pm_rad = math.radians(spec.phase_margin)

    # For a type-II second-order CP-PLL with a zero at wz and pole at wp:
    #   phase margin = pi - arctan(wbw/wz) + arctan(wbw/wp)  (approximately)
    #
    # Standard design: place zero at wbw/tan(pm + pi/4) and C2 = C1/10
    # This gives adequate phase margin with a simple filter.

    # Zero and pole placement for target phase margin
    # Using Gardner's method: wz = wbw / gamma, wp = wbw * gamma
    # where gamma = tan(pm/2 + pi/4)
    gamma = math.tan(pm_rad / 2 + math.pi / 4)
    if gamma < 1.1:
        gamma = 1.1
    wz = wbw / gamma   # zero frequency
    wp = wbw * gamma    # pole frequency

    # C2/C1 ratio from pole/zero
    # wp/wz = 1 + C1/C2, so C1/C2 = wp/wz - 1
    c1_over_c2 = (wp / wz) - 1
    if c1_over_c2 < 1:
        c1_over_c2 = 10  # fallback

    # --- Charge pump current ---
    # Open-loop unity-gain crossover at wbw:
    #   |T(jwbw)| = (Icp * Kvco * R) / (N * wbw) * |Z(jwbw)| = 1
    #
    # For initial sizing, pick Icp then solve for R and C.
    # Typical Icp: 10-200 uA. Scale with frequency.
    d.icp = 50e-6  # 50 uA default
    if d.f_center > 500e6:
        d.icp = 100e-6
    if d.f_center > 2e9:
        d.icp = 200e-6

    # --- Loop filter ---
    # R*C1 = 1/wz  (sets the zero)
    # C2 = C1/c1_over_c2  (sets the pole)
    #
    # From unity gain condition at wbw:
    #   Icp * Kvco / (N * wbw^2 * C1) ~= 1  (simplified for wz << wbw)
    # So C1 = Icp * Kvco / (N * wbw^2)
    N = d.n_nom
    d.c1 = d.icp * kvco_rad / (N * wbw * wbw)

    # Sanity clamp
    if d.c1 < 100e-15:
        d.c1 = 100e-15
        d.warnings.append("C1 clamped to 100fF minimum")
    if d.c1 > 1e-9:
        d.c1 = 1e-9
        d.warnings.append("C1 clamped to 1nF maximum")

    d.c2 = d.c1 / c1_over_c2
    d.r_filter = 1.0 / (wz * d.c1)

    # --- Verify phase margin ---
    # Compute actual open-loop transfer function phase at wbw
    wz_actual = 1.0 / (d.r_filter * d.c1)
    c_total = d.c1 + d.c2
    wp_actual = 1.0 / (d.r_filter * d.c1 * d.c2 / c_total)
    pm_check = math.pi - math.atan(wbw / wz_actual) + math.atan(wbw / wp_actual)
    # This formula gives PM referenced to -180; convert properly:
    # Actually: PM = arctan(wbw/wz) - arctan(wbw/wp) for type-II PLL
    d.pm_actual = math.degrees(math.atan(wbw / wz_actual) - math.atan(wbw / wp_actual))
    if d.pm_actual < 0:
        d.pm_actual += 180

    # --- Natural frequency and damping ---
    d.natural_freq = math.sqrt(d.icp * kvco_rad / (N * c_total))
    if d.natural_freq > 0:
        d.damping = (d.r_filter * d.c1 / 2) * d.natural_freq

    # --- Lock time estimate (5 * time constant) ---
    if d.natural_freq > 0 and d.damping > 0:
        d.lock_time = 5.0 / (d.damping * d.natural_freq)

    # --- Jitter estimate (linear noise model) ---
    # Simplified: jitter_rms ~ 1/(2*pi*f_center) * sqrt(kT / (C1 * Vswing^2))
    # This is a rough first-order estimate.
    kT = 1.38e-23 * 300  # Boltzmann * temperature
    if d.f_center > 0 and d.c1 > 0:
        d.jitter_est = (1.0 / (2 * math.pi * d.f_center)) * \
            math.sqrt(kT / (d.c1 * v_swing * v_swing))

    if spec.jitter_target and d.jitter_est > spec.jitter_target:
        d.warnings.append(
            f"Estimated jitter {d.jitter_est*1e12:.1f}ps exceeds "
            f"target {spec.jitter_target*1e12:.1f}ps"
        )

    return d


def format_eng(value: float, unit: str = "") -> str:
    """Format a value with engineering prefix."""
    if value == 0:
        return f"0 {unit}"
    prefixes = [
        (1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"),
        (1, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"),
        (1e-12, "p"), (1e-15, "f"),
    ]
    for scale, prefix in prefixes:
        if abs(value) >= scale * 0.999:
            return f"{value/scale:.3g} {prefix}{unit}"
    return f"{value:.3g} {unit}"


def summarize(d: PLLDesign) -> str:
    """Return a human-readable design summary."""
    lines = [
        "PLL Design Summary",
        "=" * 40,
        f"  Output range:    {format_eng(d.spec.freq_min, 'Hz')} — {format_eng(d.spec.freq_max, 'Hz')}",
        f"  Reference:       {format_eng(d.spec.ref_freq, 'Hz')}",
        f"  Loop bandwidth:  {format_eng(d.spec.loop_bw, 'Hz')}",
        "",
        f"  Divider N:       {d.n_min} — {d.n_max} (nom {d.n_nom})",
        f"  VCO Kvco:        {format_eng(d.kvco, 'Hz/V')}",
        f"  VCO center:      {format_eng(d.f_center, 'Hz')}",
        f"  Charge pump Icp: {format_eng(d.icp, 'A')}",
        "",
        f"  Loop filter R:   {format_eng(d.r_filter, 'ohm')}",
        f"  Loop filter C1:  {format_eng(d.c1, 'F')}",
        f"  Loop filter C2:  {format_eng(d.c2, 'F')}",
        "",
        f"  Phase margin:    {d.pm_actual:.1f} deg",
        f"  Damping ratio:   {d.damping:.2f}",
        f"  Lock time (est): {format_eng(d.lock_time, 's')}",
        f"  Jitter (est):    {format_eng(d.jitter_est, 's')} rms",
    ]
    if d.warnings:
        lines.append("")
        lines.append("  Warnings:")
        for w in d.warnings:
            lines.append(f"    - {w}")
    return "\n".join(lines)
