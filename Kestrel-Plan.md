# Kestrel — Open-Source PLL Generator

**Parametric PLL synthesis: specs in, layout + behavioral model + SPICE out.**

Kestrel generates production-quality Phase-Locked Loop designs from a high-level
specification. Given target frequency range, jitter budget, loop bandwidth, and
process node, Kestrel emits:

- GDS/OASIS layout (via gdsfactory + KLayout)
- Verilog-AMS behavioral model
- SV-RNM behavioral model
- SPICE netlist (targeting Xyce and ngspice)
- PSL/SVA assertions for functional verification
- Probability waveform jitter model (statistical timing)

All outputs are parameterically consistent — the same computed component values
drive every representation.

## Architecture

The PLL topology is the Maneatis self-biased charge-pump PLL, whose foundational
patents have expired. This architecture offers wide frequency range, low jitter,
and inherent self-biasing that simplifies the generator — the bias point is set by
the circuit topology, not by external tuning.

### Core Blocks

| Block              | Description                                        |
|--------------------|----------------------------------------------------|
| VCO                | Differential ring oscillator, N stages configurable|
| Charge Pump        | Symmetric up/down with current matching             |
| Phase/Freq Detector| Standard tri-state PFD with dead-zone elimination   |
| Loop Filter        | On-chip R-C, second or third order                  |
| Frequency Divider  | Programmable integer-N, optional fractional-N ΔΣ    |
| Bias Generator     | Self-biased replica feedback (Maneatis technique)   |
| Output Buffers     | Configurable fan-out, optional differential         |

### Generator Flow

```
User Specification (Python dict or command line)
        │
        ▼
┌───────────────────┐
│  Design Engine    │  ← Computes all component values from specs
│  (analytical +    │     Loop stability analysis (phase margin, bandwidth)
│   optimization)   │     VCO gain, charge pump current, filter R/C values
└───────┬───────────┘
        │
        ├──────────────────┬──────────────────┬──────────────────┐
        ▼                  ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Layout Gen   │  │ Verilog-AMS  │  │ SPICE Netlist│  │ Prob Waveform│
│ (gdsfactory) │  │ + SV-RNM Gen │  │ Generator    │  │ Jitter Model │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │                  │
       ▼                 ▼                 ▼                  ▼
   .gds/.oas         .vams/.sv          .sp/.cir           .pwm
```

## Supported Process Nodes (Initial)

### Open PDKs (Day 1)

- **SkyWater SKY130** — open 130nm, 5-metal, via Google/Efabless
  shuttle program. Proven silicon path for prototyping.
- **GlobalFoundries GF180MCU** — open 180nm MCU process.
  Good for lower-frequency PLLs and educational use.

### Commercial PDKs (Roadmap)

The generator is process-independent. Commercial PDK support requires
the user to supply their own NDA'd PDK files in gdsfactory format.

- TSMC 28nm (N28HPC+)
- TSMC 16nm FinFET
- Samsung 14nm
- GlobalFoundries 22FDX
- Intel 16

## Project Structure

