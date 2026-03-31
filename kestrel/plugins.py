"""Plugin discovery for Kestrel generators."""

import importlib
import os
import sys


def discover_generators() -> dict:
    """Return {name: registration_dict} for all generators.

    Discovery order:
    1. Built-in generators under kestrel.generators.*.kestrel_plugin
    2. External plugins from KESTREL_PLUGINS env var (colon-separated paths)
    """
    generators = {}

    # --- Built-in generators ---
    gen_dir = os.path.join(os.path.dirname(__file__), 'generators')
    if os.path.isdir(gen_dir):
        for name in sorted(os.listdir(gen_dir)):
            pkg_dir = os.path.join(gen_dir, name)
            plugin_file = os.path.join(pkg_dir, 'kestrel_plugin.py')
            if os.path.isdir(pkg_dir) and os.path.isfile(plugin_file):
                mod = importlib.import_module(f'kestrel.generators.{name}.kestrel_plugin')
                reg = mod.register()
                generators[reg['name']] = reg

    # --- External plugins from KESTREL_PLUGINS ---
    plugins_env = os.environ.get('KESTREL_PLUGINS', '')
    if plugins_env:
        for plugin_path in plugins_env.split(':'):
            plugin_path = plugin_path.strip()
            if not plugin_path or not os.path.isdir(plugin_path):
                continue
            # Add plugin root to sys.path so its package is importable
            if plugin_path not in sys.path:
                sys.path.insert(0, plugin_path)
            # Find the package directory containing kestrel_plugin.py
            for pkg_name in sorted(os.listdir(plugin_path)):
                pkg_dir = os.path.join(plugin_path, pkg_name)
                plugin_file = os.path.join(pkg_dir, 'kestrel_plugin.py')
                if os.path.isdir(pkg_dir) and os.path.isfile(plugin_file):
                    mod = importlib.import_module(f'{pkg_name}.kestrel_plugin')
                    reg = mod.register()
                    if reg['name'] in generators:
                        raise ValueError(
                            f"generator name conflict: '{reg['name']}' "
                            f"already registered"
                        )
                    generators[reg['name']] = reg

    return generators
