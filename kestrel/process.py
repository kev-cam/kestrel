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
        "lmin": 340e-9,       # m — NMOS Lmin=0.34u (PMOS=0.28u, use larger)
        "l_analog": 500e-9,   # m — longer L for analog matching
        "kpn": 298.8e-6,      # A/V^2 — from IHP characterization (W/L=1u/1u)
        "kpp": 82.18e-6,      # A/V^2 — from IHP characterization
        "vtn": 0.255,         # V — NMOS threshold (nominal)
        "vtp": 0.353,         # V — PMOS |Vtp| (nominal)
        "vdd": 1.2,
        "model_lib": "cornerMOSlv",
        "corner": "mos_tt",
        "inst_prefix": "X",   # .SUBCKT wrappers — use X prefix
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