```
kestrel/
├── kestrel/
│   ├── __init__.py
│   ├── cli.py                  # Command-line interface
│   ├── spec.py                 # Specification parsing and validation
│   ├── design/
│   │   ├── __init__.py
│   │   ├── engine.py           # Top-level design engine
│   │   ├── vco.py              # VCO sizing and optimization
│   │   ├── charge_pump.py      # Charge pump sizing
│   │   ├── pfd.py              # Phase-frequency detector
│   │   ├── loop_filter.py      # Filter component computation
│   │   ├── divider.py          # Frequency divider
│   │   ├── bias.py             # Self-biased replica generator
│   │   └── stability.py        # Loop stability analysis
│   ├── layout/
│   │   ├── __init__.py
│   │   ├── pll_top.py          # Top-level PLL layout assembly
│   │   ├── vco_layout.py       # VCO layout generator
│   │   ├── cp_layout.py        # Charge pump layout
│   │   ├── pfd_layout.py       # PFD layout
│   │   ├── filter_layout.py    # Loop filter (MIM/MOM caps, poly res)
│   │   ├── divider_layout.py   # Divider (standard cells or custom)
│   │   └── guard_rings.py      # Substrate isolation structures
│   ├── models/
│   │   ├── __init__.py
│   │   ├── verilog_ams.py      # Verilog-AMS model emitter
│   │   ├── sv_rnm.py           # SystemVerilog RNM model emitter
│   │   ├── spice.py            # SPICE netlist emitter
│   │   ├── prob_waveform.py    # Probability waveform jitter model
│   │   └── psl_assertions.py   # PSL/SVA property emitter
│   ├── pdk/
│   │   ├── __init__.py
│   │   ├── sky130.py           # SkyWater 130nm device/layer maps
│   │   ├── gf180.py            # GF 180nm device/layer maps
│   │   └── template.py         # Template for new PDK bring-up
│   └── verify/
│       ├── __init__.py
│       ├── drc.py              # KLayout DRC runner
│       ├── lvs.py              # LVS check (netlist vs layout)
│       └── sim.py              # Xyce/ngspice simulation harness
├── tests/
│   ├── test_design_engine.py
│   ├── test_sky130_pll.py
│   ├── test_gf180_pll.py
│   ├── test_models.py
│   └── test_stability.py
├── examples/
│   ├── sky130_1ghz_pll/        # Complete worked example
│   ├── gf180_100mhz_pll/
│   └── serdes_refclk/          # SERDES reference clock generation
├── docs/
│   ├── theory.md               # PLL design theory and equations
│   ├── maneatis.md             # Self-biased architecture details
│   ├── adding_pdk.md           # How to add a new process node
│   └── verification.md         # Simulation and verification guide
├── pyproject.toml
├── LICENSE                     # Apache 2.0
├── README.md
└── PLAN.md                     # This file
```

## Usage

### Command Line

```bash
# Generate a 1 GHz PLL on SkyWater 130nm
kestrel gen \
  --freq-min 500M --freq-max 1.5G \
  --jitter 2ps \
  --loop-bw 5M \
  --ref-freq 50M \
  --process sky130 \
  --output ./my_pll/

# Interactive mode
kestrel interactive --process sky130
```

### Python API

```python
from kestrel import PLL

pll = PLL(
    freq_range=(500e6, 1.5e9),
    jitter_target=2e-12,
    loop_bandwidth=5e6,
    ref_freq=50e6,
    process="sky130",
)

# Design and verify
pll.design()                        # compute all parameters
pll.check_stability()               # phase margin, gain margin
pll.estimate_jitter()               # probability waveform analysis

# Generate outputs
pll.write_gds("my_pll.gds")
pll.write_verilog_ams("my_pll.vams")
pll.write_sv_rnm("my_pll.sv")
pll.write_spice("my_pll.sp")
pll.write_assertions("my_pll.psl")

# Run verification
pll.run_drc()                       # KLayout DRC
pll.run_lvs()                       # layout vs schematic
pll.run_sim(simulator="xyce")       # transient + jitter analysis
```

### Interactive Session

```
$ kestrel interactive --process sky130

Kestrel PLL Generator v0.1.0 — SkyWater SKY130

Target frequency range? 800M 1.2G
Reference clock? 25M
Jitter budget? 5ps
Power budget? 3mW

Designing...

  VCO: 4-stage differential ring, Kvco = 850 MHz/V
  Charge pump: Icp = 50 uA, symmetric cascode
  Loop filter: R = 12.4 kΩ, C1 = 28.3 pF, C2 = 2.83 pF
  Divider: integer-N, range 32-48
  Phase margin: 62.3°
  Estimated jitter: 3.8 ps rms (within budget)
  Estimated power: 2.4 mW (within budget)

Generate? [y/n] y

  ✓ my_pll.gds          (layout, 847 x 523 µm)
  ✓ my_pll.vams         (Verilog-AMS behavioral)
  ✓ my_pll.sv           (SV-RNM behavioral)
  ✓ my_pll.sp           (SPICE netlist, 342 devices)
  ✓ my_pll.psl          (12 assertions)
  ✓ DRC clean           (0 violations)
  ✓ LVS clean           (netlist matches layout)
```

## Design Engine Details

### VCO Design

The VCO uses a differential ring topology with replica-biased delay cells
(Maneatis style). The number of stages is chosen to meet the target frequency
range while maintaining adequate phase noise. The design engine:

