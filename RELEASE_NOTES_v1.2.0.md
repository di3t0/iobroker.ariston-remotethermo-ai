## Overview

First stable public release of the ioBroker adapter for Ariston RemoteThermo devices.

This release focuses on Ariston Lydos Hybrid compatibility and provides practical read/write support for day-to-day automation in ioBroker.

## Highlights

- working device discovery and cloud login
- bundled Python bridge based on `ariston==0.19.9`
- working mode switching for supported Lydos Hybrid modes:
  - `IMEMORY`
  - `GREEN`
  - `PROGRAM`
  - `BOOST`
- current water temperature state
- target water temperature state
- writable controls for:
  - power
  - mode
  - domestic hot water target temperature
  - permanent boost
  - night mode
  - anti-cooling
  - antilegionella
- improved icon handling for ioBroker admin and instances
- improved polling and refresh behavior after control writes

## Important notes

For Lydos Hybrid, operation mode and target temperature should be treated as separate controls.

Example:
- `BOOST` enables fast heating behavior
- target temperature is still controlled separately through `dhw_set_temperature`

If you want boost heating up to 75°C, use:
1. mode = `BOOST`
2. `dhw_set_temperature` = `75`

## Recommended states for automation

Read:
- `values.current_water_heater_temperature`
- `values.target_water_heater_temperature`
- `values.water_heater_mode_name`
- `values.water_heater_power`

Write:
- `controls.mode`
- `controls.dhw_set_temperature`
- `controls.power`
- `controls.permanent_boost`

## Known limitations

- tested primarily against Ariston Lydos Hybrid
- some device-specific features may vary by model
- schedule editing is not implemented; existing scheduling configured in the Ariston app is preserved
