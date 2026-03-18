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
    parasitic_cap: float = 0.0   # F — post-layout parasitic C per VCO node


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

    # Transistor sizing — VCO delay cell
    vco_tail_w: float = 0.0      # m — tail current NMOS width
    vco_tail_l: float = 0.0      # m — tail current NMOS length
    vco_diff_w: float = 0.0      # m — differential pair NMOS width
    vco_diff_l: float = 0.0      # m — differential pair NMOS length
    vco_load_w: float = 0.0      # m — symmetric load PMOS width
    vco_load_l: float = 0.0      # m — symmetric load PMOS length
    vco_bias_w: float = 0.0      # m — bias mirror NMOS width
    vco_bias_l: float = 0.0      # m — bias mirror NMOS length
    vco_i_stage: float = 0.0     # A — current per delay stage

    # Transistor sizing — charge pump
    cp_up_w: float = 0.0         # m — UP current source PMOS width
    cp_up_l: float = 0.0         # m — UP current source PMOS length
    cp_dn_w: float = 0.0         # m — DN current source NMOS width
    cp_dn_l: float = 0.0         # m — DN current source NMOS length
    cp_sw_w: float = 0.0         # m — switch transistor width
    cp_sw_l: float = 0.0         # m — switch transistor length

    # Transistor sizing — PFD
    pfd_nw: float = 0.0          # m — PFD NMOS width
    pfd_nl: float = 0.0          # m — PFD NMOS length
    pfd_pw: float = 0.0          # m — PFD PMOS width
    pfd_pl: float = 0.0          # m — PFD PMOS length

    # Transistor sizing — divider
    div_nw: float = 0.0          # m — divider NMOS width
    div_nl: float = 0.0          # m — divider NMOS length
    div_pw: float = 0.0          # m — divider PMOS width
    div_pl: float = 0.0          # m — divider PMOS length
    div_stages: int = 0          # number of divide-by-2 stages

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
    kvco_schem = (spec.freq_max - spec.freq_min) / v_swing  # Hz/V
    if kvco_schem <= 0:
        kvco_schem = d.f_center * 0.3 / (spec.supply_voltage / 2)
        d.warnings.append("freq_min == freq_max; estimated Kvco from center freq")

    # Post-layout Kvco: parasitic cap attenuates frequency modulation
    # Kvco_actual = Kvco_schem * C_gate / (C_gate + C_parasitic)
    c_gate_est = 20e-15
    if spec.parasitic_cap > 0:
        kvco_atten = c_gate_est / (c_gate_est + spec.parasitic_cap)
        d.kvco = kvco_schem * kvco_atten
    else:
        d.kvco = kvco_schem

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

    # --- Transistor sizing ---
    _size_transistors(d)

    return d


# ---------------------------------------------------------------------------
# Process parameters
# ---------------------------------------------------------------------------

_PROCESS_PARAMS = {
    "sky130": {
        "nfet": "sky130_fd_pr__nfet_01v8",
        "pfet": "sky130_fd_pr__pfet_01v8",
        "lmin": 150e-9,       # m — minimum drawn length
        "l_analog": 360e-9,   # m — typical analog length (better matching)
        "kpn": 270e-6,        # A/V^2 — approximate NMOS kp (uCox*W/L factor)
        "kpp": 90e-6,         # A/V^2 — approximate PMOS kp
        "vtn": 0.4,           # V — NMOS threshold (approximate)
        "vtp": 0.4,           # V — PMOS |Vtp| (approximate)
        "vdd": 1.8,
        "model_lib": "sky130_fd_pr/cells",
        "corner": "tt",
    },
    "gf180": {
        "nfet": "nfet_03v3",
        "pfet": "pfet_03v3",
        "lmin": 280e-9,
        "l_analog": 500e-9,
        "kpn": 200e-6,
        "kpp": 65e-6,
        "vtn": 0.5,
        "vtp": 0.5,
        "vdd": 3.3,
        "model_lib": "gf180mcu_fd_pr",
        "corner": "tt",
    },
}


def get_process_params(process: str) -> dict:
    """Return process parameters for the given process name."""
    return _PROCESS_PARAMS[process]


