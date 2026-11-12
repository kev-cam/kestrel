* Kestrel PLL — Behavioral testbench (ngspice 45 + OpenVAF OSDI)
*
* CP, LF:        Verilog-A compiled via OpenVAF to OSDI (kes_cp.va, kes_lf.va)
* VCO:           XSPICE d_osc — event-driven, each edge scheduled from Vctrl
* PFD, Divider:  XSPICE d_dff digital code models
*
* The behavioral models of record are the generated Verilog-AMS set
* (kestrel pll gen -> kestrel_*.vams, event-driven logic); this deck is the
* stock-ngspice closed loop of the same architecture.
*
* Run from sim/:  openvaf kes_cp.va --output kes_cp.osdi
*                 openvaf kes_lf.va --output kes_lf.osdi
*                 ngspice -b kes_tb_beh.sp
.control
pre_osdi kes_cp.osdi
pre_osdi kes_lf.osdi
.endc

**********************************************************************
* Supply
**********************************************************************
Vdd vdd 0 1.8
Vss vss 0 0

**********************************************************************
* Reference clock — 25 MHz
**********************************************************************
Vref ref_clk 0 PULSE(0 1.8 0 100p 100p 20n 40n)

**********************************************************************
* PFD — XSPICE d_dff + d_and reset
**********************************************************************

* Analog-to-digital bridges
Aref_adc [ref_clk] [ref_d] adc_brg
Afb_adc  [fb_clk]  [fb_d]  adc_brg
.MODEL adc_brg adc_bridge(in_low=0.8 in_high=1.0 rise_delay=20p fall_delay=20p)

* Tie D inputs high
Ad_hi d_hi pull_hi
.MODEL pull_hi d_pullup(load=1p)

* XSPICE d_dff set/reset are ACTIVE-HIGH: hold set low (inactive)
As_lo set_hi pull_lo
.MODEL pull_lo d_pulldown(load=1p)

* UP DFF: D=1, CLK=ref_clk, SET=low, RST=rst_d (active-high)
Aup_ff d_hi ref_d set_hi rst_d up_d up_db dff_mod

* DN DFF: D=1, CLK=fb_clk, SET=low, RST=rst_d (active-high)
Adn_ff d_hi fb_d set_hi rst_d dn_d dn_db dff_mod

.MODEL dff_mod d_dff(clk_delay=50p set_delay=50p reset_delay=50p
+ ic=0 rise_delay=50p fall_delay=50p)

* Reset: AND(up, dn) drives the active-high resets directly
Arst_and [up_d dn_d] rst_d and_mod
.MODEL and_mod d_and(rise_delay=100p fall_delay=100p)

* Digital-to-analog bridges for UP/DN
Aup_dac [up_d] [up] dac_brg
Adn_dac [dn_d] [dn] dac_brg
.MODEL dac_brg dac_bridge(out_low=0 out_high=1.8 out_undef=0.9 t_rise=20p t_fall=20p)

**********************************************************************
* Charge pump (OSDI; the module is loaded by pre_osdi above, the .MODEL
* card instantiates it)
**********************************************************************
.MODEL cp_mod kes_cp(icp=0.0001 vdd=1.8 vth=0.9)
Ncp up dn cp_out cp_mod

**********************************************************************
* Loop filter (OSDI): R1 + C1 in series, C2 shunt; the control voltage is
* the charge-pump node (the R1-C1 zero stays visible to the VCO)
**********************************************************************
.MODEL lf_mod kes_lf(r1=2.015e+04 c1=5.895e-12 c2=4.56e-13)
Nlf cp_out vctrl lf_mod

**********************************************************************
* VCO — event-driven (XSPICE d_osc): every output edge is scheduled from
* the control voltage through the cntl_array -> freq_array table (the
* engine's sky130 law: 667 MHz at 0 V, 1000 MHz at 0.90 V,
* 1333 MHz at 1.8 V, 370 MHz/V; /32 at 25 MHz locks at 0.36 V).
* The digital output clk_d drives the divider directly; its dac bridge
* below only gives the solver a breakpoint per edge and an analog
* waveform for the measurements.  Loop values are the engine's for the
* 800 MHz-1.2 GHz / 25 MHz / 5 MHz sky130 example, like the generated
* kestrel_pll_behavioral.cir.
**********************************************************************
Avco vctrl clk_d vco_mod
Aclk_dac [clk_d] [clk_out] dac_brg
.MODEL vco_mod d_osc(cntl_array=[0 0.9 1.8] freq_array=[6.66667e+08 1e+09 1.33333e+09]
+ duty_cycle=0.5 init_phase=0 rise_delay=20p fall_delay=20p)

**********************************************************************
* Divider — toggle chain (D <- Qbar) of XSPICE flip-flops, set/reset
* held low; feedback from q4 = div-by-32 (25 MHz x 32 = 800 MHz, inside
* the behavioral VCO range; div-by-64 would ask for 1.6 GHz)
**********************************************************************
At1 tie_hi pull_lo

Aff0 q0b clk_d  tie_hi tie_hi q0 q0b tff_mod
Aff1 q1b q0     tie_hi tie_hi q1 q1b tff_mod
Aff2 q2b q1     tie_hi tie_hi q2 q2b tff_mod
Aff3 q3b q2     tie_hi tie_hi q3 q3b tff_mod
Aff4 q4b q3     tie_hi tie_hi q4 q4b tff_mod
Aff5 q5b q4     tie_hi tie_hi q5 q5b tff_mod

.MODEL tff_mod d_dff(clk_delay=50p set_delay=50p reset_delay=50p
+ ic=0 rise_delay=50p fall_delay=50p)

Afb_dac [q4] [fb_clk] dac_brg

**********************************************************************
* Monitor
**********************************************************************
Rmon vctrl vctrl_mon 1k

**********************************************************************
* Simulation
**********************************************************************
.TRAN 0.5n 4u UIC

.CONTROL
run

wrdata /tmp/kestrel_test/kes_beh_vctrl.csv V(vctrl_mon)
wrdata /tmp/kestrel_test/kes_beh_refclk.csv V(ref_clk)
wrdata /tmp/kestrel_test/kes_beh_fbclk.csv V(fb_clk)

meas tran vctrl_final FIND V(vctrl_mon) AT=3.9u
* settling: last time the control voltage was 3 % away from its lock value
meas tran t_lock WHEN V(vctrl_mon)=0.349 CROSS=LAST
* VCO frequency: 100 periods of clk_out in the last quarter of the run
meas tran t1 WHEN V(clk_out)=0.9 RISE=1 TD=3u
meas tran t2 WHEN V(clk_out)=0.9 RISE=101 TD=3u
let f_vco = 100/(t2 - t1)
echo
echo "=== Behavioral PLL Results ==="
print vctrl_final t_lock f_vco
echo

quit
.ENDC

.END
