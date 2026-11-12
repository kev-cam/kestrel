#!/bin/bash
# VCO range check — the event-driven behavioral VCO of the ngspice benches
# (XSPICE d_osc: every output edge scheduled from the control voltage through
# the Vctrl -> frequency table).  Prints frequency against control voltage for
# the table kes_tb_beh.sp uses (the engine's sky130 law:
# 667 MHz at 0 V, 1 GHz at 0.9 V, 1.33 GHz at 1.8 V).  No OSDI needed.
# Run: bash sim/vco_range.sh

SIMDIR="$(cd "$(dirname "$0")" && pwd)"
TABLE="cntl_array=[0 0.9 1.8] freq_array=[6.66667e+08 1e+09 1.33333e+09]"

echo "=== VCO range (ngspice d_osc, event-driven) ==="
echo "Vctrl(V)  Freq(MHz)"

for vctrl in 0.0 0.2 0.4 0.6 0.9 1.2 1.5 1.8; do
  cat > "$SIMDIR/_vco_tmp.sp" << SPEOF
* VCO at Vctrl=$vctrl
Vctrl vc 0 $vctrl
Avco vc clk_d vmod
.MODEL vmod d_osc($TABLE duty_cycle=0.5 init_phase=0 rise_delay=20p fall_delay=20p)
* the bridge gives the solver a breakpoint per edge and a waveform to measure
Aclk [clk_d] [clk] dac_brg
.MODEL dac_brg dac_bridge(out_low=0 out_high=1.8 t_rise=20p t_fall=20p)
.tran 0.1n 300n
.control
run
meas tran t1 WHEN V(clk)=0.9 RISE=11
meas tran t2 WHEN V(clk)=0.9 RISE=111
let fmhz = 100e-6/(t2 - t1)
print fmhz
.endc
.end
SPEOF
  freq=$(timeout 120 ngspice -b "$SIMDIR/_vco_tmp.sp" </dev/null 2>&1 | grep "^fmhz" | tail -1 | awk '{print $3}')
  printf "%-9s %s\n" "$vctrl" "$freq"
done

rm -f "$SIMDIR/_vco_tmp.sp"
