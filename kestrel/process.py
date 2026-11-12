"""Shared process parameters and formatting utilities."""


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
        "inst_prefix": "M",   # direct .MODEL cards — use M prefix
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
        "inst_prefix": "M",
    },
    "sg13g2": {
        "nfet": "sg13_lv_nmos",
        "pfet": "sg13_lv_pmos",
        "lmin": 130e-9,       # m — LV MOS min gate length; PSP cards valid for
                              #     L = 0.13-10 um (0.34u/0.28u are only the PDK
                              #     subckt defaults, not minimums)
        "l_analog": 500e-9,   # m — longer L for analog matching
        "kpn": 298.8e-6,      # A/V^2 — from IHP characterization (W/L=1u/1u)
        "kpp": 82.18e-6,      # A/V^2 — from IHP characterization
        "vtn": 0.255,         # V — NMOS threshold (nominal)
        "vtp": 0.353,         # V — PMOS |Vtp| (nominal)
        "vdd": 1.2,
        "model_lib": "cornerMOSlv",
        "corner": "mos_tt",
        "inst_prefix": "X",   # .SUBCKT wrappers — use X prefix
        # Xyce-native models bundled in sim/models (flattened IHP TT PSP103
        # cards + IHP-interface subckt wrappers).  The SPICE emitter copies
        # them next to the generated netlist and includes them in this order,
        # so `kestrel pll gen --process sg13g2` output simulates as-is.
        "model_files": ["sg13g2_psp103_tt.lib", "sg13g2_lv.lib"],
        # Transistor-level calibration (Xyce, PSP103 TT, 1.2 V, 2026-11-11).
        # Sizing from sim/kes_vco_xyce_sg13g2.cir; the numbers below were
        # measured on the *generated* VCO (kestrel_vco_tb.sp, sg13_lv_*
        # wrappers with IHP's default S/D junction areas, CMOS output
        # buffers): 844 MHz @ 0.20 V falling monotonically to 517 MHz @
        # 0.50 V, Kvco ~ -1.09 GHz/V
        # (Maneatis polarity: f falls as Vctrl rises), rail-to-rail CMOS
        # outputs.  The bare M-card deck runs ~20% faster (no junction areas)
        # and turns back up above 0.55 V, so stay within 0.2-0.5 V.  The
        # design engine uses this block instead of the square-law estimate
        # and the emitter swaps the charge-pump UP/DN inputs for the sign.
        "vco_calibration": {
            "tail_w": 4.0e-6, "tail_l": 0.5e-6,
            "diff_w": 2.0e-6, "diff_l": 0.34e-6,
            "load_w": 3.0e-6, "load_l": 0.5e-6,
            "bias_w": 2.0e-6, "bias_l": 0.5e-6,
            "i_start": 5.0e-6,
            "kvco": -1.09e9,          # Hz/V, signed
            "vctrl_nom": 0.35,        # V, middle of the usable range
            "vctrl_min": 0.20, "vctrl_max": 0.50,
            "f_min": 517e6, "f_max": 844e6,
            # Outside the monotonic range (generated VCO, Xyce): 1.09 GHz
            # at Vctrl = 0 V, minimum 511 MHz at 0.6 V, then a 625 MHz
            # floor above 0.9 V where the Vctrl PFET is off and Istart plus
            # the diode set the ring current.
            "f_vctrl0": 1.093e9,
            # Highest input frequency at which the emitted 2^n divider
            # chain was verified to count correctly (it mis-counts at
            # 1.09 GHz, so a loop started from a discharged filter stalls).
            "div_fmax": 844e6,
        },
    },
}


def get_process_params(process: str) -> dict:
    """Return process parameters for the given process name."""
    return _PROCESS_PARAMS[process]


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
