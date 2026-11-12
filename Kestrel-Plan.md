# Kestrel — Open-Source Circuit Generator Framework

**Parametric circuit synthesis: specs in, behavioral model + SPICE + schematic out.**

Kestrel is a plugin-based generator framework.  Each generator takes a
high-level specification and emits SPICE netlists, schematics, and layout.

**Built-in generators:**
- **PLL** — charge-pump PLL (Maneatis topology)

**External plugins** (via `KESTREL_PLUGINS` env var):
- **grail** (`/usr/local/src/grail`) — 2:1 resonant SC power pad for DVFS

### Usage

```bash
# PLL generator (sky130 default)
kestrel pll gen --freq-min 800M --freq-max 1.2G --ref-freq 25M --loop-bw 5M

# IHP SG13G2: models are bundled, the output simulates as-is
kestrel pll gen --freq-min 560M --freq-max 720M --ref-freq 20M --loop-bw 2M --process sg13g2
(cd kestrel_out && Xyce kestrel_vco_tb.sp)   # open-loop VCO sweep
(cd kestrel_out && Xyce kestrel_tb.sp)       # closed loop

# Grail generator (external plugin)
KESTREL_PLUGINS=/usr/local/src/grail kestrel grail gen \
    --v-in 1.2 --v-core 0.6 --i-avg 10m --f-fire-min 500M
```

## PLL Generator

Kestrel's PLL generator produces Phase-Locked Loop designs from a specification.
Given target frequency range, jitter budget, loop bandwidth, and process node,
Kestrel emits:

- Verilog-AMS behavioral model
- SPICE netlist (targeting Xyce, ngspice, Spectre)
- KiCad schematic
- Behavioral PLL testbench (ngspice/OSDI)
- Transistor-level VCO testbench (Xyce + sky130 BSIM4)

All outputs are parametrically consistent — the same computed component values
drive every representation.

## Current Status (March 2026, SG13G2 path updated November 2026)

### Working

- **Design engine** (`kestrel/design/engine.py`) — full analytical PLL sizing
  from specs: divider ratio, VCO gain, charge pump current, loop filter R/C,
  transistor W/L, phase margin, jitter estimate. Supports sky130 and gf180.
- **CLI** (`kestrel/cli.py`) — `kestrel gen` and `kestrel gui` subcommands.
- **Tkinter GUI** (`kestrel/gui.py`) — interactive spec entry and generation.
- **Verilog-AMS emitter** (`kestrel/models/verilog_ams.py`) — generates VCO,
  PFD, charge pump, loop filter, divider modules.