1. Determines the required VCO gain (Kvco) from the frequency range and
   expected control voltage swing.
2. Selects the number of delay stages (typically 3-5 for the target range).
3. Sizes the delay cell transistors for the target current, frequency, and
   output swing.
4. Computes the symmetric load resistance for self-biasing.

### Loop Dynamics

The design engine solves the standard charge-pump PLL transfer function:

- Open-loop gain crossover at the specified loop bandwidth
- Phase margin ≥ 60° (configurable)
- Reference spur suppression via C2/C1 ratio
- Lock time estimation from the natural frequency and damping

The loop filter components (R, C1, C2) are computed analytically, then
optionally refined by numerical optimization against the full nonlinear
model.

### Jitter Estimation

Two methods:

1. **Linear noise analysis** — standard phase noise integration from the
   VCO, charge pump, and reference contributions through the closed-loop
   transfer function. Fast, good for initial sizing.

2. **Probability waveform analysis** — propagates probability distributions
   through the PLL model per US8478576, capturing the effects of device
   mismatch, supply noise, and thermal variation. Slower but gives the
   full statistical jitter distribution, not just rms.

## Dependencies

- **gdsfactory** (≥9.0) — layout generation and GDS output
- **KLayout** — DRC, LVS, layout viewing
- **NumPy/SciPy** — numerical computation
- **Xyce** (optional) — SPICE simulation
- **ngspice** (optional) — alternative SPICE simulation
- **NVC** (optional) — VHDL simulation of generated models

## Milestones

### M1: Design Engine (Month 1)

- Analytical PLL design from specs
- Loop stability analysis
- Jitter estimation (linear)
- Verilog-AMS and SPICE netlist output
- Unit tests against known-good PLL designs from literature

### M2: SkyWater 130nm Layout (Month 2)

- VCO layout generator
- Charge pump layout
- PFD (standard cells from Sky130 PDK)
- Loop filter (MIM caps + poly resistors)
- Top-level assembly with guard rings
- DRC clean on Sky130

### M3: Verification and GF180 (Month 3)

- LVS passing (netlist matches layout)
- Xyce simulation of extracted netlist
- Probability waveform jitter analysis
- GF180MCU PDK support
- SV-RNM model output

### M4: Polish and Release (Month 4)

- Interactive CLI
- Documentation and tutorials
- Complete worked examples with simulation results
- Comparison against published TCI Ultra PLL specs
- GitHub release, announce at OCP / Chiplet Summit

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
- **OAE cell synthesis** — Shannon Slot timing derived from Kestrel's
  reference clock output.

## License

Apache 2.0 — use it, modify it, ship it. Attribution appreciated.

## Prior Art and References

- J. Maneatis, "Low-jitter process-independent DLL and PLL based on
  self-biased techniques," JSSC, Nov 1996.
- J. Maneatis and M. Horowitz, "Precise delay generation using coupled
  oscillators," JSSC, Dec 1993.
- B. Razavi, "Design of Analog CMOS Integrated Circuits," Ch. 15 (PLLs).
- SkyWater SKY130 PDK: https://github.com/google/skywater-pdk
- GF180MCU PDK: https://github.com/google/gf180mcu-pdk
- gdsfactory: https://github.com/gdsfactory/gdsfactory

---

## Appendix A: LC-VCO Option

For sub-picosecond jitter applications (SERDES reference clocks, high-speed ADC
clocking), ring oscillator VCOs cannot compete with LC-tank VCOs. The Q factor
of an on-chip spiral inductor (typically 5-15) gives 10-20 dB better phase noise
than an equivalent ring oscillator. Kestrel includes an LC-VCO generator path
built on open-source inductor design and layout tools.

### Architecture

The LC-VCO uses a cross-coupled NMOS (or complementary NMOS/PMOS) negative
resistance pair with an on-chip spiral inductor and MOS varactor tank. The
cross-coupled pair topology is covered by expired patents from the late 1990s.

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
   ├────┬┘───┤
   │    │     │
   └────┴─────┘
        GND
