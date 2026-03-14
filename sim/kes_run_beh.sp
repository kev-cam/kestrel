* Kestrel PLL — launcher
* Uses circbyline so OSDI models are available before netlist parse

.control

* Load OSDI models first — registers device types
* OSDI paths — compile .va files with: openvaf kes_vco.va --output kes_vco.osdi
osdi kes_vco.osdi
osdi kes_cp.osdi
osdi kes_lf.osdi

* Build circuit line by line
circbyline * Kestrel PLL behavioral testbench
circbyline Vdd vdd 0 1.8
circbyline Vss vss 0 0
circbyline Vref ref_clk 0 PULSE(0 1.8 0 100p 100p 20n 40n)
*
* OSDI model instances (type registered by osdi command, instance by .MODEL)
circbyline .MODEL vco_mod kes_vco(kvco=1e9 vdd=1.8)
circbyline .MODEL cp_mod kes_cp(icp=100e-6 vdd=1.8 vth=0.9)
circbyline .MODEL lf_mod kes_lf(r1=20.153e3 c1=5.895e-12 c2=456e-15)
*
* PFD — ADC bridges (vector ports, brackets required)
circbyline Aref_adc [ref_clk] [ref_d] adc_brg
circbyline Afb_adc [fb_clk] [fb_d] adc_brg
circbyline .MODEL adc_brg adc_bridge(in_low=0.8 in_high=1.0)
*
* Pullups for digital tie-high (scalar ports, no brackets)
circbyline Ad_hi d_hi pull_hi
circbyline .MODEL pull_hi d_pullup(load=1p)
circbyline As_hi set_hi pull_hi
*
* PFD DFFs (all scalar ports)
circbyline Aup_ff d_hi ref_d set_hi rst_n up_d up_db dff_mod
circbyline Adn_ff d_hi fb_d set_hi rst_n dn_d dn_db dff_mod
circbyline .MODEL dff_mod d_dff(clk_delay=50p set_delay=50p reset_delay=50p ic=0 rise_delay=50p fall_delay=50p)
*
* Reset logic (d_and: input=vector, output=scalar; d_inverter: all scalar)
circbyline Arst_and [up_d dn_d] rst_d and_mod
circbyline .MODEL and_mod d_and(rise_delay=100p fall_delay=100p)
circbyline Arst_inv rst_d rst_n inv_mod
circbyline .MODEL inv_mod d_inverter(rise_delay=100p fall_delay=100p)
*
* DAC bridges for UP/DN (vector ports)
circbyline Aup_dac [up_d] [up] dac_brg
circbyline Adn_dac [dn_d] [dn] dac_brg
circbyline .MODEL dac_brg dac_bridge(out_low=0 out_high=1.8 out_undef=0.9)
*
* Charge pump (OSDI)
circbyline Ncp up dn cp_out cp_mod
*
* Loop filter (OSDI)
circbyline Nlf cp_out vctrl lf_mod
*
* VCO (OSDI)
circbyline Nvco vctrl clk_out clk_outb vco_mod
*
* Divider — div-by-64 (adc_bridge=vector, d_dff=scalar, dac_bridge=vector)
circbyline Aclk_adc [clk_out] [clk_d] adc_brg
circbyline Ad1 d1 pull_hi
circbyline At1 tie_hi pull_hi
circbyline Aff0 d1 clk_d tie_hi tie_hi q0 q0b tff_mod
circbyline Aff1 d1 q0 tie_hi tie_hi q1 q1b tff_mod
circbyline Aff2 d1 q1 tie_hi tie_hi q2 q2b tff_mod
circbyline Aff3 d1 q2 tie_hi tie_hi q3 q3b tff_mod
circbyline Aff4 d1 q3 tie_hi tie_hi q4 q4b tff_mod
circbyline Aff5 d1 q4 tie_hi tie_hi q5 q5b tff_mod
circbyline .MODEL tff_mod d_dff(clk_delay=50p set_delay=50p reset_delay=50p ic=0 rise_delay=50p fall_delay=50p)
circbyline Afb_dac [q5] [fb_clk] dac_brg
*
* Monitor
circbyline Rmon vctrl vctrl_mon 1k
*
circbyline .TRAN 0.5n 4u UIC
circbyline .END

* Run the simulation
run

* Save waveforms
wrdata kes_beh_vctrl.csv V(vctrl_mon)
wrdata kes_beh_clkout.csv V(clk_out)
wrdata kes_beh_refclk.csv V(ref_clk)
wrdata kes_beh_fbclk.csv V(fb_clk)

* Measurements
meas tran vctrl_final FIND V(vctrl_mon) AT=3.9u
meas tran t_lock WHEN V(vctrl_mon)=0.85 RISE=1
meas tran t1 WHEN V(clk_out)=0.9 RISE=LAST-1
meas tran t2 WHEN V(clk_out)=0.9 RISE=LAST
let f_vco = 1/(t2 - t1)
echo
echo === Behavioral PLL Results ===
print vctrl_final t_lock f_vco
echo

quit
.endc
