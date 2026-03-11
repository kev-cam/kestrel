"""Tkinter GUI for collecting PLL specifications and generating outputs."""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from .design.engine import PLLSpec, design_pll, summarize
from .models.verilog_ams import emit_verilog_ams
from .spec import parse_freq, parse_time


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class KestrelGUI:
    """PLL specification entry and generation GUI."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Kestrel PLL Generator")
        self.root.resizable(True, True)

        self._build_spec_frame()
        self._build_button_frame()
        self._build_output_frame()

        self.design = None

    # ----- layout -----

    def _build_spec_frame(self):
        frame = ttk.LabelFrame(self.root, text="PLL Specification", padding=10)
        frame.grid(row=0, column=0, padx=10, pady=5, sticky="ew")
        frame.columnconfigure(1, weight=1)

        entries = [
            ("Freq min:",       "freq_min",  "800M",    "Hz (e.g. 800M, 1G, 500k)"),
            ("Freq max:",       "freq_max",  "1.2G",    "Hz"),
            ("Reference freq:", "ref_freq",  "25M",     "Hz"),
            ("Loop bandwidth:", "loop_bw",   "5M",      "Hz"),
            ("Phase margin:",   "pm",        "60",      "degrees"),
            ("Jitter target:",  "jitter",    "5p",      "s rms (blank = unconstrained)"),
            ("Supply voltage:", "vdd",       "1.8",     "V"),
        ]

        self.fields = {}
        for row, (label, key, default, hint) in enumerate(entries):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="e", padx=(0, 5))
            var = tk.StringVar(value=default)
            ent = ttk.Entry(frame, textvariable=var, width=14)
            ent.grid(row=row, column=1, sticky="w")
            ttk.Label(frame, text=hint, foreground="gray").grid(row=row, column=2, sticky="w", padx=(5, 0))
            self.fields[key] = var

        # VCO type
        row = len(entries)
        ttk.Label(frame, text="VCO type:").grid(row=row, column=0, sticky="e", padx=(0, 5))
        self.vco_type = tk.StringVar(value="ring")
        combo = ttk.Combobox(frame, textvariable=self.vco_type,
                             values=["ring", "lc"], state="readonly", width=12)
        combo.grid(row=row, column=1, sticky="w")

        # VCO stages
        row += 1
        ttk.Label(frame, text="VCO stages:").grid(row=row, column=0, sticky="e", padx=(0, 5))
        self.vco_stages = tk.StringVar(value="4")
        ttk.Entry(frame, textvariable=self.vco_stages, width=14).grid(row=row, column=1, sticky="w")
        ttk.Label(frame, text="(ring only)", foreground="gray").grid(row=row, column=2, sticky="w", padx=(5, 0))

        # Process
        row += 1
        ttk.Label(frame, text="Process:").grid(row=row, column=0, sticky="e", padx=(0, 5))
        self.process = tk.StringVar(value="sky130")
        combo2 = ttk.Combobox(frame, textvariable=self.process,
                              values=["sky130", "gf180"], state="readonly", width=12)
        combo2.grid(row=row, column=1, sticky="w")

    def _build_button_frame(self):
        frame = ttk.Frame(self.root, padding=5)
        frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")

        ttk.Button(frame, text="Design", command=self._on_design).pack(side="left", padx=5)
        ttk.Button(frame, text="Generate Verilog-AMS", command=self._on_generate).pack(side="left", padx=5)
        ttk.Button(frame, text="Save Summary...", command=self._on_save_summary).pack(side="left", padx=5)

    def _build_output_frame(self):
        frame = ttk.LabelFrame(self.root, text="Design Summary", padding=10)
        frame.grid(row=2, column=0, padx=10, pady=5, sticky="nsew")
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.output_text = scrolledtext.ScrolledText(frame, width=60, height=20,
                                                      font=("Courier", 10))
        self.output_text.pack(fill="both", expand=True)

    # ----- actions -----

    def _read_spec(self) -> PLLSpec:
        """Parse GUI fields into a PLLSpec."""
        jitter_text = self.fields["jitter"].get().strip()
        jitter = parse_time(jitter_text) if jitter_text else None

        return PLLSpec(
            freq_min=parse_freq(self.fields["freq_min"].get()),
            freq_max=parse_freq(self.fields["freq_max"].get()),
            ref_freq=parse_freq(self.fields["ref_freq"].get()),
            loop_bw=parse_freq(self.fields["loop_bw"].get()),
            phase_margin=float(self.fields["pm"].get()),
            jitter_target=jitter,
            vco_type=self.vco_type.get(),
            vco_stages=int(self.vco_stages.get()),
            supply_voltage=float(self.fields["vdd"].get()),
            process=self.process.get(),
        )

    def _on_design(self):
        try:
            spec = self._read_spec()
        except (ValueError, KeyError) as e:
            messagebox.showerror("Invalid spec", str(e))
            return

        self.design = design_pll(spec)
        summary = summarize(self.design)

        self.output_text.delete("1.0", "end")
        self.output_text.insert("1.0", summary)

    def _on_generate(self):
        if not self.design:
            self._on_design()
        if not self.design:
            return

        out_dir = filedialog.askdirectory(title="Select output directory")
        if not out_dir:
            return

        try:
            files = emit_verilog_ams(self.design, out_dir)
            msg = "Generated files:\n" + "\n".join(f"  {os.path.basename(f)}" for f in files)
            self.output_text.insert("end", f"\n\n{msg}\n")
            messagebox.showinfo("Success", msg)
        except Exception as e:
            messagebox.showerror("Generation error", str(e))

    def _on_save_summary(self):
        if not self.design:
            messagebox.showwarning("No design", "Run Design first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save summary",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            with open(path, "w") as f:
                f.write(self.output_text.get("1.0", "end"))

    def run(self):
        self.root.mainloop()


def main():
    gui = KestrelGUI()
    gui.run()


if __name__ == "__main__":
    main()
