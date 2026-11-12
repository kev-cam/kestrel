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

- Verilog-AMS behavioral model set with event-driven logic (the VCO
  schedules the delay to its next edge; PFD and divider are logic; only the
  charge pump and loop filter are analog) and a closed-loop testbench
- nvc/stat-sim form of the same PLL (VHDL on stat-sim's `prob_load`
  discipline, fully event-driven) with timing variability and a
  Monte-Carlo runner
- SPICE netlist (targeting Xyce, ngspice, Spectre)
- KiCad schematic
- Behavioral PLL testbench for stock ngspice (XSPICE event-driven VCO,
  PFD and divider; generated per design)
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
  PFD, charge pump, loop filter, divider, top and a closed-loop testbench
  (`kestrel_tb.vams`).  Modelling rule (November 2026): behavioral models
  are Verilog-AMS with event-driven logic, and a VCO schedules the delay to
  its next edge from the control voltage instead of integrating a
  frequency.  The VCO is one process — the edge travelling round the ring
  — that reads V(vctrl) on entering each stage and waits that stage's
  delay (td_nom/(1 + s_td·(V − vctrl_nom)), td_nom = 1/(2·N·f_center)),
  toggling the output once round; no `idtmod`, no `$bound_step`, the
  simulator only lands on the edges.  PFD and divider are `always` blocks
  with `#` delays; the pump reads UP/DN in its analog block through
  `transition()`.  Every logic block carries stat-sim variability
  parameters (`sigma_mm` per-stage mismatch, `sigma_j` per-edge jitter,
  `seed`, drawn from a seeded `$random` via Box–Muller), defaulting to 0.
  Verified with Icarus Verilog (`tests/test_vams_logic.py`: the emitted
  PFD and divider verbatim, the VCO with its control port made real, and
  an exact event-driven stand-in for the analog pump + filter): sg13g2
  locks at 640.0 MHz / 0.350 V, sky130 at 1000.0 MHz / 0.900 V, |ref→fb|
  under 25 ps.
- **stat-sim emitter** (`kestrel/models/statsim.py`) — the nvc runtime form
  of the same PLL, in the style of stat-sim's `nvcgen.py`: VHDL entities on
  the `prob_load` discipline (`kestrel_vco_pl`, `kestrel_pfd_pl`,
  `kestrel_divider_pl`, `kestrel_cp_lf_pl`, `kestrel_pll_statsim`), a
  testbench and `run_statsim.sh`.  The charge pump + R1–C1/C2 filter is
  replaced by its exact closed form between pump events (x = drop across
  R1 relaxes to Icp·R1·C1/(C1+C2) with τ = R1·C1·C2/(C1+C2); the C1 node
  integrates it), so the control voltage is evaluated exactly at every VCO
  stage edge with no analog solver.  Variability generics: `SIGMA_MM`,
  `SIGMA_J`, `SIGMA_ICP` (UP/DN current mismatch), `SIGMA_REF` (reference
  edge jitter); `run_statsim.sh --mc N` runs seeds 1..N and tabulates lock
  time, frequency, period jitter and ref→fb offset (mean ± sd).  nvc runs
  the 2.6 us sg13g2 loop in 0.1 s (Xyce: 2.1 h): deterministic 639.97 MHz,
  lock at 1.24 us, 9 ps rms ref→fb; with 1 % stage jitter, 2 % stage
  mismatch, 5 % pump mismatch and 5 ps reference jitter: 5.6 ps rms period
  jitter, 45 ps rms ref→fb over 10 seeds.  Logic nets are `resolved_pl`,
  so stat-sim's metastable flops, taps and CDC traps attach directly (the
  PFD flops are the natural next target).  Needs a stat-sim checkout
  (`STATSIM_DIR`, PolyForm-Noncommercial) for `lib/statsim_disc.vhd`.
