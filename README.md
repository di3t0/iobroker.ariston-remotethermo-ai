# ioBroker Ariston RemoteThermo AI adapter

Custom ioBroker adapter for Ariston NET / Remote Thermo cloud devices.

## Included

- bundled `ariston==0.19.9`
- bundled Python wheels for easier bootstrap
- multi-device discovery and sync
- writable controls for common heating and DHW functions
- install strategies: `auto`, `offline`, `online`, `system`

## Important

This is a custom adapter package for manual installation from GitHub.

The adapter name in this package is:

- npm package: `iobroker.ariston-remotethermo-ai`
- ioBroker adapter name: `ariston-remotethermo-ai`

These names are aligned so installation from GitHub works correctly.

## Directory layout

- `main.js` - ioBroker adapter runtime
- `io-package.json` - ioBroker metadata and native config
- `package.json` - Node package metadata
- `admin/jsonConfig.json` - admin UI config form
- `lib/python-bridge.py` - Python bridge for Ariston API access
- `python-wheels/` - bundled Python packages
- `ariston.png` - adapter icon

## Installation from GitHub

Example:

```bash
iobroker url https://github.com/di3t0/iobroker.ariston-remotethermo-ai.git --host iobroker --debug
```

## Notes

Bundled Python dependencies improve deployment convenience, but some binary wheels may still depend on the host platform and Python version.


## Python compatibility notes

This build is designed to be more tolerant on typical ioBroker Linux hosts:

- works with system `python3`
- bootstraps `pip` via `ensurepip` when `python3-pip` is missing
- tries bundled wheels first when compatible
- falls back to online install when needed

Recommended setting for most systems: `installStrategy=auto`.


## 1.1.0 notes

- fixed the default API URL to `https://www.ariston-net.remotethermo.com/api/v2/`
- old wrong values like `.../R2/Account/` are auto-normalized
- improved authentication diagnostics in adapter logs
- adapter enabled by default for cleaner initial setup


## Automation states

Recommended readable states for automation on Lydos Hybrid:
- `devices.<gateway>.values.water_heater_mode_name`
- `devices.<gateway>.values.water_heater_mode`
- `devices.<gateway>.values.target_water_heater_temperature`
- `devices.<gateway>.values.water_heater_power`
- `devices.<gateway>.values.available_showers_estimate`

Recommended writable states:
- `devices.<gateway>.controls.mode`
- `devices.<gateway>.controls.dhw_set_temperature`
- `devices.<gateway>.controls.power`
