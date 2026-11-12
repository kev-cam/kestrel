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
        # vamos (spectre personality) on VACASK / Xyce: a wrapper library over
        # the PDK's own PSP103 cards and psp103.va (Spectre refuses the PDK
        # wrappers' m= parameter); the PDK directories below are passed as
        # -I include paths by the generated run_tb.sh.
        "vamos_model_files": ["sg13g2_lv_vamos.lib"],
        "pdk_root_env": "IHP_PDK_ROOT",
        "pdk_root_default": "/usr/local/src/IHP-Open-PDK",
        "pdk_include_dirs": ["ihp-sg13g2/libs.tech/verilog-a/psp103",
                             "ihp-sg13g2/libs.tech/ngspice/models"],
        # Transistor-level calibration (VACASK through vamos's spectre
        # personality, the PDK's own PSP103 — psp103.va on the PDK's
        # sg13g2_moslv_parm cards — TT, 1.2 V, 2026-11-12).  Sizing from
        # sim/kes_vco_xyce_sg13g2.cir; the numbers below were measured on
        # the *generated* VCO (kestrel_vco.sp, sg13_lv_* wrappers with
        # IHP's default S/D junction areas, CMOS output buffers) in 50 mV
        # steps: 1579 MHz at 0 V falling monotonically to 733 MHz at
        # 0.55 V, then back up to a 965 MHz floor above 0.9 V where the
        # Vctrl PFET is off and Istart plus the diode set the ring current.
        # The linear window is 0.15-0.45 V (1299 -> 812 MHz; a
        # least-squares fit gives Kvco = -1.634 GHz/V with residuals under
        # 1.3 %), Maneatis polarity (f falls as Vctrl rises), rail-to-rail
        # CMOS outputs.  ngspice with the PDK's OSDI psp103.va agrees
        # within 1 %.  Xyce's built-in level-103 on the flattened TT cards
        # runs the same ring ~45 % slower (844 MHz at 0.2 V, 517 at 0.5 V,
        # minimum 511 at 0.6 V), so the Xyce deck settles at a different
        # Vctrl: the PDK Verilog-A is the reference.  The design engine
        # uses this block instead of the square-law estimate, places the
        # spec's centre frequency on the fitted line (f_nom at vctrl_nom)
        # and the emitter swaps the charge-pump UP/DN inputs for the sign.
        "vco_calibration": {
            "tail_w": 4.0e-6, "tail_l": 0.5e-6,
            "diff_w": 2.0e-6, "diff_l": 0.34e-6,
            "load_w": 3.0e-6, "load_l": 0.5e-6,
            "bias_w": 2.0e-6, "bias_l": 0.5e-6,
            "i_start": 5.0e-6,
            "kvco": -1.634e9,         # Hz/V, signed; fit over 0.15-0.45 V
            "vctrl_nom": 0.30,        # V, middle of the linear window
            "f_nom": 1.044e9,         # Hz, the fitted line at vctrl_nom (measured 1036 MHz)
            "vctrl_min": 0.15, "vctrl_max": 0.45,
            "f_min": 812e6, "f_max": 1299e6,
            # Outside the window (generated VCO, VACASK): 1.58 GHz at
            # Vctrl = 0 V, minimum 733 MHz at 0.55 V, then the 965 MHz
            # floor above 0.9 V.
            "f_vctrl0": 1.579e9,
            # Highest input frequency at which the emitted 2^n divider
            # chain was verified to count /32 correctly (VACASK, PDK
            # PSP103, divider sized for a 960 MHz centre): 1.8 GHz, the
            # highest point tried and above the VCO's top speed, so a
            # power-up from a discharged filter stays within range.
            "div_fmax": 1.8e9,
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
