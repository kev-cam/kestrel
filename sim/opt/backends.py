"""
Pluggable simulator and extraction backends for VCO optimization.

Each backend implements a common interface:
  - Simulator:  generate_netlist(), run(), parse_results()
  - Extractor:  extract()

Add new backends by subclassing SimulatorBackend or ExtractorBackend.
"""

import abc
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Simulator interface
# ---------------------------------------------------------------------------

class SimulatorBackend(abc.ABC):
    """Base class for circuit simulators."""

    @abc.abstractmethod
    def generate_netlist(self, params: Dict[str, float], vctrl_points: List[float],
                         config: dict, work_dir: str,
                         extracted_netlist: Optional[str] = None) -> str:
        """Generate netlist file, return path."""

    @abc.abstractmethod
    def run(self, netlist_path: str, config: dict) -> Tuple[bool, str]:
        """Run simulation.  Returns (success, message)."""

    @abc.abstractmethod
    def parse_results(self, netlist_path: str, vctrl_points: List[float],
                      config: dict) -> Dict[float, Optional[float]]:
        """Parse measurement results.  Returns {vctrl: freq_hz or None}."""


# ---------------------------------------------------------------------------
# Xyce backend
# ---------------------------------------------------------------------------

class XyceBackend(SimulatorBackend):

    NETLIST_TEMPLATE = """\
* Kestrel VCO — optimization run (auto-generated)
*
**********************************************************************
* Process models
**********************************************************************
{slope_params}

.INCLUDE "{nfet_model}"
.INCLUDE "{pfet_model}"

**********************************************************************
* VCO subcircuits
**********************************************************************

.SUBCKT kestrel_delay_cell outp outn inp inn vctrl vbn vdd vss
Mtail  tail  vbn  vss  vss  sky130_fd_pr__nfet_01v8__model.6 W={W_tail} L=0.360u
Mn1    outn  inp  tail vss  sky130_fd_pr__nfet_01v8__model.6 W={W_diff} L=0.360u
Mn2    outp  inn  tail vss  sky130_fd_pr__nfet_01v8__model.6 W={W_diff} L=0.360u
Mp1a   outn  outn vdd  vdd  sky130_fd_pr__pfet_01v8__model.6 W={W_pfet_diode} L=0.360u
Mp1b   outn  vctrl vdd vdd  sky130_fd_pr__pfet_01v8__model.6 W={W_pfet_ctrl} L=0.360u
Mp2a   outp  outp vdd  vdd  sky130_fd_pr__pfet_01v8__model.6 W={W_pfet_diode} L=0.360u
Mp2b   outp  vctrl vdd vdd  sky130_fd_pr__pfet_01v8__model.6 W={W_pfet_ctrl} L=0.360u
.ENDS kestrel_delay_cell

.SUBCKT kestrel_vco_bias vbn vctrl vdd vss
Mrep_n   vbn  vbn  vss  vss  sky130_fd_pr__nfet_01v8__model.6 W={W_bias_n} L=0.360u
Mrep_pd  vbn  vbn  vdd  vdd  sky130_fd_pr__pfet_01v8__model.6 W={W_pfet_diode} L=0.360u
Mrep_pc  vbn  vctrl vdd vdd  sky130_fd_pr__pfet_01v8__model.6 W={W_pfet_ctrl} L=0.360u
Istart vdd vbn {I_start}
.ENDS kestrel_vco_bias

.SUBCKT kestrel_vco outp outn vctrl_ext vdd vss
Xbias vbn vctrl_int vdd vss kestrel_vco_bias
Rsw vctrl_ext vctrl 1
Rbias vctrl_int vctrl 100k
Xstage0 dp_0 dn_0 dn_3 dp_3 vctrl vbn vdd vss kestrel_delay_cell
Xstage1 dp_1 dn_1 dp_0 dn_0 vctrl vbn vdd vss kestrel_delay_cell
Xstage2 dp_2 dn_2 dp_1 dn_1 vctrl vbn vdd vss kestrel_delay_cell
Xstage3 dp_3 dn_3 dp_2 dn_2 vctrl vbn vdd vss kestrel_delay_cell
Routp dp_3 outp 1
Routn dn_3 outn 1
.ENDS kestrel_vco

**********************************************************************
* Testbench
**********************************************************************
Vdd vdd 0 1.8
Vss vss 0 0
Vctrl vctrl 0 {{vctrl_val}}
.PARAM vctrl_val = 0.9

Xvco outp outn vctrl vdd vss kestrel_vco

Cload_p outp 0 10f
Cload_n outn 0 10f
Ediff diff 0 outp outn 1.0
Ikick 0 Xvco:dp_0 PULSE(0 1m 0 50p 50p 10n 0)

.TRAN 50p {sim_time}
.PRINT TRAN V(outp) V(outn) V(diff)
.MEASURE TRAN t1 WHEN V(diff)=0 RISE={rise_a} TD={meas_td}
.MEASURE TRAN t2 WHEN V(diff)=0 RISE={rise_b} TD={meas_td}
.STEP vctrl_val LIST {vctrl_list}

.END
"""

    # sky130 mismatch params (TT corner = 0)
    SLOPE_PARAMS = """\
.PARAM sky130_fd_pr__nfet_01v8__toxe_slope = 0.0
.PARAM sky130_fd_pr__nfet_01v8__vth0_slope = 0.0
.PARAM sky130_fd_pr__nfet_01v8__voff_slope = 0.0
.PARAM sky130_fd_pr__nfet_01v8__vth0_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__toxe_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__vth0_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__voff_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__nfactor_slope = 0.0
.PARAM sky130_fd_pr__pfet_01v8__toxe_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__vth0_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__voff_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__nfactor_slope1 = 0.0
.PARAM sky130_fd_pr__pfet_01v8__wlod_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__kvth0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__ku0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__kvsat_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__lkvth0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__wkvth0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__lku0_diff = 0.0
.PARAM sky130_fd_pr__pfet_01v8__wku0_diff = 0.0"""

    def generate_netlist(self, params, vctrl_points, config, work_dir,
                         extracted_netlist=None):
        sim_cfg = config['simulator']
        vctrl_list = ' '.join(f'{v}' for v in sorted(vctrl_points))

        # Model file paths (relative to work_dir)
        nfet_model = 'models/sky130_nfet_xyce.spice'
        pfet_model = 'models/sky130_pfet_xyce.spice'

        fmt = {
            'slope_params': self.SLOPE_PARAMS,
            'nfet_model': nfet_model,
            'pfet_model': pfet_model,
            'sim_time': f"{float(sim_cfg['sim_time']):.0e}",
            'meas_td': f"{float(sim_cfg['meas_td']):.0e}",
            'rise_a': int(sim_cfg['meas_rise_a']),
            'rise_b': int(sim_cfg['meas_rise_b']),
            'vctrl_list': vctrl_list,
        }
        # Design variables — format widths in engineering notation
        for name, val in params.items():
            if name.startswith('W_') or name.startswith('I_'):
                fmt[name] = f"{val:.4e}"
            else:
                fmt[name] = str(val)

        netlist = self.NETLIST_TEMPLATE.format(**fmt)
        path = os.path.join(work_dir, '_opt_vco.cir')
        with open(path, 'w') as f:
            f.write(netlist)
        return path

    def run(self, netlist_path, config):
        binary = config['simulator'].get('binary', 'Xyce')
        work_dir = os.path.dirname(netlist_path)
        try:
            result = subprocess.run(
                [binary, os.path.basename(netlist_path)],
                cwd=work_dir,
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                # Check for specific errors
                if 'Abort' in result.stdout or 'Abort' in result.stderr:
                    return False, result.stdout[-500:] + result.stderr[-500:]
            return True, ''
        except subprocess.TimeoutExpired:
            return False, 'timeout'
        except Exception as e:
            return False, str(e)

    def parse_results(self, netlist_path, vctrl_points, config):
        base = netlist_path
        results = {}
        for i, vc in enumerate(sorted(vctrl_points)):
            mt_file = f'{base}.mt{i}'
            freq = None
            try:
                with open(mt_file) as f:
                    lines = f.readlines()
                t1 = t2 = None
                for line in lines:
                    if line.startswith('T1') and 'FAILED' not in line:
                        t1 = float(line.split('=')[1].strip())
                    elif line.startswith('T2') and 'FAILED' not in line:
                        t2 = float(line.split('=')[1].strip())
                if t1 is not None and t2 is not None and t2 > t1:
                    freq = 1.0 / (t2 - t1)
            except (FileNotFoundError, ValueError, IndexError):
                pass
            results[vc] = freq
        return results


# ---------------------------------------------------------------------------
# Spectre backend (stub — fill in when Spectre is available)
# ---------------------------------------------------------------------------

class SpectreBackend(SimulatorBackend):
    """Cadence Spectre simulator backend."""

    NETLIST_TEMPLATE = """\
// Kestrel VCO — Spectre optimization run (auto-generated)
simulator lang=spectre

// sky130 models
include "{model_path}/sky130_nfet.scs" section=tt
include "{model_path}/sky130_pfet.scs" section=tt

// --- Delay cell subcircuit ---
subckt kestrel_delay_cell (outp outn inp inn vctrl vbn vdd vss)
  Mtail  (tail  vbn  vss  vss)  nfet_01v8 w={W_tail} l=360n
  Mn1    (outn  inp  tail vss)  nfet_01v8 w={W_diff} l=360n
  Mn2    (outp  inn  tail vss)  nfet_01v8 w={W_diff} l=360n
  Mp1a   (outn  outn vdd  vdd)  pfet_01v8 w={W_pfet_diode} l=360n
  Mp1b   (outn  vctrl vdd vdd)  pfet_01v8 w={W_pfet_ctrl} l=360n
  Mp2a   (outp  outp vdd  vdd)  pfet_01v8 w={W_pfet_diode} l=360n
  Mp2b   (outp  vctrl vdd vdd)  pfet_01v8 w={W_pfet_ctrl} l=360n
ends kestrel_delay_cell

subckt kestrel_vco_bias (vbn vctrl vdd vss)
  Mrep_n  (vbn vbn vss vss)   nfet_01v8 w={W_bias_n} l=360n
  Mrep_pd (vbn vbn vdd vdd)   pfet_01v8 w={W_pfet_diode} l=360n
  Mrep_pc (vbn vctrl vdd vdd) pfet_01v8 w={W_pfet_ctrl} l=360n
  Istart  (vdd vbn) isource dc={I_start}
ends kestrel_vco_bias

subckt kestrel_vco (outp outn vctrl_ext vdd vss)
  Xbias (vbn vctrl_int vdd vss) kestrel_vco_bias
  Rsw   (vctrl_ext vctrl) resistor r=1
  Rbias (vctrl_int vctrl) resistor r=100k
  Xstage0 (dp_0 dn_0 dn_3 dp_3 vctrl vbn vdd vss) kestrel_delay_cell
  Xstage1 (dp_1 dn_1 dp_0 dn_0 vctrl vbn vdd vss) kestrel_delay_cell
  Xstage2 (dp_2 dn_2 dp_1 dn_1 vctrl vbn vdd vss) kestrel_delay_cell
  Xstage3 (dp_3 dn_3 dp_2 dn_2 vctrl vbn vdd vss) kestrel_delay_cell
  Routp (dp_3 outp) resistor r=1
  Routn (dn_3 outn) resistor r=1
ends kestrel_vco

// Testbench
Vdd  (vdd  0) vsource dc=1.8
Vss  (vss  0) vsource dc=0
Vctrl (vctrl 0) vsource dc=vctrl_val

Xvco (outp outn vctrl vdd vss) kestrel_vco
Cload_p (outp 0) capacitor c=10f
Cload_n (outn 0) capacitor c=10f
Ikick (0 Xvco.dp_0) isource type=pulse val0=0 val1=1m rise=50p fall=50p width=10n

tran1 tran stop={sim_time}
save outp outn
"""

    def generate_netlist(self, params, vctrl_points, config, work_dir,
                         extracted_netlist=None):
        # Spectre runs one Vctrl at a time (no built-in .STEP equivalent)
        # Generate one netlist per Vctrl point
        paths = []
        sim_cfg = config['simulator']
        for vc in sorted(vctrl_points):
            fmt = {
                'model_path': config['simulator'].get('model_path', '.'),
                'sim_time': f"{sim_cfg['sim_time']:.0e}",
            }
            for name, val in params.items():
                fmt[name] = f"{val:.4e}"

            netlist = self.NETLIST_TEMPLATE.format(**fmt)
            # Set vctrl_val
            netlist = netlist.replace('vctrl_val', str(vc))
            path = os.path.join(work_dir, f'_opt_vco_{vc:.2f}.scs')
            with open(path, 'w') as f:
                f.write(netlist)
            paths.append(path)
        # Store paths for run/parse
        self._netlist_paths = paths
        self._vctrl_points = sorted(vctrl_points)
        return paths[0]  # return first; run() handles all

    def run(self, netlist_path, config):
        binary = config['simulator'].get('binary', 'spectre')
        work_dir = os.path.dirname(netlist_path)
        for path in self._netlist_paths:
            try:
                result = subprocess.run(
                    [binary, '+aps', os.path.basename(path)],
                    cwd=work_dir,
                    capture_output=True, text=True, timeout=600
                )
                if result.returncode != 0:
                    return False, f'{path}: {result.stderr[-300:]}'
            except Exception as e:
                return False, str(e)
        return True, ''

    def parse_results(self, netlist_path, vctrl_points, config):
        # Spectre output parsing: read PSF or raw files
        # This is a stub — real implementation reads PSF binary or ASCII
        results = {}
        work_dir = os.path.dirname(netlist_path)
        sim_cfg = config['simulator']
        td = sim_cfg['meas_td']

        for i, vc in enumerate(sorted(vctrl_points)):
            results[vc] = None
            raw_dir = os.path.join(work_dir, f'_opt_vco_{vc:.2f}.raw')
            psf_dir = os.path.join(work_dir, f'_opt_vco_{vc:.2f}.psf')
            # Try ASCII raw format first
            tran_file = None
            for d in [raw_dir, psf_dir]:
                candidate = os.path.join(d, 'tran.tran')
                if os.path.exists(candidate):
                    tran_file = candidate
                    break
            if tran_file is None:
                continue
            try:
                times, outp, outn = [], [], []
                with open(tran_file) as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 3:
                            try:
                                t = float(parts[0])
                                vp = float(parts[1])
                                vn = float(parts[2])
                                times.append(t)
                                outp.append(vp)
                                outn.append(vn)
                            except ValueError:
                                continue
                # Find zero crossings of (outp - outn) after td
                diff = [p - n for p, n in zip(outp, outn)]
                crossings = []
                for j in range(1, len(diff)):
                    if times[j] > td and diff[j-1] < 0 and diff[j] >= 0:
                        # Linear interpolation
                        frac = -diff[j-1] / (diff[j] - diff[j-1])
                        tc = times[j-1] + frac * (times[j] - times[j-1])
                        crossings.append(tc)
                rise_a = sim_cfg['meas_rise_a'] - 1  # 0-indexed
                rise_b = sim_cfg['meas_rise_b'] - 1
                if len(crossings) > rise_b:
                    period = crossings[rise_b] - crossings[rise_a]
                    if period > 0:
                        results[vc] = 1.0 / period
            except Exception:
                pass
        return results


# ---------------------------------------------------------------------------
# Extraction interface
# ---------------------------------------------------------------------------

class ExtractorBackend(abc.ABC):
    """Base class for parasitic extraction tools."""

    @abc.abstractmethod
    def extract(self, config: dict, work_dir: str) -> Optional[str]:
        """Run extraction.  Returns path to extracted netlist, or None."""


class NoExtraction(ExtractorBackend):
    """Passthrough — no extraction, use schematic netlist directly."""
    def extract(self, config, work_dir):
        return None


class CalibreExtractor(ExtractorBackend):
    """Siemens Calibre xRC parasitic extraction."""

    def extract(self, config, work_dir):
        ext_cfg = config['extraction']
        rules = ext_cfg.get('rules_file', '')
        gds = ext_cfg.get('gds_file', '')
        output = os.path.join(work_dir, 'extracted.spice')

        # Calibre xRC command
        runset = os.path.join(work_dir, '_calibre_xrc.runset')
        with open(runset, 'w') as f:
            f.write(f"""\
*calibrerc*
*drcRulesFile: {rules}
*layoutPath: {gds}
*layoutPrimary: kestrel_vco
*extractionType: RC
*netlistFile: {output}
*format: SPICE
""")
        try:
            result = subprocess.run(
                ['calibre', '-xrc', '-pdb', runset],
                cwd=work_dir,
                capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0 and os.path.exists(output):
                return output
        except Exception:
            pass
        return None


class MagicExtractor(ExtractorBackend):
    """Magic VLSI parasitic extraction (open-source)."""

    def extract(self, config, work_dir):
        ext_cfg = config['extraction']
        tech = ext_cfg.get('tech_file', '')
        mag_file = ext_cfg.get('mag_file', '')
        cell_name = ext_cfg.get('cell_name', 'kestrel_vco')
        output = os.path.join(work_dir, 'extracted.spice')

        tcl_script = os.path.join(work_dir, '_extract.tcl')
        with open(tcl_script, 'w') as f:
            f.write(f"""\
tech load {tech}
load {mag_file}
select top cell
extract all
ext2spice lvs
ext2spice cthresh 0.01
ext2spice rthresh 10
ext2spice -o {output}
quit
""")
        try:
            result = subprocess.run(
                ['magic', '-dnull', '-noconsole', '-T', tech, tcl_script],
                cwd=work_dir,
                capture_output=True, text=True, timeout=300
            )
            if os.path.exists(output):
                return output
        except Exception:
            pass
        return None


class QuantusExtractor(ExtractorBackend):
    """Cadence Quantus QRC parasitic extraction."""

    def extract(self, config, work_dir):
        ext_cfg = config['extraction']
        tech_dir = ext_cfg.get('tech_dir', '')
        gds = ext_cfg.get('gds_file', '')
        cell_name = ext_cfg.get('cell_name', 'kestrel_vco')
        output = os.path.join(work_dir, 'extracted.spice')

        cmd_file = os.path.join(work_dir, '_quantus.cmd')
        with open(cmd_file, 'w') as f:
            f.write(f"""\
extract -selection all
input_db -type layout -directory_name {os.path.dirname(gds)} \\
         -file_name {os.path.basename(gds)} -top_cell {cell_name}
tech_setup -technology_library_file {tech_dir}/qrcTechFile
output_setup -net_name_space spice \\
             -file_name {output} -type spice
""")
        try:
            result = subprocess.run(
                ['quantus', '-cmd', cmd_file],
                cwd=work_dir,
                capture_output=True, text=True, timeout=600
            )
            if os.path.exists(output):
                return output
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

SIMULATORS = {
    'xyce': XyceBackend,
    'spectre': SpectreBackend,
}

EXTRACTORS = {
    'none': NoExtraction,
    'calibre': CalibreExtractor,
    'magic': MagicExtractor,
    'quantus': QuantusExtractor,
}


def get_simulator(config: dict) -> SimulatorBackend:
    tool = config['simulator']['tool'].lower()
    cls = SIMULATORS.get(tool)
    if cls is None:
        raise ValueError(f"Unknown simulator: {tool}. Available: {list(SIMULATORS)}")
    return cls()


def get_extractor(config: dict) -> ExtractorBackend:
    tool = config['extraction']['tool'].lower()
    cls = EXTRACTORS.get(tool)
    if cls is None:
        raise ValueError(f"Unknown extractor: {tool}. Available: {list(EXTRACTORS)}")
    return cls()
