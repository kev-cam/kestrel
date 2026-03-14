#!/bin/bash
# VCO range characterization — behavioral (ngspice/OSDI)
# Run from the sim/ directory: cd sim && bash vco_range.sh
#
# Prerequisites: compile .va → .osdi with OpenVAF:
#   openvaf kes_vco.va --output kes_vco.osdi

SIMDIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== VCO Range (Behavioral ngspice/OSDI) ==="
echo "Vctrl(V)  Freq(MHz)"

for vctrl in 0.1 0.2 0.3 0.5 0.7 0.9 1.0 1.1 1.3 1.5; do
  # Longer sim for low Vctrl (slow oscillation + startup time)
  if [ "$(echo "$vctrl < 0.3" | bc)" = "1" ]; then
    tstop=200n
  else
    tstop=40n
  fi

  cat > "$SIMDIR/_vco_tmp.sp" << SPEOF
* VCO at Vctrl=$vctrl
.control
osdi $SIMDIR/kes_vco.osdi
circbyline * VCO test Vctrl=$vctrl
circbyline Vctrl vc 0 $vctrl
circbyline .MODEL vmod kes_vco(kvco=1e9 vdd=1.8)
circbyline Nvco1 vc outp outn vmod
circbyline R1 outp 0 100k
circbyline R2 outn 0 100k
circbyline .tran 10p $tstop UIC
circbyline .end
run
meas tran t1 WHEN V(outp)=0.9 RISE=5
meas tran t2 WHEN V(outp)=0.9 RISE=6
let fmhz = 1e-6/(t2 - t1)
print fmhz
quit
.endc
SPEOF
  freq=$(timeout 120 ngspice "$SIMDIR/_vco_tmp.sp" </dev/null 2>&1 | grep "fmhz" | tail -1 | awk '{print $3}')
  printf "%-9s %s\n" "$vctrl" "$freq"
done

rm -f "$SIMDIR/_vco_tmp.sp"