- **SPICE netlist emitter** (`kestrel/models/spice.py`) — full transistor-level
  netlist for all PLL blocks with testbench.  Emits a self-biased replica
  bias that tracks Vctrl (same topology as the validated `sim/` decks),
  differential-to-CMOS output buffers on the VCO (the static divider
  needs rail-to-rail clocks at any Vctrl), a six-NAND edge-triggered
  flip-flop with asynchronous reset used by the PFD and as the divider's
  toggle stage, an open-loop VCO characterization deck
  (`kestrel_vco_tb.sp`), and swaps the charge-pump UP/DN inputs for VCOs
  with negative Kvco.  The closed-loop testbench starts the loop filter at
  a defined point (`.IC`, 0 V or the process's calibrated minimum),
  releases the reference only after the VCO and the divider have produced
  their first edges (a reference edge arriving first latches UP, and one
  full-width pump pulse steps the control node by Icp·R1), and runs long
  enough for the slew-limited acquisition, which the design summary
  reports next to the linear lock-time estimate and the ripple step.
- **IHP SG13G2 transistor-level path** (November 2026) — Xyce-native PSP103
  TT cards plus wrappers with IHP's `sg13_lv_nmos/pmos` subcircuit
  interface, bundled in `sim/models/` and copied next to every generated
  netlist.  `kestrel pll gen --process sg13g2` output parses and runs in
  Xyce unmodified; the generated VCO is verified at 844 MHz (Vctrl 0.20 V)
  to 517 MHz (0.50 V), the divider chain at 640 and 844 MHz, the PFD
  standalone.  Process-calibrated sizing and the measured limits live in
  `kestrel/process.py` (`vco_calibration`).
- **Behavioral emitter** (`kestrel/models/behavioral.py`) — the generated
  closed loop for stock ngspice (`kestrel_pll_behavioral.cir`): XSPICE
  `d_osc` VCO (edges scheduled from a Vctrl→frequency table built from the
  calibration or the engine's law), `d_dff`/`d_and` PFD, `d_fdiv` divider,
  B-source pump with rail drop-out, R1–C1/C2 filter tapped at the pump
  node, reference released after the divider runs.  sg13g2 locks at
  639.996 MHz / 0.350 V in 0.09 s, sky130 at 999.3 MHz / 0.900 V in 0.13 s.
  Two ngspice lessons baked in: `d_osc` advances its phase only at analog
  time points, so its output is bridged to an analog node to make every
  edge a breakpoint; and XSPICE bridges default to 1 ns delays/ramps.
  (The behavioral KiCad schematic template still depicts the old B-source
  multiplier/SDT toy — open item.)
- **KiCad schematic emitter** (`kestrel/models/schematic.py`) — SVG and
  KiCad S-expression output.
- **Behavioral PLL testbench** (`sim/kes_tb_beh.sp`) — the PLL loop in
  ngspice: XSPICE `d_osc` VCO
  (event-driven; the Van der Pol Verilog-A VCO `kes_vco.va` is retired —
  it integrated a frequency), OSDI charge pump and loop filter
  (`kes_cp.va`, `kes_lf.va`), XSPICE PFD and toggle-chain divider.  Fixed
  in November 2026: XSPICE `d_dff` set/reset are active-high (the bench
  drove them as active-low and never released reset), the divider
  flip-flops had D tied high instead of D ← Q̄, the loop filter tapped the
  node across C1, the hand-picked filter (R1 = 20 kΩ) gave a 2 V ripple
  step once the tap was right, and the plain netlist never loaded its
  OSDI models (no `pre_osdi`, module names used as model names).  The
  circbyline launcher `kes_run_beh.sp` is gone: with `pre_osdi` the plain
  netlist loads its own OSDI models, and ngspice 45.2 segfaulted in the
  launcher's interactive measurements.  Now the loop values and VCO law
  are the engine's sky130
  example (667 MHz–1.33 GHz over 0–1.8 V), /32 at 25 MHz: locks at
  799.9 MHz with Vctrl = 0.360 V, settled by 0.28 us, 0.6 s of ngspice
  (11.7 s with the Van der Pol VCO).  ngspice 45.2 still segfaults at
  `quit` after writing the results.
- **Transistor-level VCO** (`sim/kes_vco_xyce.cir`) — 4-stage differential
  Maneatis ring oscillator on sky130 BSIM4 models. Runs in Xyce with
  `.STEP` parameter sweep.
- **VCO optimization framework** (`sim/opt/`) — Nelder-Mead sizing
  optimization with pluggable simulator (Xyce, Spectre) and extraction
  (Calibre, Magic, Quantus) backends.  `--refit` fits the transistor-level
  f(Vctrl) polynomial and emits it as the delay law of an event-driven
  Verilog-AMS VCO (`sim/opt/kes_vco_refit.vams`, same ports as the
  generated `kestrel_vco`).
- **VCO range characterization** (`sim/vco_range.sh`) — frequency-vs-Vctrl
  sweep of the event-driven ngspice VCO table (no OSDI needed).
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
- Probability-waveform aggregation of the stat-sim Monte-Carlo: the
  per-seed edge statistics exist (`run_statsim.sh --mc`); the P(edge, t)
  waveform of US8478576B1 is not yet built from them, and the PFD flops
  are not yet stat-sim's metastable cells
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
- The behavioral KiCad schematic template
  (`pll_behavioral.kicad_sch-template`) still draws the retired B-source
  multiplier/SDT loop; the `.cir` it accompanied is now the event-driven
  ngspice bench.
- The generated Verilog-AMS has not been run on a Verilog-AMS simulator
  here (vamos's `-ams` flow is not implemented yet); its logic is verified
  with iverilog and its nvc form with nvc.
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
  a divider that counts at the VCO's top speed (prescaler, open item); the
  testbench now releases the reference after the divider is running
  (`PULSE(0 1.2 115.8n ...)`, two divider periods after the ring starts),
  so the PFD's first event is a feedback edge and the pump's first pulse
  pushes the VCO towards the slow end, where the divider keeps counting.
  With that guard (Xyce, 3 us): locks.  The PFD's first event is the
  feedback edge at ~50 ns and the pump pushes the control node to
  0.43–0.46 V (VCO 563–617 MHz, divider counting) until the first
  reference edge at 116 ns; the loop then
  pulls the VCO up through 625 MHz at 0.65 us, peaks at 644 MHz (+0.7 %)
  at 1.0 us and is at 640.0 MHz from 1.6 us on.  Over 1.5–3 us:
  f = 640.00 MHz (±0.01 %), Vctrl 0.361–0.373 V (the 12 mV spread is the
  per-pulse ripple on the charge-pump node), ref→fb phase offset
  0.45–0.58 ns and steady, PFD pulses 4–5 % UP / 3 % DN (reset-path
  widths).  Lock well inside the emitted 2.6 us transient; the 3 us run
  took 2.1 h in Xyce (132k steps).
  Decks: `sim/kes_vco_xyce_sg13g2.cir` (hand sizing, no junction areas,
  17-point sweep ≈ 16 min) and the generated `kestrel_vco_tb.sp` (7 points
  ≈ 5 min; a closed-loop `kestrel_tb.sp` run ≈ 80 min for 2 us with the
  old tap and 2–3.5 h for 3 us with the charge-pump node as the control
  voltage — the pulse ripple costs Xyce many more time steps).  Note: on a
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
│               ├── behavioral.py    # Event-driven ngspice bench emitter
│               ├── spice.py         # Transistor-level SPICE netlist
│               ├── verilog_ams.py   # Verilog-AMS model emitter (event-driven logic)
│               ├── statsim.py       # nvc/stat-sim form with timing variability
│               ├── schematic.py     # SVG / KiCad PLL schematics
│               └── templates/       # SPICE and KiCad templates
├── sim/
│   ├── kes_cp.va                # Charge pump (Verilog-A / OSDI)
│   ├── kes_lf.va                # Loop filter (Verilog-A / OSDI)
│   ├── kes_tb_beh.sp            # Behavioral PLL testbench (ngspice, pre_osdi + XSPICE)
│   ├── kes_vco_xyce.cir         # Transistor VCO testbench (Xyce + sky130)
│   ├── kes_vco_xyce_sg13g2.cir # Transistor VCO testbench (Xyce + IHP SG13G2)
│   ├── vco_range.sh             # VCO range sweep script
│   ├── models/                  # sky130 BSIM4 cards; SG13G2 PSP103 cards + sg13_lv wrappers
│   └── opt/
│       ├── optimize_vco.py      # Sizing optimizer (main script)
│       ├── backends.py          # Pluggable sim/extraction backends
│       ├── config.yaml          # Optimization configuration
│       ├── best_vco.cir         # Best-found netlist
│       ├── kes_vco_refit.vams   # Refit event-driven Verilog-AMS VCO
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

- [x] Event-driven behavioral VCO everywhere (November 2026): the
      Verilog-AMS ring model schedules the delay to its next edge, the
      ngspice benches use XSPICE `d_osc`; the Van der Pol Verilog-A VCO is
      retired
- [x] Charge pump and loop filter Verilog-A models
- [x] Full PLL behavioral testbench (plain netlist, pre_osdi + XSPICE digital)
- [x] VCO range characterization script
- [x] Verilog-AMS model set with event-driven logic and a closed-loop
      testbench; logic verified with iverilog (sg13g2 640.0 MHz / 0.350 V,
      sky130 1000.0 MHz / 0.900 V)
- [x] nvc/stat-sim form with timing variability: 2.6 us of the sg13g2 loop
      in 0.1 s, lock at 1.24 us, 5.6 ps rms period jitter at 1 % stage
      jitter over 10 seeds
- [x] Generated event-driven ngspice bench per design (`kestrel_pll_behavioral.cir`)

### M3: Transistor-Level VCO — DONE

- [x] Xyce testbench with sky130 BSIM4 binned models
- [x] Maneatis delay cell + self-biased replica
- [x] Sizing optimization framework (Nelder-Mead)
- [x] Behavioral model refit to match transistor curve (emits the
      event-driven Verilog-AMS VCO with the fitted delay law)
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
- [x] IHP SG13G2: start-up race found and guarded.  With the tap fixed, a
      3 us run from 0.2 V with the reference running from t = 0 did not
      lock: the first reference edge set UP about 50 ns before the VCO
      (starts at ~11 ns) and the divider could produce a feedback edge;
      with the zero now visible that one pulse stepped the control node by
      Icp·R1 = 0.22 V, from 0.2 V to 0 V within 25 ns, the VCO ran to
      1.09 GHz, the divider produced no edges, UP stayed latched and the
      loop parked there for the whole run (the old tap survived the same
      race because its pulse only slewed C1 at 0.7 V/us).  The testbench
      now releases the reference after the divider is running (two divider
      periods after the ring starts, 116 ns here) and the design summary
      reports the ripple step Icp·R1 with a warning when it exceeds half
      the tuning range
- [x] IHP SG13G2: closed loop of the emitted PLL, tap fixed and reference
      released late (Xyce, 3 us): locks.  The PFD's first event is the
      feedback edge at ~50 ns and the pump pushes the control node to
      0.43–0.46 V (VCO 563–617 MHz, divider counting) until the first
      reference edge at 116 ns; the loop then
      pulls the VCO up through 625 MHz at 0.65 us, peaks at 644 MHz (+0.7 %)
      at 1.0 us and is at 640.0 MHz from 1.6 us on.  Over 1.5–3 us:
      f = 640.00 MHz (±0.01 %), Vctrl 0.361–0.373 V (the 12 mV spread is the
      per-pulse ripple on the charge-pump node), ref→fb phase offset
      0.45–0.58 ns and steady, PFD pulses 4–5 % UP / 3 % DN (reset-path
      widths).  Lock well inside the emitted 2.6 us transient; the 3 us run
      took 2.1 h in Xyce (132k steps)

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
- [ ] Probability waveform jitter analysis (the stat-sim Monte-Carlo runner
      gives the per-seed statistics; aggregate them into P(edge, t))
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