```

### Open-Source Tool Chain

The LC-VCO generator uses four open-source tools in sequence:

**1. ASITIC (Berkeley) — Inductor Design Engine**

- Source: http://rfic.eecs.berkeley.edu/~niknejad/asitic.html
- Function: Given target L, Q, frequency, and process metal stack,
  sweeps the inductor geometry space (turns, width, spacing, diameter,
  shape) and identifies optimal designs.
- Input: Technology file describing metal layers, thicknesses,
  sheet resistance, substrate properties.
- Output: Geometry parameters, L/Q/SRF vs frequency, lumped
  equivalent circuit (pi-model with substrate parasitics).
- Process support: Technology files available for SkyWater 130nm
  (academic ports), IHP SG13G2, and readily created for any process
  with known metal stack parameters.

**2. RapidPassives — GDS Layout Generation**

- Source: https://github.com/milanofthe/rapidpassives
- License: Open source
- Function: Generates DRC-aware GDS for spiral inductors and
  transformers from geometry parameters.
- Supports: Spiral inductors (square, octagonal), symmetric inductors
  (with center tap for differential VCOs), transformers.
- Features: Via array generation, geometry validation (no clipping or
  overlap), arbitrary winding counts and ratios.
- Integration: Pure Python, outputs GDS directly, trivial to wire
  into gdsfactory component assembly.

**3. Mühlhaus RFIC Inductor Toolkit — Synthesis and Modeling**

- Source: https://github.com/VolkerMuehlhaus/RFIC-Inductor-Toolkit-Open
- License: MIT (open-sourced August 2025)
- Function: End-to-end inductor synthesis — from specs to DRC-clean
  layout with equivalent circuit model and Spectre netlist.
- Supports: Symmetric octagonal, symmetric square, asymmetric square
  inductors. Patterned ground shields (PGS) for improved Q.
- Limitation: EM simulation step uses Keysight ADS Momentum.
  Layout generation and equivalent circuit extraction logic is open
  and can be used independently with alternative EM solvers.
- Value: The equivalent circuit extraction methodology and PGS
  geometry generation are directly reusable.

**4. FastHenry / FastCap (MIT) — Inductance/Capacitance Extraction**

- Source: https://www.fastfieldsolvers.com/
- License: Open source (MIT origin)
- Function: 3D inductance and capacitance extraction from arbitrary
  conductor geometries. Purpose-built for IC interconnect and spiral
  inductors.
- Use in Kestrel: Post-layout verification of inductor L and Q.
  Cross-checks ASITIC's analytical model against a numerical
  field solution of the actual generated layout geometry.
- Alternative: OpenEMS (open-source FDTD) for full-wave EM
  verification when substrate effects are critical.

### LC-VCO Generator Flow

```
User specs (jitter target, freq range, process)
        │
        ▼
┌───────────────────┐
│  VCO Design Engine │  ← Determines required L, Q, tuning range
│                    │     from phase noise/jitter budget
│  Phase noise model:│     Leeson's equation + tank Q + bias current
│  PN = f(L, Q, I)  │     → minimum Q for jitter target
└───────┬───────────┘     → required L from target frequency
        │
        ▼
┌───────────────────┐
│  ASITIC            │  ← Sweep inductor geometry space
│                    │     for target L, Q at operating freq
│  Input: L, Q, f,  │
│    technology file │     Output: optimal (N, W, S, Dout, shape)
└───────┬───────────┘     + lumped equivalent circuit
        │
        ├─────────────────────────────────┐
        ▼                                 ▼
┌───────────────────┐           ┌───────────────────┐
│  RapidPassives     │           │  FastHenry         │
│                    │           │                    │
│  Generate GDS from │           │  Verify L, Q from  │
│  ASITIC geometry   │           │  3D field solution  │
│  + PGS if needed   │           │  of generated layout│
└───────┬───────────┘           └───────┬───────────┘
        │                               │
        ▼                               ▼