def _size_transistors(d: PLLDesign):
    """Compute transistor W/L for all PLL blocks.

    Uses square-law MOSFET equations for initial sizing.
    These are starting points — real designs need SPICE optimization.
    """
    proc = _PROCESS_PARAMS.get(d.spec.process, _PROCESS_PARAMS["sky130"])
    lmin = proc["lmin"]
    l_an = proc["l_analog"]
    kpn = proc["kpn"]
    kpp = proc["kpp"]
    vtn = proc["vtn"]
    vtp = proc["vtp"]
    vdd = d.spec.supply_voltage

    # --- VCO delay cell sizing ---
    # Ring oscillator: f = 1 / (2 * N_stages * t_delay)
    # t_delay = C_total * V_swing / I_stage
    # C_total = C_gate (intrinsic) + C_parasitic (from layout)
    #
    # With parasitics, the actual frequency is:
    #   f_actual = f_intrinsic * C_gate / (C_gate + C_parasitic)
    #
    # To hit the target f_center after parasitics, size the VCO for
    # an intrinsic frequency that's higher by the ratio
    #   (C_gate + C_parasitic) / C_gate
    n_stg = d.spec.vco_stages
    v_swing_vco = 0.4 * vdd
    c_gate_est = 20e-15              # intrinsic gate + wiring cap
    c_parasitic = d.spec.parasitic_cap
    c_total = c_gate_est + c_parasitic

    # The ring oscillator frequency is f = 1/(2*N*C_total*Vswing/I).
    # With parasitic cap, C_total > C_gate, so for the same current
    # the frequency drops.  To hit f_center post-layout, we need to
    # design for a higher intrinsic frequency:
    #   f_intrinsic = f_center * C_total / C_gate
    # Then: f_actual = f_intrinsic * C_gate / C_total = f_center. ✓
    f_design = d.f_center * c_total / c_gate_est
    t_delay = 1.0 / (2 * n_stg * f_design)

    # Size for intrinsic load cap (C_gate) at the higher frequency
    # I = C_gate * V_swing / t_delay
    # The parasitic cap is there in reality but doesn't need more
    # current — it just slows the ring back down to f_center.
    d.vco_i_stage = c_gate_est * v_swing_vco / t_delay

    # Clamp to reasonable range
    d.vco_i_stage = max(50e-6, min(2e-3, d.vco_i_stage))

    # Differential pair: Id = 0.5 * kpn * (W/L) * (Vgs - Vtn)^2
    # Vgs ~ Vdd/2 for biasing at mid-rail
    vgs_diff = vdd / 2
    vov_diff = vgs_diff - vtn
    if vov_diff < 0.1:
        vov_diff = 0.1
    # Each transistor carries half the tail current
    i_half = d.vco_i_stage / 2
    # W/L = 2*Id / (kpn * Vov^2)
    wl_diff = 2 * i_half / (kpn * vov_diff * vov_diff)
    d.vco_diff_l = lmin
    d.vco_diff_w = max(lmin * 2, wl_diff * d.vco_diff_l)

    # Tail current source: carries full stage current
    vov_tail = 0.2  # modest overdrive for good matching
    wl_tail = 2 * d.vco_i_stage / (kpn * vov_tail * vov_tail)
    d.vco_tail_l = l_an  # longer L for current source matching
    d.vco_tail_w = max(lmin * 2, wl_tail * d.vco_tail_l)

    # Symmetric load PMOS (Maneatis): sized to set output swing
    # Each load PMOS carries half the stage current in triode/saturation boundary
    vov_load = 0.15
    wl_load = 2 * i_half / (kpp * vov_load * vov_load)
    d.vco_load_l = l_an
    d.vco_load_w = max(lmin * 2, wl_load * d.vco_load_l)

    # Bias mirror NMOS: mirrors the tail current
    d.vco_bias_l = l_an
    d.vco_bias_w = d.vco_tail_w  # 1:1 mirror ratio

    # --- Charge pump sizing ---
    # UP source (PMOS): Icp = 0.5 * kpp * (W/L) * (Vsg - |Vtp|)^2
    vov_cp = 0.25  # overdrive for output compliance
    wl_cp_up = 2 * d.icp / (kpp * vov_cp * vov_cp)
    d.cp_up_l = l_an
    d.cp_up_w = max(lmin * 2, wl_cp_up * d.cp_up_l)

    # DN sink (NMOS)
    wl_cp_dn = 2 * d.icp / (kpn * vov_cp * vov_cp)
    d.cp_dn_l = l_an
    d.cp_dn_w = max(lmin * 2, wl_cp_dn * d.cp_dn_l)

    # Switches: wide for low Ron, minimum length for speed
    d.cp_sw_l = lmin
    d.cp_sw_w = max(1e-6, d.cp_up_w * 2)  # 2x current source for low drop

    # --- PFD sizing ---
    # Digital gates: sized for speed at target reference frequency
    # Minimum length, width scaled for fanout
    d.pfd_nl = lmin
    d.pfd_pl = lmin
    # PMOS ~2x NMOS for balanced rise/fall (mobility ratio)
    d.pfd_nw = max(lmin * 3, 500e-9)
    d.pfd_pw = max(d.pfd_nw * 2, 1e-6)

    # --- Divider sizing ---
    # Must operate at VCO frequency — use minimum length
    d.div_nl = lmin
    d.div_pl = lmin
    # Width: scale with frequency for adequate drive
    w_scale = max(1.0, d.f_center / 500e6)
    d.div_nw = max(lmin * 3, 500e-9 * w_scale)
    d.div_pw = max(d.div_nw * 2, 1e-6 * w_scale)
    # Number of divide-by-2 stages: N = 2^stages, pick minimum
    d.div_stages = max(1, math.ceil(math.log2(d.n_nom)))


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
