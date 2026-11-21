# ioBroker Ariston RemoteThermo AI

ioBroker adapter for Ariston RemoteThermo devices with focus on Ariston Lydos Hybrid water heaters.

## Features

- Ariston cloud login
- device discovery
- current and target water temperature states
- water heater mode state
- writable controls for common water-heater functions
- energy consumption (hourly / daily / monthly kWh, measured and estimated power) — since 1.3.0
- bundled Python bridge using `ariston==0.19.9`

## Tested devices

| Device | Ariston type | Library class | Status |
| --- | --- | --- | --- |
| Ariston Lydos Hybrid | LydosHybrid | `AristonLydosHybridDevice` | ✅ primary target of the upstream project |
| Ariston Lydos Wifi | Lydos | – | ✅ reported working (upstream issue #1) |
| **Ariston Velis WIFI 50** | **Velis Evo2** (`wheType 6`, `systemType 4`) | `AristonEvoDevice` | ✅ **tested 2026-11-21: live values, controls and all energy states work** |

With the Velis WIFI 50, the live values (water and target temperature, mode, ECO, heating
request, remaining time, showers) match a direct `api/v2` query (`velis/medPlantData`) one
to one. Consumption is read with `usages=Dhw`. Note that the cloud reports `antiLeg` as the
anti-legionella cycle currently running, while `values.water_anti_leg` reflects the program
setting.

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

## Energy states (since 1.3.0)

**In short:** The Ariston cloud does not report a live power value. It only keeps a
consumption history in kWh (per hour for the last 24 h, per day for the last 7/30 days,
per month). The adapter reads this history and turns it into ready-to-use values such as
*today*, *yesterday*, *last 30 days* or *total*. Use them for SourceAnalytix, InfluxDB or VIS.
It also provides two power values. `measured_power_w` is calculated from the increase in
kWh, and `estimated_power_w` is the heater's rated power while it is heating.

The consumption report `GET api/v2/remote/reports/{gw}/consSequencesApi8?usages=<type>`
is read through the library call `device.async_update_energy()`. Because this report is slow
and rate limited, it is not fetched on every poll but on the first poll and then every
`energyEveryN`-th poll (default 3 → every 15 min at a 300 s poll interval).

All states live in the channel `devices.<gatewayId>.energy` and are read-only:

| State | Unit | Role | Meaning |
| --- | --- | --- | --- |
| `energy.current_hour_kwh` | kWh | `value.energy` | consumption of the running hour |
| `energy.last_full_hour_kwh` | kWh | `value.energy` | consumption of the last completed hour |
| `energy.last_24h_kwh` | kWh | `value.energy` | rolling 24 hours |
| `energy.today_kwh` | kWh | `value.energy` | since local midnight (host time zone) |
| `energy.yesterday_kwh` | kWh | `value.energy` | previous day |
| `energy.last_7_days_kwh` | kWh | `value.energy` | last 7 days |
| `energy.last_30_days_kwh` | kWh | `value.energy` | last ~30 days |
| `energy.last_month_kwh` | kWh | `value.energy` | last **completed** month |
| `energy.last_12_months_kwh` | kWh | `value.energy` | last 12 month buckets |
| `energy.total_kwh` | kWh | `value.energy` | sum of all month buckets the cloud reports |
| `energy.measured_power_w` | W | `value.power` | average power between two energy polls, derived from the increase of `today_kwh` |
| `energy.estimated_power_w` | W | `value.power` | `is_heating × nominalPowerW` — updated on every poll |
| `energy.hourly_series` | – | `json` | hourly buckets of the last 24 h, oldest first |
| `energy.daily7_series` | – | `json` | daily buckets of the last 7 days, oldest first |
| `energy.daily30_series` | – | `json` | daily buckets of the last ~30 days, oldest first |
| `energy.monthly_series` | – | `json` | monthly buckets, oldest first |
| `energy.raw_sequences` | – | `json` | raw `consSequencesApi8` response |
| `energy.last_update` | – | `date` | timestamp of the last successful energy poll |
| `energy.last_error` | – | `text` | last energy error (empty when OK) |

### Interval semantics of `consSequencesApi8`

Verified against a Velis Evo2 (`wheType 6`):

```
p=1  rolling 24 h, hourly buckets, oldest first   → last element = running hour
p=2  last 7 days, daily buckets, oldest first     → last element = yesterday
p=3  last ~30 days, daily buckets, oldest first   → tail equals p=2
p=4  monthly buckets, oldest first                → last element = last completed month

today_kwh = sum of the last (local hour + 1) elements of p=1
```

If several consumption types (`k`) are returned for one query they are summed element-wise
per interval. An energy error never breaks the regular state sync; it is logged as a warning
and written to `energy.last_error`.

`measured_power_w` is only available from the second energy poll of a day on, and it is
reset at local midnight. Because the cloud aggregates hourly, it is a coarse average, not a
live reading.

### Configuration

| Option | Default | Meaning |
| --- | --- | --- |
| `energyEnabled` | `true` | read the consumption report |
| `energyEveryN` | `3` | read it every N-th poll |
| `nominalPowerW` | `1500` | heater power used for `estimated_power_w` |

## Installation

Install from a GitHub custom adapter URL or from your own published repository.

## Known limitations

- tested primarily against Ariston Lydos Hybrid; also verified on Ariston Velis WIFI 50 (Evo2), see *Tested devices*
- some device-specific features may vary by model
- schedule editing is not implemented; existing scheduling configured in the Ariston app is preserved

## License

MIT


## Rate limiting

When the Ariston cloud returns HTTP 429 / "Requests are blocked", the adapter enters a cooldown period and temporarily pauses writes and polling to avoid hammering the API.


## Python bootstrap

This adapter can now create and use a self-managed local virtual environment inside the adapter folder when the host blocks system-wide pip installs (for example Debian 12 / PEP 668).


## Changelog

### 1.3.0 (2026-11-21)

- energy states from `consSequencesApi8` (channel `devices.<gw>.energy`, see above)
- new options `energyEnabled`, `energyEveryN`, `nominalPowerW`
- verified on Ariston Velis WIFI 50 (Evo2, `wheType 6`)
