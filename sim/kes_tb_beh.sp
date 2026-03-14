* Kestrel PLL — Behavioral testbench (ngspice 45 + OpenVAF OSDI)
*
* VCO, CP, LF: Verilog-A compiled via OpenVAF to OSDI
* PFD, Divider: XSPICE d_dff digital code models
*

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
.MODEL adc_brg adc_bridge(in_low=0.8 in_high=1.0)

* Tie D inputs high
Ad_hi d_hi pull_hi
.MODEL pull_hi d_pullup(load=1p)

* Tie set_bar high (inactive)
As_hi set_hi pull_hi

* UP DFF: D=1, CLK=ref_clk, SET=hi, RST=rst_n
Aup_ff d_hi ref_d set_hi rst_n up_d up_db dff_mod

* DN DFF: D=1, CLK=fb_clk, SET=hi, RST=rst_n
Adn_ff d_hi fb_d set_hi rst_n dn_d dn_db dff_mod

.MODEL dff_mod d_dff(clk_delay=50p set_delay=50p reset_delay=50p
+ ic=0 rise_delay=50p fall_delay=50p)

* Reset: AND(up, dn) inverted for active-low reset
Arst_and [up_d dn_d] rst_d and_mod
.MODEL and_mod d_and(rise_delay=100p fall_delay=100p)

Arst_inv rst_d rst_n inv_mod
.MODEL inv_mod d_inverter(rise_delay=100p fall_delay=100p)

* Digital-to-analog bridges for UP/DN
Aup_dac [up_d] [up] dac_brg
Adn_dac [dn_d] [dn] dac_brg
.MODEL dac_brg dac_bridge(out_low=0 out_high=1.8 out_undef=0.9)

**********************************************************************
* Charge pump (OSDI)
**********************************************************************
Ncp up dn cp_out kes_cp

**********************************************************************
* Loop filter (OSDI)
**********************************************************************
Nlf cp_out vctrl kes_lf

**********************************************************************
* VCO (OSDI)
**********************************************************************
Nvco vctrl clk_out clk_outb kes_vco

**********************************************************************
* Divider — div-by-64 using 6 XSPICE toggle flip-flops
**********************************************************************
Aclk_adc [clk_out] [clk_d] adc_brg

Ad1 d1 pull_hi
At1 tie_hi pull_hi

Aff0 d1 clk_d  tie_hi tie_hi q0 q0b tff_mod
Aff1 d1 q0     tie_hi tie_hi q1 q1b tff_mod
Aff2 d1 q1     tie_hi tie_hi q2 q2b tff_mod
Aff3 d1 q2     tie_hi tie_hi q3 q3b tff_mod
Aff4 d1 q3     tie_hi tie_hi q4 q4b tff_mod
Aff5 d1 q4     tie_hi tie_hi q5 q5b tff_mod

.MODEL tff_mod d_dff(clk_delay=50p set_delay=50p reset_delay=50p
+ ic=0 rise_delay=50p fall_delay=50p)

Afb_dac [q5] [fb_clk] dac_brg

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
wrdata /tmp/kestrel_test/kes_beh_clkout.csv V(clk_out)
wrdata /tmp/kestrel_test/kes_beh_refclk.csv V(ref_clk)
wrdata /tmp/kestrel_test/kes_beh_fbclk.csv V(fb_clk)

meas tran vctrl_final FIND V(vctrl_mon) AT=3.9u
meas tran t_lock WHEN V(vctrl_mon)=0.85 RISE=1
meas tran t1 WHEN V(clk_out)=0.9 RISE=LAST-1
meas tran t2 WHEN V(clk_out)=0.9 RISE=LAST
let f_vco = 1/(t2 - t1)
echo
echo "=== Behavioral PLL Results ==="
print vctrl_final t_lock f_vco
echo

quit
.ENDC

.END
