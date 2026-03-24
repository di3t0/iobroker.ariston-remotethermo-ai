# ioBroker Ariston RemoteThermo AI

ioBroker adapter for Ariston RemoteThermo devices with focus on Ariston Lydos Hybrid water heaters.

## Features

- Ariston cloud login
- device discovery
- current and target water temperature states
- water heater mode state
- writable controls for common water-heater functions
- bundled Python bridge using `ariston==0.19.9`

## Main writable controls

- `controls.mode`
- `controls.dhw_set_temperature`
- `controls.power`
- `controls.permanent_boost`

## Notes

For Lydos Hybrid, operation mode and target temperature are separate.
`BOOST` does not automatically force the target to 75°C.
To boost heating to 75°C, set:

- `controls.mode = BOOST`
- `controls.dhw_set_temperature = 75`

## Recommended states for automation

### Read

- `values.current_water_heater_temperature`
- `values.target_water_heater_temperature`
- `values.water_heater_mode_name`
- `values.water_heater_power`

### Write

- `controls.mode`
- `controls.dhw_set_temperature`
- `controls.power`
- `controls.permanent_boost`

## Installation

Install from a GitHub custom adapter URL or from your own published repository.

## Known limitations

- tested primarily against Ariston Lydos Hybrid
- some device-specific features may vary by model
- schedule editing is not implemented; existing scheduling configured in the Ariston app is preserved

## License

MIT