┌───────────────────┐           ┌───────────────────┐
│  gdsfactory        │           │  Check: L, Q meet  │
│                    │           │  spec? If not,      │
│  Assemble full VCO:│           │  iterate ASITIC     │
│  inductor +        │           │  with tighter       │
│  cross-coupled pair│           │  constraints        │
│  + varactor bank   │           └───────────────────┘
│  + bias circuit    │
│  + guard rings     │
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│  Output            │
│                    │
│  • VCO GDS layout  │
│  • SPICE netlist   │
│    (with extracted │
│    inductor model) │
│  • Verilog-AMS     │
│    behavioral VCO  │
└───────────────────┘
```

### Inductor Geometry Parameters

The ASITIC optimization explores this parameter space:

| Parameter       | Typical Range (Sky130)     | Description                    |
|-----------------|----------------------------|--------------------------------|
| Shape           | square, octagonal          | Octagonal preferred for Q      |
| N (turns)       | 2 - 8                      | More turns = more L, lower SRF |
| W (width)       | 3 - 15 µm                  | Wider = lower R, more C        |
| S (spacing)     | 2 - 5 µm (DRC limited)    | Tighter = more L, more C       |
| Dout (diameter) | 100 - 400 µm              | Larger = higher Q, more area   |
| Metal layer     | Top metal (M5 on Sky130)   | Thickest metal for lowest R    |
| PGS             | poly-Si patterned shield   | Reduces substrate coupling     |
| Center tap      | yes (for differential)     | Required for cross-coupled VCO |

### Equivalent Circuit Model

The generated inductor model uses a frequency-dependent pi-network:

```
        Ls          Rs(f)
  ──────/\/\/──────/\/\/──────
  │                           │
  │    Cp                Cp   │
  ├────||────┐     ┌────||────┤
  │          │     │          │
  │    Rsub  │     │  Rsub    │
  ├──/\/\/───┤     ├──/\/\/───┤
  │          │     │          │
  │    Csub  │     │  Csub    │
  ├────||────┘     └────||────┤
  │                           │
  ─────────────────────────────
             GND

  Ls   = series inductance
  Rs   = series resistance (frequency-dependent, skin/proximity)
  Cp   = parallel capacitance (interwinding + oxide)
  Rsub = substrate resistance
  Csub = substrate capacitance
```

Parameters are extracted by ASITIC's analytical model and verified by
FastHenry's numerical extraction. Both representations (lumped circuit
and S-parameter touchstone) are emitted for use in SPICE and
Verilog-AMS respectively.

### Varactor Bank

The LC tank tuning uses accumulation-mode MOS varactors (available in
both Sky130 and GF180). The varactor bank is split into:

- **Coarse tuning**: Switched MOM capacitor bank (4-6 bits) for
  covering the full frequency range without excessive VCO gain.
- **Fine tuning**: Analog varactor (continuous) driven by the PLL
  loop filter output. Kvco kept small for low phase noise.

The switched capacitor bank layout is generated by gdsfactory from
the required capacitance values computed by the design engine. Each
switch is a thick-oxide NMOS with known Ron, sized to maintain tank Q.

### Additional Milestones for LC-VCO

### M5: LC-VCO on SkyWater 130nm (Month 5-6)

- ASITIC technology file validation against published Sky130 inductor data
- RapidPassives integration into gdsfactory pipeline
- Cross-coupled NMOS pair layout generator (parameterized W/L, fingers)
- Varactor bank layout generator (switched cap + analog)
- Full LC-VCO assembly with PGS
- FastHenry extraction and verification loop
- DRC/LVS clean
- Phase noise simulation in Xyce

### M6: LC-VCO on GF180 + Characterization (Month 7)

- GF180MCU technology file for ASITIC
- Port layout generators to GF180 metal stack
- Silicon validation if shuttle run available (Efabless/Google MPW)
- Publish measured vs. simulated inductor Q and VCO phase noise

### LC-VCO References

- A. M. Niknejad and R. G. Meyer, "Analysis, Design, and Optimization
  of Spiral Inductors and Transformers for Si RF ICs," JSSC, 1998.
- ASITIC: http://rfic.eecs.berkeley.edu/~niknejad/asitic.html
- RapidPassives: https://github.com/milanofthe/rapidpassives
- Mühlhaus Inductor Toolkit: https://github.com/VolkerMuehlhaus/RFIC-Inductor-Toolkit-Open
- FastHenry: https://www.fastfieldsolvers.com/
- OpenEMS: https://openems.de/
- B. Razavi, "Design of Analog CMOS Integrated Circuits," Ch. 14 (Oscillators).
- T. H. Lee, "The Design of CMOS Radio-Frequency Integrated Circuits," Ch. 17-18.
