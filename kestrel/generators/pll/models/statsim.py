"""stat-sim emitter: the PLL as an event-driven nvc model with timing variability.

The Verilog-AMS models (verilog_ams.py) are the authored form.  This writes
the nvc runtime form the stat-sim flow uses (stat-sim/nvcgen.py style): VHDL
entities on stat-sim's `prob_load` discipline (library statsim,
lib/statsim_disc.vhd), event-driven throughout.  The analog charge pump and
loop filter are replaced by their exact event-driven equivalent: the R1-C1 /
C2 filter driven by a piecewise-constant pump current has a closed-form
response between pump events, so the control voltage is evaluated exactly
at every VCO stage edge without an analog solver.

Timing variability (US8478576B1 style, per-event random draws):
  SIGMA_MM   VCO per-stage static mismatch (fraction of the stage delay), one
             draw per stage per instance from SEED
  SIGMA_J    per-edge jitter on every VCO stage delay and flop clock-to-Q
             (fraction), a fresh draw at every event
  SIGMA_ICP  charge-pump UP/DN current mismatch (fraction), static
  SIGMA_REF  reference edge jitter (s rms), testbench
Run run_statsim.sh with --mc N for a Monte-Carlo sample over seeds; the
per-seed summary lines are the probability waveform of lock time, output
frequency, period jitter and static phase offset.
"""

import os
from kestrel.process import format_eng, get_process_params
from ..engine import PLLDesign
from .verilog_ams import reference_release_delay, simulation_time


def _r(x: float) -> str:
    """A VHDL real literal (needs a '.' or an exponent)."""
    s = f"{x:.6e}"
    return s


def emit_statsim(design: PLLDesign, output_dir: str) -> list:
    """Write kestrel_pll_statsim.vhd, kestrel_pll_statsim_tb.vhd and run_statsim.sh."""
    os.makedirs(output_dir, exist_ok=True)
    files = [_write_model(design, output_dir),
             _write_tb(design, output_dir),
             _write_runner(design, output_dir)]
    return files


def _banner(d: PLLDesign, what: str) -> str:
    s = d.spec
    return f"""\
-- {what} — Kestrel PLL Generator v0.1.0 (stat-sim / nvc form)
--
-- Target: {format_eng(s.freq_min, 'Hz')} — {format_eng(s.freq_max, 'Hz')}   Ref: {format_eng(s.ref_freq, 'Hz')}   Process: {s.process}
-- VCO:    {format_eng(d.f_center, 'Hz')} at Vctrl = {d.vctrl_nom:.2f} V, Kvco = {format_eng(d.kvco * d.kvco_sign, 'Hz/V')}, {s.vco_stages} stages
-- Pump:   Icp = {format_eng(d.icp, 'A')}   Filter: R1 = {format_eng(d.r_filter, 'ohm')}, C1 = {format_eng(d.c1, 'F')}, C2 = {format_eng(d.c2, 'F')}   N = {d.n_nom}
--
-- Auto-generated — do not hand-edit.  Authored form: kestrel_*.vams.
"""