- **SPICE netlist emitter** (`kestrel/models/spice.py`) — full transistor-level
  netlist for all PLL blocks with testbench.  Emits a self-biased replica
  bias that tracks Vctrl (same topology as the validated `sim/` decks),
  differential-to-CMOS output buffers on the VCO (the static divider
  needs rail-to-rail clocks at any Vctrl), a six-NAND edge-triggered
  flip-flop with asynchronous reset used by the PFD and as the divider's
  toggle stage, an open-loop VCO characterization deck
  (`kestrel_vco_tb.sp`), and swaps the charge-pump UP/DN inputs for VCOs
  with negative Kvco.  The closed-loop testbench starts the loop filter at
  a defined point (`.IC`, 0 V or the process's calibrated minimum) and runs
  long enough for the slew-limited acquisition, which the design summary
  reports next to the linear lock-time estimate.
- **IHP SG13G2 transistor-level path** (November 2026) — Xyce-native PSP103
  TT cards plus wrappers with IHP's `sg13_lv_nmos/pmos` subcircuit
  interface, bundled in `sim/models/` and copied next to every generated
  netlist.  `kestrel pll gen --process sg13g2` output parses and runs in
  Xyce unmodified; the generated VCO is verified at 844 MHz (Vctrl 0.20 V)
  to 517 MHz (0.50 V), the divider chain at 640 and 844 MHz, the PFD
  standalone.  Process-calibrated sizing and the measured limits live in
  `kestrel/process.py` (`vco_calibration`).
- **Behavioral emitter** (`kestrel/models/behavioral.py`) — template-based
  behavioral SPICE output.
- **KiCad schematic emitter** (`kestrel/models/schematic.py`) — SVG and
  KiCad S-expression output.
- **Behavioral VCO model** (`sim/kes_vco.va`) — Van der Pol oscillator in
  Verilog-A, compiled to OSDI for ngspice. Self-starting, frequency
  proportional to V(vctrl).
- **Behavioral PLL testbench** (`sim/kes_run_beh.sp`, plain-netlist twin
  `sim/kes_tb_beh.sp`) — full PLL loop in ngspice using OSDI models (VCO,
  charge pump, loop filter) + XSPICE digital (PFD, divider).  Fixed in
  November 2026: XSPICE `d_dff` set/reset are active-high (the bench drove
  them as active-low and never released reset), the divider flip-flops had
  D tied high instead of D ← Q̄, the loop filter tapped the node across C1,
  and /64 asked for 1.6 GHz, outside the behavioral VCO's range.  Now /32:
  locks at 800 MHz (25 MHz reference) with the ref→fb phase error bounded
  to ±1.4 ns; ngspice 45.2 segfaults at `quit` after writing the results.
- **Transistor-level VCO** (`sim/kes_vco_xyce.cir`) — 4-stage differential
  Maneatis ring oscillator on sky130 BSIM4 models. Runs in Xyce with
  `.STEP` parameter sweep.
- **VCO optimization framework** (`sim/opt/`) — Nelder-Mead sizing
  optimization with pluggable simulator (Xyce, Spectre) and extraction
  (Calibre, Magic, Quantus) backends. Refits behavioral model polynomial
  to match transistor-level data.
- **VCO range characterization** (`sim/vco_range.sh`) — automated
  frequency-vs-Vctrl sweep for behavioral model.
- **KiCad VCO schematic** (`sch/kes_vco_delay_cell.kicad_sch`) —
  Maneatis delay cell with NMOS/PMOS symbols, generated programmatically.

### Key Results

| Metric | Behavioral | Transistor (sky130) | Transistor (SG13G2, generated) |
|--------|-----------|---------------------|--------------------------------|
| VCO range | 71–1056 MHz | 477–789 MHz | 517–844 MHz (monotonic part; 1.09 GHz at 0 V) |
| Kvco | ~707 MHz/V (linear) | ~298 MHz/V (saturating) | ~−1.09 GHz/V (monotonic, Maneatis polarity) |
| Valid Vctrl | 0.1–1.5 V | 0.7–1.5 V | 0.2–0.5 V |
| Simulator | ngspice + OSDI | Xyce | Xyce (PSP103) |

Optimization converged in 112 evaluations (cost 0.363 → 0.249).
Refit behavioral model matches transistor curve within ±3.3%.

- **GDSII layout generator** (`layout/gds_gen.py`) — gdsfactory-based,
  full PLL layout from design engine parameters.  Sky130 layers,
  parameterized NFET/PFET primitives with contact stacks, Maneatis
  delay cell, VCO ring, charge pump, PFD, loop filter, divider, and
  top-level assembly with inter-block routing.
- **KLayout netlist extraction** (`layout/extract.py`) — MOS3Transistor
  device recognition from physical geometry.  Flat extraction with
  sky130 layer connectivity (DIFF→LICON→LI→MCON→MET1→VIA1→MET2→VIA2→MET3).
- **Parasitic analysis** (`layout/parasitics.py`) — wire R/C from
  layout geometry using sky130 interconnect parameters.  Impact estimates
  for VCO frequency shift, loop bandwidth, supply IR drop, phase noise,
  and inter-stage crosstalk.
- **Layout vs design comparison** (`layout/compare.py`) — geometric W/L
  verification of extracted devices against design-engine sizing.
- **Iterative design loop** (`layout/iterate.py`) — closed-loop
  design→layout→extract→compare flow with parasitic feedback.

### Not Yet Implemented

- SPICE-in-the-loop VCO frequency tuning (ngspice/Xyce on extracted netlist)
- DRC clean on sky130
- LVS passing (full hierarchical)
- SV-RNM behavioral model
- PSL/SVA assertions
- Probability waveform jitter model
- LC-VCO path (inductor design, see Appendix A)
- Programmable feedback divider: the emitted chain divides by 2^n, so
  closed-loop runs need a spec whose nominal N is a power of two
- sky130 generated netlists still need the PDK's subcircuit model mapping
  (the bundled `sim/models` sky130 cards are binned `__model.N` names)
- Engine loop equations omit the 2π of the PFD gain (Icp/2π A/rad):
  `natural_freq` and `damping` come out √(2π) too high (ζ = 1.8 reported,
  0.72 realized), `lock_time` 2π too short, and C1 = Icp·Kvco/(N·ω_bw²)
  places the real unity-gain crossover at γ/2π ≈ 0.59 × the requested loop
  bandwidth (phase margin there ≈ 57° for a 60° request).  Stable, but the
  bandwidth and settling numbers in the summary are not the realized ones.
- The behavioral SPICE template (`pll_behavioral.cir-template`) is a
  multiplier-PD toy with a voltage-driven RC and an SDT() VCO, not a
  charge-pump loop; the OSDI bench `sim/kes_run_beh.sp` is the behavioral
  reference.
- A faster first divider stage (prescaler): the six-NAND toggle stage is
  verified to 844 MHz on SG13G2 and mis-counts at 1.09 GHz, which is where
  the generated VCO sits with a discharged loop filter.  A cold power-up
  from 0 V is therefore not covered; the testbench starts at the
  calibrated 0.2 V and the design summary warns about it.  With the
  loop-filter tap fixed this matters during acquisition too: every
  full-width charge-pump pulse moves the control node by Icp·R1 (0.22 V
  here, more than half the tuning range), so a cycle slip in the fast
  direction can push the VCO past the divider's range, and the loop then
  latches UP with nothing to reset it.  Robust cold acquisition needs the
  divider rated for the VCO's top speed.

## Architecture

The PLL topology is the Maneatis self-biased charge-pump PLL, whose foundational
patents have expired (US5727038, US5736892). This architecture offers wide
frequency range, low jitter, and inherent self-biasing.

### Core Blocks

| Block              | Description                                        | Status |
|--------------------|----------------------------------------------------|--------|
| VCO                | 4-stage differential ring, Maneatis delay cell     | Done   |
| Charge Pump        | Symmetric up/down with current matching             | Done   |
| Phase/Freq Detector| Standard tri-state PFD with dead-zone elimination   | Done   |
| Loop Filter        | On-chip R-C, second order                           | Done   |
| Frequency Divider  | Programmable integer-N (div-by-64 in testbench)     | Done   |
| Bias Generator     | Self-biased replica feedback (Maneatis technique)   | Done   |

### Generator Flow

```
User Specification (Python dict or command line)
        │
        ▼
┌───────────────────┐
│  Design Engine    │  ← Computes all component values from specs
│  (analytical)     │     Loop stability, VCO gain, charge pump, filter R/C
└───────┬───────────┘
        │
        ├──────────────────┬──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Verilog-AMS  │  │ SPICE Netlist│  │ KiCad        │
│ + Behavioral │  │ Generator    │  │ Schematic    │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                  │
       ▼                 ▼                  ▼
    .vams/.cir        .sp/.cir          .kicad_sch
```

### Transistor-Level Verification Flow

```
Schematic netlist (from design engine)
        │
        ▼
┌───────────────────┐
│  Xyce / Spectre   │  ← Transient simulation with sky130 BSIM4
│  .STEP sweep      │     Frequency measurement via zero-crossing
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│  Optimizer        │  ← Nelder-Mead / Powell / differential evolution
│  (sim/opt/)       │     Cost = MSE(f_target, f_measured)
└───────┬───────────┘
        │
        ├──────────────────┐
        ▼                  ▼
┌──────────────┐  ┌──────────────┐
│ Best sizing  │  │ Refit model  │
│ (best_vco)   │  │ (polynomial) │
└──────────────┘  └──────────────┘
```

## Supported Process Nodes

### Open PDKs

- **SkyWater SKY130** — open 130nm, 5-metal. Sky130 BSIM4 models
  integrated and tested with Xyce (binned models, TT corner).
- **GlobalFoundries GF180MCU** — open 180nm MCU process.
  Supported in design engine; transistor-level sim not yet validated.
- **IHP SG13G2** — open 130nm SiGe BiCMOS (low-voltage 1.2V CMOS,
  PSP 103 models, Apache-2.0 IHP-Open-PDK).  Nothing to install: the TT
  model cards are bundled as Xyce-native level-103 `.model`s
  (`sim/models/sg13g2_psp103_tt.lib`, all 341 numeric parameters identical
  to IHP's `mos_tt` section with the corner multipliers evaluated) behind
  wrappers that reproduce IHP's `sg13_lv_nmos / sg13_lv_pmos  d g s b
  w= l= ng= m=` interface with the PDK's default source/drain areas
  (`sim/models/sg13g2_lv.lib`).  IHP's own `libs.tech/xyce` library needs
  the `YPSP103_VA` device that stock Xyce builds do not have, which is why
  the flattened cards are used.  Design engine: kpn=299uA/V², vtn=0.255V,
  kpp=82uA/V², vtp=0.353V, plus a transistor-level `vco_calibration`
  (tail 4u/0.5u, pair 2u/0.34u, loads 3u/0.5u) measured on the generated
  VCO: 844 MHz at Vctrl 0.20 V falling to 517 MHz at 0.50 V, Kvco ≈ −1.09
  GHz/V — Maneatis polarity, so the emitter swaps the charge-pump UP/DN
  inputs.  Full curve of the generated VCO (Xyce, 0.1 V steps): 1093 MHz at
  0 V, 968 at 0.1, 844 at 0.2, 723 at 0.3, 609 at 0.4, 517 at 0.5, a
  minimum of 511 MHz at 0.6 V, then back up to a 625 MHz floor from 0.9 V
  (Vctrl PFET off; Istart and the replica diode set the ring current).
  Stay within 0.2–0.5 V.  The emitted 2^n divider counts cleanly at 640
  and 844 MHz and mis-counts at 1093 MHz, so the closed-loop testbench
  starts the loop filter at 0.2 V (`.IC V(Xpll:vctrl)=0.2`) instead of a
  discharged 0 V, and `kestrel pll gen` warns about it.  Acquisition is
  slew-limited: the charge pump moves the filter at about Icp/2/(C1+C2)
  while the PFD cycle-slips (0.34 V/us for the 2 MHz-bandwidth example),
  so the summary's linear lock time (230 ns) understates the time to lock
  (estimate 0.67 us from 0.2 V; an undefined start left the loop parked at
  1.2 V on the 625 MHz floor and still unlocked after 2 us).  Closed loop
  from 0.2 V (Xyce): the loop overshoots to 0.50 V at 0.85 us and is within
  3 % of 640 MHz by 1.3 us.  With the original loop-filter tap (control
  voltage taken across C1) it then never settled: Vctrl rang ±17 mV at
  ~0.85 MHz with ±9 ns of phase error, undamped over 3 us — the natural
  frequency of a double integrator with no zero, sqrt(Icp·Kvco/(2π·N·(C1+C2)))
  = 0.77 MHz.  With the charge-pump node as the control voltage (tap fixed,
  November 2026) a start-up problem shows instead: the reference's first
  edge at t = 0 sets UP before any feedback edge exists (VCO start ~11 ns,
  first divider edge ≥ 50 ns), that one full-width pump pulse steps the
  control node by Icp·R1 = 0.22 V — from 0.2 V to 0 V in 25 ns — the VCO
  runs to 1.09 GHz, the divider stops producing edges, UP never resets and
  the loop parks at Vctrl = 0 V for the whole 3 us.  The cure in silicon is
  a divider that counts at the VCO's top speed (prescaler, open item); in
  the testbench the reference is released after the divider is running
  (under test).
  Decks: `sim/kes_vco_xyce_sg13g2.cir` (hand sizing, no junction areas,
  17-point sweep ≈ 16 min) and the generated `kestrel_vco_tb.sp` (7 points
  ≈ 5 min; a 2 us closed-loop `kestrel_tb.sp` run ≈ 80 min).  Note: on a
  Xyce that supplies PSP103 through the Verilog-A `.HDL` path (PyMS) every
  run pays a ~2 min model compile before the transient starts.

### Commercial PDKs (Roadmap)

The generator is process-independent. Commercial PDK support requires
the user to supply their own NDA'd PDK files.

## Project Structure

Kestrel is a multi-generator framework.  PLL is the built-in reference
generator; external generators (like grail) register as plugins via
the `KESTREL_PLUGINS` environment variable.

```
kestrel/
├── kestrel/
│   ├── __init__.py              # Package init, version
│   ├── __main__.py              # python -m kestrel entry point
│   ├── cli.py                   # Generic dispatcher (kestrel <gen> gen ...)
│   ├── spec.py                  # SI suffix parsing (parse_freq, parse_time)
│   ├── gui.py                   # Dynamic GUI — fields from active plugin
│   ├── process.py               # Shared process params (sky130, gf180, sg13g2)
│   ├── plugins.py               # Plugin discovery (built-in + KESTREL_PLUGINS)
│   ├── schematic.py             # Shared SVG drawing primitives
│   └── generators/
│       └── pll/
│           ├── kestrel_plugin.py    # PLL plugin registration
│           ├── engine.py            # PLLSpec, PLLDesign, design_pll
│           └── models/
│               ├── behavioral.py    # Behavioral SPICE emitter
│               ├── spice.py         # Transistor-level SPICE netlist
│               ├── verilog_ams.py   # Verilog-AMS model emitter
│               ├── schematic.py     # SVG / KiCad PLL schematics
│               └── templates/       # SPICE and KiCad templates
├── sim/
│   ├── kes_vco.va               # Van der Pol VCO (Verilog-A / OSDI)
│   ├── kes_cp.va                # Charge pump (Verilog-A / OSDI)
│   ├── kes_lf.va                # Loop filter (Verilog-A / OSDI)
│   ├── kes_run_beh.sp           # Behavioral PLL testbench (ngspice)
│   ├── kes_tb_beh.sp            # Behavioral PLL netlist
│   ├── kes_vco_xyce.cir         # Transistor VCO testbench (Xyce + sky130)
│   ├── kes_vco_xyce_sg13g2.cir # Transistor VCO testbench (Xyce + IHP SG13G2)
│   ├── vco_range.sh             # VCO range sweep script
│   ├── models/                  # sky130 BSIM4 cards; SG13G2 PSP103 cards + sg13_lv wrappers
│   └── opt/
│       ├── optimize_vco.py      # Sizing optimizer (main script)
│       ├── backends.py          # Pluggable sim/extraction backends
│       ├── config.yaml          # Optimization configuration
│       ├── best_vco.cir         # Best-found netlist
│       ├── kes_vco_refit.va     # Refit behavioral VCO model
│       └── refit_params.yaml    # Polynomial fit coefficients
├── sch/
│   ├── kes_vco_delay_cell.kicad_sch  # VCO delay cell schematic
│   ├── kes_vco_delay_cell.kicad_pro  # KiCad project
│   └── gen_delay_cell.py             # Schematic generator script
├── layout/
│   ├── gds_gen.py                     # GDSII layout generator (gdsfactory)
│   ├── extract.py                     # KLayout netlist extraction
│   ├── parasitics.py                  # Parasitic R/C estimation
│   ├── compare.py                     # Layout vs design-engine comparison
│   ├── iterate.py                     # Iterative design→layout→extract loop
│   ├── kestrel_pll.gds               # Generated PLL layout
│   └── kestrel_pll_flat_extracted.cir # Extracted SPICE netlist
├── tests/
│   ├── test_behavioral.py       # Emitter tests
│   └── test_sg13g2.py           # SG13G2 calibration, bundled models, Xyce parse check
├── docs/
│   └── index.html               # Project documentation (browser)
├── Kestrel-Plan.md              # This file
└── Expired-Patents.csv          # Patent expiry reference
```

## Design Engine Details

### VCO Design

The VCO uses a differential ring topology with replica-biased delay cells
(Maneatis style). The number of stages is chosen to meet the target frequency
range while maintaining adequate phase noise. The design engine:

1. Determines the required VCO gain (Kvco) from the frequency range and
   expected control voltage swing.
2. Selects the number of delay stages (typically 4 for the target range).
3. Sizes the delay cell transistors for the target current, frequency, and
   output swing.
4. Computes the symmetric load resistance for self-biasing.

#### Maneatis Delay Cell

```
        VDD
    ┌────┴────────────────┴────┐
    │                          │
  Mp1a(diode)  Mp1b(vctrl)  Mp2a(diode)  Mp2b(vctrl)
    │              │          │              │
    ├──────────────┤          ├──────────────┤
    │             outn        │             outp
    │                         │
  Mn1(inp)                 Mn2(inn)
    │                         │
    └────────┬────────────────┘
           tail
             │
          Mtail(vbn)
             │
            VSS
```

The symmetric load (diode PFET + Vctrl-controlled PFET) provides
voltage-controlled delay with approximately constant output swing.
The self-biased replica adjusts vbn to track Vctrl, maintaining
the operating point across the tuning range.

**Transistor-level findings (sky130):**
- Valid Vctrl range: 0.7–1.5 V (limited by PFET load balance)
- Frequency saturates above Vctrl ≈ 1.0 V (Vctrl PFET fully off)
- Effective Kvco ≈ 298 MHz/V in the active range
- Optimized sizing: W_tail=40u, W_diff=10u, W_pfet_diode=15u,
  W_pfet_ctrl=9u (all L=0.36u, bin 6)

### Loop Dynamics

The design engine solves the standard charge-pump PLL transfer function:

- Open-loop gain crossover at the specified loop bandwidth
- Phase margin ≥ 60° (configurable)
- Reference spur suppression via C2/C1 ratio
- Lock time estimation from the natural frequency and damping

The loop filter components (R, C1, C2) are computed analytically using
Gardner's method.

### Jitter Estimation

Linear noise analysis — standard phase noise integration from the VCO,
charge pump, and reference contributions through the closed-loop transfer
function.

## VCO Optimization Framework

The `sim/opt/` directory contains a complete optimization loop for matching
transistor-level VCO behavior to a target curve.

### Usage

```bash
cd sim
python3 opt/optimize_vco.py                          # default config
python3 opt/optimize_vco.py --method powell           # alternative optimizer
python3 opt/optimize_vco.py --max-iter 200 --refit    # optimize + refit behavioral
python3 opt/optimize_vco.py --refit-only              # refit from existing log
```

### Pluggable Backends

**Simulators** — implement `SimulatorBackend` in `backends.py`:

| Backend | Status | Notes |
|---------|--------|-------|
| Xyce | Working | sky130 BSIM4, .STEP sweep, .MEASURE |
| Spectre | Stub | Netlist template + PSF parser framework |

**Extractors** — implement `ExtractorBackend` in `backends.py`:

| Backend | Status | Notes |
|---------|--------|-------|
| None | Working | Schematic-level (no extraction) |
| Calibre | Stub | xRC runset generation |
| Magic | Stub | TCL script generation |
| Quantus | Stub | QRC command file generation |

### Configuration

All parameters in `opt/config.yaml`:
- Target f(Vctrl) curve
- Design variables with min/max bounds (respecting model bin boundaries)
- Simulator and extractor selection
- Optimizer method (nelder-mead, powell, cobyla, differential-evolution)
- Cost function (mse_relative, mse_absolute, max_relative)

## Layout Generation and Extraction

The `layout/` directory contains the physical design flow: generate GDSII
from the design engine, extract a SPICE netlist from the layout, compare
against the intended design, and estimate parasitic impact.

### Quick Start

```bash
cd /usr/local/src/kestrel

# Generate PLL layout (writes layout/kestrel_pll.gds)
python3 layout/gds_gen.py

# Extract SPICE netlist from layout
# (flatten first for cross-hierarchy via connectivity)
python3 -c "
import klayout.db as kdb
layout = kdb.Layout()
layout.read('layout/kestrel_pll.gds')
layout.top_cells()[0].flatten(True)
layout.write('/tmp/kestrel_pll_flat.gds')
"
python3 layout/extract.py /tmp/kestrel_pll_flat.gds -v

# Compare extracted W/L against design engine
python3 layout/compare.py

# Parasitic R/C analysis
python3 layout/parasitics.py layout/kestrel_pll.gds

# Iterative design→layout→extract loop
python3 layout/iterate.py --max-iter 5 --tol 5.0
```

### Layout Architecture

The layout generator uses three metal layers to avoid shorts:

| Layer | Usage |
|-------|-------|
| MET1 | Transistor contacts, local same-row wiring |
| MET2 | Intra-cell inter-row routing (diff pair ↔ tail, diff pair ↔ load) |
| MET3 | Inter-stage routing (VCO stage-to-stage, feedback) |

Each metal transition uses a via stack (VIA1 for MET1↔MET2, VIA2 for
MET2↔MET3).  This layering prevents MET2 polygon merging that would
short source and drain nets within the delay cell.

### Extraction Details

The KLayout extractor uses `DeviceExtractorMOS3Transistor` (3-terminal:
G, S, D) with the gate region as the `P` (oxide marker) layer.  This
avoids the S/D-to-bulk short that `DeviceExtractorMOS4Transistor` causes
when there is no explicit substrate tap layer.

Extraction must run on a **flattened** layout because VIA1 connections
between parent-level routing and child-cell MET1 contacts are not visible
across the GDS hierarchy boundary.

The extracted netlist includes per-device W, L, AS, AD, PS, PD parameters
with sky130 model names (`sky130_fd_pr__nfet_01v8`, `sky130_fd_pr__pfet_01v8`).

### Parasitic Feedback

The analytical parasitic model estimates ~4.3 fF per VCO output node from
MET1+MET2 routing (against a 20 fF intrinsic gate capacitance).  The design
engine compensates by:

1. Targeting a higher intrinsic VCO frequency:
   `f_intrinsic = f_target × (C_gate + C_parasitic) / C_gate`
2. De-rating Kvco for loop filter sizing:
   `Kvco_actual = Kvco_schematic × C_gate / (C_gate + C_parasitic)`

This gets within ~15% on the first analytical pass.  Closing the remaining
gap requires SPICE simulation of the extracted netlist (not yet automated).

### Key Results (March 2026)

| Metric | Value |
|--------|-------|
| Die size | 94 × 70 um |
| Devices extracted | 131 (44 NFET + 87 PFET) |
| Signal nets | 335 |
| Total wire R | 28 kohm |
| Total wire C | 197 fF |
| VCO parasitic C/node | 4.3 fF |
| VCO freq shift (analytical) | −15% |
| Supply IR drop | 0.2% |
| Phase noise degradation | 0.02 dB |
| Inter-stage crosstalk | 14% of load cap |

## Dependencies

- **NumPy / SciPy** — numerical computation, optimization
- **PyYAML** — configuration files
- **gdsfactory** — GDSII layout generation (`pip install gdsfactory`)
- **KLayout** — netlist extraction, Python bindings (`pip install klayout`)
- **Xyce** (optional) — transistor-level SPICE simulation
- **ngspice** (optional) — behavioral SPICE simulation
- **OpenVAF** (optional) — compile Verilog-A to OSDI for ngspice
- **KiCad** (optional) — schematic viewing and export

## Milestones

### M1: Design Engine — DONE

- [x] Analytical PLL design from specs
- [x] Loop stability analysis (phase margin)
- [x] Jitter estimation (linear)
- [x] Verilog-AMS and SPICE netlist output
- [x] CLI and GUI

### M2: Behavioral Simulation — DONE

- [x] Van der Pol VCO in Verilog-A (ngspice/OSDI)
- [x] Charge pump and loop filter Verilog-A models
- [x] Full PLL behavioral testbench (circbyline + XSPICE digital)
- [x] VCO range characterization script

### M3: Transistor-Level VCO — DONE

- [x] Xyce testbench with sky130 BSIM4 binned models
- [x] Maneatis delay cell + self-biased replica
- [x] Sizing optimization framework (Nelder-Mead)
- [x] Behavioral model refit to match transistor curve
- [x] KiCad schematic (delay cell)
- [x] IHP SG13G2: bundled Xyce-native PSP103 models + IHP-interface wrappers
- [x] IHP SG13G2: generated VCO verified in Xyce (517–844 MHz over Vctrl 0.5–0.2 V)
- [x] IHP SG13G2: generated divider (/32 at 640 and 844 MHz) and PFD verified standalone in Xyce
- [x] IHP SG13G2: closed-loop acquisition of the generated PLL in Xyce (/32,
      20 MHz ref → 640 MHz, 2 us): from Vctrl 0.2 V the loop overshoots to
      0.50 V at 0.85 us, is back within 3 % of 640 MHz by 1.3 us and at 2 us
      sits at 659 MHz with Vctrl flat at 0.355 V and the ref→fb phase error
      passing through zero (final overdamped settling, ζ = 1.8)
- [x] Loop-filter output tap fixed in the SPICE emitter, the Verilog-AMS
      emitter and `sim/kes_lf.va`: the VCO now sees the charge-pump node
      (with the R1–C1 zero), not the voltage across C1.  Behavioral check
      (ngspice/OSDI, /32, 800 MHz): old tap → phase error wandering ±20 ns,
      new tap → bounded ±1.4 ns
- [ ] IHP SG13G2: settled lock of the emitted PLL with the tap fixed.  The
      first 3 us run (from 0.2 V, reference running from t = 0) did not
      lock: the first reference edge sets UP about 50 ns before the VCO
      (starts at ~11 ns) and the divider can produce a feedback edge; with
      the zero now visible that one pulse steps the control node by
      Icp·R1 = 0.22 V, from 0.2 V to 0 V within 25 ns, the VCO runs to
      1.09 GHz, the divider produces no edges, UP stays latched and the
      loop parks there for the whole run.  The old tap survived the same
      race because its pulse only slewed C1 at 0.7 V/us.  Guard under
      test: the testbench releases the reference after the divider is
      running

### M4: Layout and Extraction — IN PROGRESS

- [x] gdsfactory layout generators (VCO, CP, PFD, filter, divider, top)
- [x] KLayout netlist extraction (MOS3, flat, sky130 layers)
- [x] Geometric sizing comparison (extracted W/L vs design engine)
- [x] Parasitic R/C estimation from wire geometry
- [x] Iterative design→layout→extract loop with parasitic feedback
- [x] Design engine parasitic-aware VCO sizing and Kvco de-rating
- [x] SPICE-in-the-loop VCO frequency tuning (Xyce, converges in 7 iterations)
- [ ] DRC clean on sky130
- [ ] LVS passing (full hierarchical)
- [ ] Extraction-in-the-loop optimization (KLayout or Magic)

### M5: LC-VCO Option — TODO

- [ ] ASITIC inductor design integration
- [ ] RapidPassives GDS generation
- [ ] Cross-coupled pair + varactor bank layout
- [ ] FastHenry verification loop

### M6: Polish — TODO

- [ ] Complete worked examples
- [ ] SV-RNM behavioral model
- [ ] PSL/SVA assertions
- [ ] Probability waveform jitter analysis
- [ ] pyproject.toml packaging

## Relationship to Cameron EDA Platform

Kestrel is a standalone tool, but integrates with the broader Cameron EDA
chiplet simulation and synthesis platform:

- **ltz** (mixed-signal simulation) — Kestrel's SPICE output runs directly
  in the ltz/Xyce federation for system-level chiplet simulation.
- **Haast** (IBIS-AMI converter) — Kestrel-generated PLLs can replace the
  opaque PLL models inside vendor IBIS-AMI characterizations with
  inspectable, simulatable equivalents.
- **NVC pipes** — when Kestrel generates a PLL for a SERDES bridge, the
  behavioral model plugs into NVC's pipe elaboration as a clock source
  for the link protocol FSM.
- **Smart SERDES** (US12206442B2) — Kestrel provides the system reference
  clock; Smart SERDES eliminates the per-lane CDR PLL entirely.

## License

Apache 2.0 — use it, modify it, ship it. Attribution appreciated.

## References

- J. Maneatis, "Low-jitter process-independent DLL and PLL based on
  self-biased techniques," JSSC, Nov 1996.
- J. Maneatis and M. Horowitz, "Precise delay generation using coupled
  oscillators," JSSC, Dec 1993.
- B. Razavi, "Design of Analog CMOS Integrated Circuits," Ch. 15 (PLLs).
- SkyWater SKY130 PDK: https://github.com/google/skywater-pdk
- GF180MCU PDK: https://github.com/google/gf180mcu-pdk
- gdsfactory: https://github.com/gdsfactory/gdsfactory
- ASITIC: http://rfic.eecs.berkeley.edu/~niknejad/asitic.html
- RapidPassives: https://github.com/milanofthe/rapidpassives
- OpenEMS: https://openems.de/

---

## Appendix A: LC-VCO Option

For sub-picosecond jitter applications (SERDES reference clocks, high-speed ADC
clocking), ring oscillator VCOs cannot compete with LC-tank VCOs. The Q factor
of an on-chip spiral inductor (typically 5-15) gives 10-20 dB better phase noise
than an equivalent ring oscillator.

### Architecture

The LC-VCO uses a cross-coupled NMOS negative resistance pair with an on-chip
spiral inductor and MOS varactor tank.

```
        VDD
         │
    ┌────┴────┐
    │  Ibias  │
    └────┬────┘
         │
   ┌─────┼─────┐
   │     │     │
 ┌─┴─┐ ┌┴┐ ┌─┴─┐
 │M1 │ │L│ │M2 │    L = spiral inductor
 └─┬─┘ └┬┘ └─┬─┘    C = varactor bank
   │  ┌─┴─┐  │      M1/M2 = cross-coupled pair
   │  │ C │  │
   │  └─┬─┘  │
   └────┴─────┘
        GND
```

### Open-Source Tool Chain

1. **ASITIC** (Berkeley) — inductor geometry optimization for target L, Q
2. **RapidPassives** — DRC-aware GDS generation for spiral inductors
3. **FastHenry** — 3D inductance/capacitance extraction for verification
4. **gdsfactory** — full VCO assembly (inductor + active + varactors)

This path is not yet implemented. See references in `Expired-Patents.csv`
for patent status of the cross-coupled topology.
