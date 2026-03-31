"""Tkinter GUI for Kestrel circuit generators."""

import argparse
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from .plugins import discover_generators


class KestrelGUI:
    """Dynamic generator GUI — fields come from the active plugin."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Kestrel Circuit Generator")
        self.root.resizable(True, True)

        self.generators = discover_generators()
        self.active_gen = None
        self.fields = {}
        self.combos = {}

        self._build_selector()
        self._build_spec_frame()
        self._build_button_frame()
        self._build_output_frame()

        # Select first generator
        if self.generators:
            first = next(iter(self.generators))
            self.gen_var.set(first)
            self._on_generator_changed()

    def _build_selector(self):
        frame = ttk.Frame(self.root, padding=5)
        frame.grid(row=0, column=0, padx=10, pady=5, sticky="ew")

        ttk.Label(frame, text="Generator:").pack(side="left", padx=(0, 5))
        self.gen_var = tk.StringVar()
        combo = ttk.Combobox(
            frame, textvariable=self.gen_var,
            values=list(self.generators.keys()),
            state="readonly", width=20,
        )
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_generator_changed())

        self.gen_desc = ttk.Label(frame, text="", foreground="gray")
        self.gen_desc.pack(side="left", padx=(10, 0))

    def _build_spec_frame(self):
        self.spec_frame = ttk.LabelFrame(self.root, text="Specification", padding=10)
        self.spec_frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        self.spec_frame.columnconfigure(1, weight=1)

    def _build_button_frame(self):
        frame = ttk.Frame(self.root, padding=5)
        frame.grid(row=2, column=0, padx=10, pady=5, sticky="ew")
        ttk.Button(frame, text="Generate", command=self._on_generate).pack(side="left", padx=5)

    def _build_output_frame(self):
        frame = ttk.LabelFrame(self.root, text="Output", padding=10)
        frame.grid(row=3, column=0, padx=10, pady=5, sticky="nsew")
        self.root.rowconfigure(3, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.output_text = scrolledtext.ScrolledText(
            frame, width=70, height=20, font=("Courier", 10),
        )
        self.output_text.pack(fill="both", expand=True)

    def _on_generator_changed(self):
        name = self.gen_var.get()
        if name not in self.generators:
            return
        reg = self.generators[name]
        self.active_gen = reg
        self.gen_desc.config(text=reg.get("description", ""))

        # Clear old fields
        for w in self.spec_frame.winfo_children():
            w.destroy()
        self.fields.clear()
        self.combos.clear()

        # Build fields from plugin
        gui_fields_fn = reg.get("gui_fields")
        if not gui_fields_fn:
            ttk.Label(self.spec_frame, text="(no GUI fields defined)").grid(row=0, column=0)
            return

        field_defs = gui_fields_fn()
        row = 0

        for entry in field_defs.get("entries", []):
            ttk.Label(self.spec_frame, text=entry["label"]).grid(row=row, column=0, sticky="e", padx=(0, 5))
            var = tk.StringVar(value=entry.get("default", ""))
            ttk.Entry(self.spec_frame, textvariable=var, width=14).grid(row=row, column=1, sticky="w")
            hint = entry.get("hint", "")
            if hint:
                ttk.Label(self.spec_frame, text=hint, foreground="gray").grid(row=row, column=2, sticky="w", padx=(5, 0))
            self.fields[entry["key"]] = var
            row += 1

        for combo_def in field_defs.get("combos", []):
            ttk.Label(self.spec_frame, text=combo_def["label"]).grid(row=row, column=0, sticky="e", padx=(0, 5))
            var = tk.StringVar(value=combo_def.get("default", ""))
            ttk.Combobox(
                self.spec_frame, textvariable=var,
                values=combo_def.get("choices", []),
                state="readonly", width=12,
            ).grid(row=row, column=1, sticky="w")
            self.combos[combo_def["key"]] = var
            row += 1

        for extra in field_defs.get("extras", []):
            ttk.Label(self.spec_frame, text=extra["label"]).grid(row=row, column=0, sticky="e", padx=(0, 5))
            var = tk.StringVar(value=extra.get("default", ""))
            ttk.Entry(self.spec_frame, textvariable=var, width=14).grid(row=row, column=1, sticky="w")
            hint = extra.get("hint", "")
            if hint:
                ttk.Label(self.spec_frame, text=hint, foreground="gray").grid(row=row, column=2, sticky="w", padx=(5, 0))
            self.fields[extra["key"]] = var
            row += 1

    def _on_generate(self):
        if not self.active_gen:
            messagebox.showwarning("No generator", "Select a generator first.")
            return

        out_dir = filedialog.askdirectory(title="Select output directory")
        if not out_dir:
            return

        # Build a synthetic args namespace from fields
        args = argparse.Namespace()
        for key, var in self.fields.items():
            setattr(args, key.replace("-", "_"), var.get())
        for key, var in self.combos.items():
            setattr(args, key.replace("-", "_"), var.get())
        args.output = out_dir

        self.output_text.delete("1.0", "end")

        # Capture print output
        import io
        import contextlib
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.active_gen["run"](args)
            self.output_text.insert("1.0", buf.getvalue())
        except Exception as e:
            self.output_text.insert("1.0", f"Error: {e}\n")
            messagebox.showerror("Generation error", str(e))

    def run(self):
        self.root.mainloop()


def main():
    gui = KestrelGUI()
    gui.run()


if __name__ == "__main__":
    main()