def _write_model(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_pll_statsim.vhd")
    s = d.spec
    td_nom = 1.0 / (2.0 * s.vco_stages * d.f_center)
    kvco_signed = d.kvco * d.kvco_sign

    with open(path, "w") as f:
        f.write(_banner(d, "kestrel_pll_statsim.vhd"))
        f.write(f"""\
--
-- Event-driven PLL on stat-sim's prob_load discipline (library statsim):
--   kestrel_vco_pl      the travelling edge of the ring; each stage delay is
--                       computed from the control voltage at that instant
--   kestrel_pfd_pl      two flops set by the clock edges, cleared through an
--                       AND after TD_RST
--   kestrel_divider_pl  /N counter on the VCO's rising edges
--   kestrel_cp_lf_pl    charge pump + R1-C1/C2 filter, exact closed form
--                       between pump events (no analog solver)
--   kestrel_pll_statsim the loop
-- Logic nets are resolved_pl, so stat-sim's metastable flops, wire/load taps
-- and CDC traps can be attached (e.g. replace the PFD flops, or trap UP/DN).

library ieee;
use ieee.math_real.all;
library statsim;
use statsim.statsim_disc_pkg.all;

package kestrel_pll_statsim_pkg is
    -- Loop-filter state: v1 = voltage across C1, x = drop across R1 (so the
    -- control node is v1 + x), xs = steady-state x for the present pump
    -- current, t0 = time of the last update (s).  tau = R1*C1*C2/(C1+C2).
    type lf_state is record
        v1, x, xs, t0 : real;
    end record;
    constant LF_TAU  : real := {_r(d.r_filter * d.c1 * d.c2 / (d.c1 + d.c2))};
    constant LF_R1C1 : real := {_r(d.r_filter * d.c1)};

    impure function now_s return real;                   -- simulation time in seconds
    function lf_value (st : lf_state; t : real) return real;   -- control voltage at time t
    function lf_advance (st : lf_state; t : real) return lf_state;
    procedure gauss (s1, s2 : inout integer; g : out real);   -- N(0,1), Box-Muller on uniform()
    function sec (t : real) return time;                 -- real seconds -> time (1 fs floor)
    function is_hi (pl : prob_load) return boolean;
end package;

package body kestrel_pll_statsim_pkg is
    impure function now_s return real is
    begin
        return real(now / 1 fs) * 1.0e-15;
    end function;

    -- x(t)  = xs + (x0 - xs) e^(-dt/tau)
    -- v1(t) = v1_0 + [xs dt + (x0 - xs) tau (1 - e^(-dt/tau))] / (R1 C1)
    function lf_advance (st : lf_state; t : real) return lf_state is
        variable dt, e : real;
        variable r     : lf_state;
    begin
        dt   := t - st.t0;
        e    := exp(-dt / LF_TAU);
        r.v1 := st.v1 + (st.xs * dt + (st.x - st.xs) * LF_TAU * (1.0 - e)) / LF_R1C1;
        r.x  := st.xs + (st.x - st.xs) * e;
        r.xs := st.xs;
        r.t0 := t;
        return r;
    end function;

    function lf_value (st : lf_state; t : real) return real is
        variable r : lf_state;
    begin
        r := lf_advance(st, t);
        return r.v1 + r.x;
    end function;

    procedure gauss (s1, s2 : inout integer; g : out real) is
        variable u1, u2 : real;
    begin
        uniform(s1, s2, u1);
        uniform(s1, s2, u2);
        if u1 < 1.0e-12 then u1 := 1.0e-12; end if;
        g := sqrt(-2.0 * log(u1)) * cos(2.0 * MATH_PI * u2);
    end procedure;

    function sec (t : real) return time is
    begin
        if t < 1.0e-15 then
            return 1 fs;
        end if;
        return 1 fs * (t * 1.0e15);   -- time * real: no 32-bit integer on the way
    end function;

    function is_hi (pl : prob_load) return boolean is
    begin
        return pl.p1 > 0.5;
    end function;
end package body;


-- ===========================================================================
-- VCO: the edge travelling round N_STAGES delay cells
-- ===========================================================================
library ieee;
use ieee.math_real.all;
library statsim;
use statsim.statsim_disc_pkg.all;
use work.kestrel_pll_statsim_pkg.all;

entity kestrel_vco_pl is
    generic ( TD_NOM    : real := {_r(td_nom)};   -- s, stage delay at VCTRL_NOM = 1/(2 N f_center)
              S_TD      : real := {_r(kvco_signed / d.f_center)};   -- 1/V, Kvco/f_center
              VCTRL_NOM : real := {d.vctrl_nom:.4f};
              N_STAGES  : integer := {s.vco_stages};
              X_MIN     : real := 0.05;             -- slowest the ring may run (fraction of f_center)
              SIGMA_MM  : real := 0.0;              -- per-stage mismatch, fraction (static)
              SIGMA_J   : real := 0.0;              -- per-edge jitter, fraction
              SEED      : integer := 1 );
    port ( lf      : in    lf_state;                -- loop-filter state: V(vctrl) = lf_value(lf, now)
           vctrl   : out   real := 0.0;             -- the control voltage as sampled at each stage edge
           clk_out : inout resolved_pl := PL_FLOAT );
end entity;

architecture evt of kestrel_vco_pl is
begin
    ring : process
        type real_vec is array (0 to N_STAGES - 1) of real;
        variable mm      : real_vec := (others => 0.0);
        variable s1, s2  : integer := SEED;
        variable g, x, td, v : real;
        variable stage   : integer := 0;
        variable hi      : boolean := false;
    begin
        if SIGMA_MM > 0.0 then
            for k in mm'range loop
                gauss(s1, s2, g);
                mm(k) := SIGMA_MM * g;
            end loop;
        end if;
        clk_out <= PL_0;
        loop
            -- delay of the stage being entered, from the control voltage now
            v  := lf_value(lf, now_s);
            vctrl <= v;
            x  := 1.0 + S_TD * (v - VCTRL_NOM);
            if x < X_MIN then x := X_MIN; end if;
            td := TD_NOM / x * (1.0 + mm(stage));
            if SIGMA_J > 0.0 then
                gauss(s1, s2, g);
                td := td * (1.0 + SIGMA_J * g);
            end if;
            wait for sec(td);
            stage := stage + 1;
            if stage >= N_STAGES then
                stage := 0;
                hi := not hi;
                if hi then clk_out <= PL_1; else clk_out <= PL_0; end if;
            end if;
        end loop;
    end process;
end architecture;


-- ===========================================================================
-- PFD: UP on the reference edge, DN on the feedback edge, both cleared
-- TD_RST after both are set (the pump's pulse in lock: no dead zone)
-- ===========================================================================
library ieee;
use ieee.math_real.all;
library statsim;
use statsim.statsim_disc_pkg.all;
use work.kestrel_pll_statsim_pkg.all;

entity kestrel_pfd_pl is
    generic ( TD_RST  : real := 200.0e-12;         -- s, reset path (AND + flop clear)
              TD_CQ   : real := 50.0e-12;          -- s, flop clock-to-Q
              SIGMA_J : real := 0.0;               -- per-edge jitter on TD_CQ, fraction
              SEED    : integer := 1 );
    port ( ref_clk, fb_clk : in    resolved_pl;
           up, dn          : inout resolved_pl := PL_FLOAT );
end entity;

architecture evt of kestrel_pfd_pl is
    signal rst : boolean := false;
begin
    -- reset: AND of UP and DN, TD_RST later
    rst <= (is_hi(up) and is_hi(dn)) after sec(TD_RST);

    up_ff : process (ref_clk, rst)
        variable prev   : boolean := false;
        variable hi     : boolean;
        variable s1, s2 : integer := SEED;
        variable g, tq  : real;
    begin
        hi := is_hi(ref_clk);
        if rst then
            up <= PL_0;
        elsif hi and not prev then
            tq := TD_CQ;
            if SIGMA_J > 0.0 then gauss(s1, s2, g); tq := tq * (1.0 + SIGMA_J * g); end if;
            up <= PL_1 after sec(tq);
        end if;
        prev := hi;
    end process;

    dn_ff : process (fb_clk, rst)
        variable prev   : boolean := false;
        variable hi     : boolean;
        variable s1, s2 : integer := SEED + 7;
        variable g, tq  : real;
    begin
        hi := is_hi(fb_clk);
        if rst then
            dn <= PL_0;
        elsif hi and not prev then
            tq := TD_CQ;
            if SIGMA_J > 0.0 then gauss(s1, s2, g); tq := tq * (1.0 + SIGMA_J * g); end if;
            dn <= PL_1 after sec(tq);
        end if;
        prev := hi;
    end process;
end architecture;


-- ===========================================================================
-- Divider: /N counter on the rising edges of clk_in
-- ===========================================================================
library ieee;
use ieee.math_real.all;
library statsim;
use statsim.statsim_disc_pkg.all;
use work.kestrel_pll_statsim_pkg.all;

entity kestrel_divider_pl is
    generic ( N_DIV   : integer := {d.n_nom};
              TD_CQ   : real := 50.0e-12;
              SIGMA_J : real := 0.0;
              SEED    : integer := 1 );
    port ( clk_in  : in    resolved_pl;
           clk_out : inout resolved_pl := PL_FLOAT );
end entity;

architecture evt of kestrel_divider_pl is
begin
    cnt : process (clk_in)
        variable prev   : boolean := false;
        variable hi     : boolean;
        variable count  : integer := 0;
        variable s1, s2 : integer := SEED;
        variable g, tq  : real;
    begin
        hi := is_hi(clk_in);
        if hi and not prev then
            tq := TD_CQ;
            if SIGMA_J > 0.0 then gauss(s1, s2, g); tq := tq * (1.0 + SIGMA_J * g); end if;
            if N_DIV <= 1 then
                clk_out <= PL_1 after sec(tq);
            else
                count := count + 1;
                if count >= N_DIV then
                    count := 0;
                    clk_out <= PL_1 after sec(tq);
                elsif count = N_DIV / 2 then
                    clk_out <= PL_0 after sec(tq);
                end if;
            end if;
        elsif not hi and prev and N_DIV <= 1 then
            clk_out <= PL_0 after sec(TD_CQ);
        end if;
        prev := hi;
    end process;
end architecture;


-- ===========================================================================
-- Charge pump + loop filter, exact event-driven form
-- ===========================================================================
library ieee;
use ieee.math_real.all;
library statsim;
use statsim.statsim_disc_pkg.all;
use work.kestrel_pll_statsim_pkg.all;

entity kestrel_cp_lf_pl is
    generic ( ICP       : real := {_r(d.icp)};       -- A
              R1        : real := {_r(d.r_filter)};
              C1        : real := {_r(d.c1)};
              C2        : real := {_r(d.c2)};
              VDD       : real := {s.supply_voltage:.3f};
              V_INIT    : real := {d.vctrl_start:.4f};              -- V, start point of the filter
              SIGN      : integer := {d.kvco_sign};               -- -1: UP sinks (negative Kvco)
              SIGMA_ICP : real := 0.0;              -- UP/DN current mismatch, fraction (static)
              SEED      : integer := 1 );
    port ( up, dn : in  resolved_pl;
           lf     : out lf_state := (0.0, 0.0, 0.0, 0.0) );
end entity;

architecture evt of kestrel_cp_lf_pl is
begin
    pump : process (up, dn)
        variable st     : lf_state := (V_INIT, 0.0, 0.0, 0.0);
        variable first  : boolean := true;
        variable i_up, i_dn, i, g : real;
        variable s1, s2 : integer := SEED;
    begin
        if first then
            i_up := ICP; i_dn := ICP;
            if SIGMA_ICP > 0.0 then
                gauss(s1, s2, g); i_up := ICP * (1.0 + SIGMA_ICP * g);
                gauss(s1, s2, g); i_dn := ICP * (1.0 + SIGMA_ICP * g);
            end if;
            first := false;
        end if;
        -- bring the filter state to now with the current that was flowing
        st := lf_advance(st, now_s);
        -- rails: the current sources drop out at the supply and ground
        if st.v1 + st.x > VDD then
            if st.v1 > VDD then st.v1 := VDD; end if;
            st.x := VDD - st.v1;
        elsif st.v1 + st.x < 0.0 then
            if st.v1 < 0.0 then st.v1 := 0.0; end if;
            st.x := -st.v1;
        end if;
        -- new pump current: source while UP, sink while DN (swapped for SIGN = -1)
        i := 0.0;
        if is_hi(up) then i := i + i_up; end if;
        if is_hi(dn) then i := i - i_dn; end if;
        i := real(SIGN) * i;
        st.xs := i * R1 * C1 / (C1 + C2);
        lf <= st;
    end process;
end architecture;


-- ===========================================================================
-- The loop
-- ===========================================================================
library ieee;
use ieee.math_real.all;
library statsim;
use statsim.statsim_disc_pkg.all;
use work.kestrel_pll_statsim_pkg.all;

entity kestrel_pll_statsim is
    generic ( SEED      : integer := 1;
              SIGMA_MM  : real := 0.0;
              SIGMA_J   : real := 0.0;
              SIGMA_ICP : real := 0.0 );
    port ( ref_clk : in    resolved_pl;
           clk_out : inout resolved_pl := PL_FLOAT;
           fb_clk  : inout resolved_pl := PL_FLOAT;
           up, dn  : inout resolved_pl := PL_FLOAT;
           vctrl   : out   real := 0.0;
           lf      : out   lf_state := (0.0, 0.0, 0.0, 0.0) );
end entity;

architecture evt of kestrel_pll_statsim is
    signal lf_i : lf_state := ({d.vctrl_start:.4f}, 0.0, 0.0, 0.0);
begin
    pfd : entity work.kestrel_pfd_pl
        generic map (SIGMA_J => SIGMA_J, SEED => SEED + 1)
        port map (ref_clk => ref_clk, fb_clk => fb_clk, up => up, dn => dn);
    cp  : entity work.kestrel_cp_lf_pl
        generic map (SIGMA_ICP => SIGMA_ICP, SEED => SEED + 3)
        port map (up => up, dn => dn, lf => lf_i);
    vco : entity work.kestrel_vco_pl
        generic map (SIGMA_MM => SIGMA_MM, SIGMA_J => SIGMA_J, SEED => SEED)
        port map (lf => lf_i, vctrl => vctrl, clk_out => clk_out);
    div : entity work.kestrel_divider_pl
        generic map (SIGMA_J => SIGMA_J, SEED => SEED + 2)
        port map (clk_in => clk_out, clk_out => fb_clk);
    lf <= lf_i;
end architecture;
""")
    return path


def _write_tb(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "kestrel_pll_statsim_tb.vhd")
    s = d.spec
    ref_period = 1.0 / s.ref_freq
    ref_delay = reference_release_delay(d)
    t_stop = simulation_time(d)

    with open(path, "w") as f:
        f.write(_banner(d, "kestrel_pll_statsim_tb.vhd"))
        f.write(f"""\
--
-- Closed loop with measurements.  The reference is released at {format_eng(ref_delay, 's')}
-- (after the divider's first edges).  Per seed it reports one line:
--   KESTREL_STATSIM seed=.. f_MHz=.. t_lock_us=.. jit_ps=.. pe_rms_ps=.. pe_max_ps=.. vctrl=..
--   f_MHz     VCO frequency over the last quarter of the run (mean period)
--   t_lock_us start of the first LOCK_CYCLES consecutive cycles with |ref->fb| < PE_LOCK
--   jit_ps    rms period jitter of clk_out over the last quarter
--   pe_rms/max  ref->fb phase error over the last quarter (static offset + jitter)
-- run_statsim.sh --mc N collects these over seeds 1..N.

library ieee;
use ieee.math_real.all;
library statsim;
use statsim.statsim_disc_pkg.all;
use work.kestrel_pll_statsim_pkg.all;

entity kestrel_pll_statsim_tb is
    generic ( SEED        : integer := 1;
              SIGMA_MM    : real := 0.0;
              SIGMA_J     : real := 0.0;
              SIGMA_ICP   : real := 0.0;
              SIGMA_REF   : real := 0.0;            -- s rms, reference edge jitter
              T_STOP      : real := {_r(t_stop)};   -- s
              PE_LOCK     : real := {_r(0.01 * ref_period)};   -- s, |ref->fb| for "locked" (1 % of the reference period)
              LOCK_CYCLES : integer := 20 );
end entity;

architecture tb of kestrel_pll_statsim_tb is
    constant T_REF     : real := {_r(ref_period)};
    constant REF_DELAY : real := {_r(ref_delay)};
    constant N_DIV     : integer := {d.n_nom};
    signal ref_clk : resolved_pl := PL_0;
    signal clk_out, fb_clk, up, dn : resolved_pl := PL_FLOAT;
    signal vctrl   : real := 0.0;
    signal lf      : lf_state;
    signal done    : boolean := false;
begin
    dut : entity work.kestrel_pll_statsim
        generic map (SEED => SEED, SIGMA_MM => SIGMA_MM, SIGMA_J => SIGMA_J, SIGMA_ICP => SIGMA_ICP)
        port map (ref_clk => ref_clk, clk_out => clk_out, fb_clk => fb_clk,
                  up => up, dn => dn, vctrl => vctrl, lf => lf);

    -- reference: released at REF_DELAY, optional Gaussian edge jitter
    refgen : process
        variable s1, s2 : integer := SEED + 11;
        variable g, th  : real;
        variable hi     : boolean := false;
    begin
        ref_clk <= PL_0;
        wait for sec(REF_DELAY);
        loop
            th := T_REF / 2.0;
            if SIGMA_REF > 0.0 then gauss(s1, s2, g); th := th + SIGMA_REF * g; end if;
            wait for sec(th);
            hi := not hi;
            if hi then ref_clk <= PL_1; else ref_clk <= PL_0; end if;
            exit when done;
        end loop;
        wait;
    end process;

    meas : process
        variable prev_ref, prev_fb, prev_clk : boolean := false;
        variable t_ref_edge, t_now, pe       : real := 0.0;
        variable t_last_clk, per             : real := 0.0;
        variable n_lock, n_per, n_pe         : integer := 0;
        variable t_lock, t_cand              : real := -1.0;
        variable sum_per, sum_per2           : real := 0.0;
        variable sum_pe2, pe_max             : real := 0.0;
        variable f_mhz, jit, pe_rms          : real := 0.0;
        variable have_clk                    : boolean := false;
    begin
        loop
            wait on ref_clk, fb_clk, clk_out for sec(T_STOP) - now;
            t_now := now_s;
            if t_now >= T_STOP then exit; end if;
            if is_hi(ref_clk) and not prev_ref then
                t_ref_edge := t_now;
            end if;
            if is_hi(fb_clk) and not prev_fb and t_ref_edge > 0.0 then
                -- signed phase error against the nearest reference edge
                pe := t_now - t_ref_edge;
                if pe > T_REF / 2.0 then pe := pe - T_REF; end if;
                if abs(pe) < PE_LOCK then
                    if n_lock = 0 then t_cand := t_now; end if;
                    n_lock := n_lock + 1;
                    if n_lock = LOCK_CYCLES and t_lock < 0.0 then t_lock := t_cand; end if;
                else
                    n_lock := 0;
                end if;
                if t_now > 0.75 * T_STOP then
                    n_pe := n_pe + 1;
                    sum_pe2 := sum_pe2 + pe * pe;
                    if abs(pe) > pe_max then pe_max := abs(pe); end if;
                end if;
            end if;
            if is_hi(clk_out) and not prev_clk then
                if have_clk and t_now > 0.75 * T_STOP then
                    per := t_now - t_last_clk;
                    n_per := n_per + 1;
                    sum_per := sum_per + per;
                    sum_per2 := sum_per2 + per * per;
                end if;
                t_last_clk := t_now;
                have_clk := true;
            end if;
            prev_ref := is_hi(ref_clk);
            prev_fb  := is_hi(fb_clk);
            prev_clk := is_hi(clk_out);
        end loop;
        if n_per > 1 then
            f_mhz := 1.0e-6 / (sum_per / real(n_per));
            jit := sqrt(abs(sum_per2 / real(n_per) - (sum_per / real(n_per)) ** 2)) * 1.0e12;
        end if;
        if n_pe > 0 then pe_rms := sqrt(sum_pe2 / real(n_pe)) * 1.0e12; end if;
        report "KESTREL_STATSIM seed=" & integer'image(SEED)
             & " f_MHz=" & real'image(f_mhz)
             & " t_lock_us=" & real'image(t_lock * 1.0e6)
             & " jit_ps=" & real'image(jit)
             & " pe_rms_ps=" & real'image(pe_rms)
             & " pe_max_ps=" & real'image(pe_max * 1.0e12)
             & " vctrl=" & real'image(lf_value(lf, now_s))
             & " target_MHz=" & real'image(real(N_DIV) * {_r(s.ref_freq)} * 1.0e-6);
        done <= true;
        wait;
    end process;
end architecture;
""")
    return path


def _write_runner(d: PLLDesign, out: str) -> str:
    path = os.path.join(out, "run_statsim.sh")
    t_stop = simulation_time(d)
    with open(path, "w") as f:
        f.write(f"""\
#!/bin/bash
# Run the stat-sim form of the Kestrel PLL on nvc.
#
#   bash run_statsim.sh                      one deterministic run (seed 1)
#   bash run_statsim.sh --mc 20              20 seeds, summary table
#   SIGMA_J=0.02 SIGMA_MM=0.05 bash run_statsim.sh --mc 20
#
# Environment: NVC (nvc binary), NVC_LIB (its std/ieee library directory, when
# the binary does not find it by itself), STATSIM_DIR (stat-sim checkout with
# lib/statsim_disc.vhd; github.com/kev-cam/stat-sim), NVC_STD (default 2008),
# SIGMA_MM SIGMA_J SIGMA_ICP SIGMA_REF (variability, see the generics).
set -e
cd "$(dirname "$0")"
NVC=${{NVC:-nvc}}
if [ -n "$NVC_LIB" ]; then NVC="$NVC -L $NVC_LIB"; fi
STATSIM_DIR=${{STATSIM_DIR:-/usr/local/src/stat-sim}}
STD=${{NVC_STD:-2008}}
MC=1
if [ "$1" = "--mc" ]; then MC=${{2:-10}}; fi
GEN="-gSIGMA_MM=${{SIGMA_MM:-0.0}} -gSIGMA_J=${{SIGMA_J:-0.0}} -gSIGMA_ICP=${{SIGMA_ICP:-0.0}} -gSIGMA_REF=${{SIGMA_REF:-0.0}}"
if [ ! -f "$STATSIM_DIR/lib/statsim_disc.vhd" ]; then
  echo "stat-sim discipline not found: set STATSIM_DIR to a stat-sim checkout" >&2; exit 2
fi
mkdir -p build
$NVC --std=$STD --work=statsim:build/statsim -a "$STATSIM_DIR/lib/statsim_disc.vhd"
$NVC --std=$STD --work=kestrel:build/kestrel -L build -a kestrel_pll_statsim.vhd kestrel_pll_statsim_tb.vhd
$NVC --std=$STD --work=kestrel:build/kestrel -L build -e kestrel_pll_statsim_tb $GEN -gSEED=1 >/dev/null
rm -f statsim_mc.log
for seed in $(seq 1 $MC); do
  $NVC --std=$STD --work=kestrel:build/kestrel -L build -e kestrel_pll_statsim_tb $GEN -gSEED=$seed \\
       -r --stop-time={t_stop * 1e9 + 1:.0f}ns 2>&1 | grep -o 'KESTREL_STATSIM.*' | tee -a statsim_mc.log
done
if [ "$MC" -gt 1 ]; then
  echo
  echo "Monte-Carlo over $MC seeds (mean +/- sd):"
  awk '{{ for (i = 2; i <= NF; i++) {{ split($i, kv, "="); k = kv[1]; v = kv[2] + 0;
           if (k == "seed" || k == "target_MHz") continue;
           if (k == "t_lock_us" && v < 0) {{ unlocked++; continue }}
           n[k]++; s[k] += v; s2[k] += v * v; if (!(k in order)) {{ order[k] = ++no; names[no] = k }} }} }}
       END {{ for (j = 1; j <= no; j++) {{ k = names[j]; m = s[k] / n[k]; var = s2[k] / n[k] - m * m;
              if (var < 0) var = 0; printf "  %-12s %12.5g +/- %-10.4g (n=%d)\\n", k, m, sqrt(var), n[k] }}
              if (unlocked > 0) printf "  %d seed(s) never met the lock criterion\\n", unlocked }}' statsim_mc.log
fi
""")
    os.chmod(path, 0o755)
    return path
