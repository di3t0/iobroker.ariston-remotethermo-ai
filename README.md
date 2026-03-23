# ioBroker Ariston Cloud adapter

GitHub-ready custom ioBroker adapter for Ariston RemoteThermo based on the upstream Python library `ariston==0.19.9`.

## What is included

- production-oriented ioBroker adapter structure
- bundled `ariston==0.19.9`
- bundled Python wheels for easier bootstrap
- multi-device discovery and sync
- writable controls for basic heating and DHW functions
- fallback install strategy: `auto`, `offline`, `online`, `system`

## Directory layout

- `main.js` - ioBroker adapter runtime
- `io-package.json` - ioBroker metadata and native config
- `package.json` - Node package metadata
- `admin/jsonConfig.json` - Admin UI config form
- `lib/python-bridge.py` - Python bridge for Ariston API access
- `python-wheels/` - bundled Python packages
- `ariston.png` - adapter icon
- `LICENSE` - project license

## Exposed states

- `ariston-cloud.X.info.*`
- `ariston-cloud.X.devices.<gateway>.meta.*`
- `ariston-cloud.X.devices.<gateway>.values.*`
- `ariston-cloud.X.devices.<gateway>.controls.*`
- `ariston-cloud.X.commands.refresh`
- `ariston-cloud.X.commands.invoke`

## Basic writable controls

Depending on device capabilities, the adapter can create controls such as:

- `power`
- `mode`
- `dhw_set_temperature`
- `dhw_reduced_temperature`
- `eco_mode`
- `antilegionella`
- `boost`
- `preheating`
- `holiday_until`
- `automatic_thermoregulation`
- `quiet`
- `hybrid_mode`
- `buffer_control_mode`
- `heating_rate`
- `max_setpoint_temp`
- `ch_set_temperature_zone_1`
- `ch_reduced_temperature_zone_1`
- `ch_mode_zone_1`
- `ch_flow_temperature_zone_1`
- `ch_flow_offset_zone_1`

Additional zones are created dynamically when available.

## Generic command invoke

For methods that are not yet mapped to native controls, write JSON into `commands.invoke`:

```json
{"gatewayId":"123456789","method":"async_set_water_heater_temperature","args":[50]}
```

## Recommended install strategy

Default recommendation:

- host with `python3`
- adapter config `installStrategy=auto`

The adapter will try:
1. bundled offline wheels
2. regular `pip` install if needed
3. system-installed packages if already present

## Upload to GitHub

### Option A - easiest: GitHub web upload

1. Create a new empty repository on GitHub.
2. Download and unzip this package on your PC.
3. Open the new repository on GitHub.
4. Click **Add file** -> **Upload files**.
5. Drag the **contents of the unzipped folder**, not the ZIP itself.
6. Wait until upload finishes.
7. Enter a commit message such as `Initial commit`.
8. Click **Commit changes**.

Important:
- upload the extracted files and folders
- do not upload only the ZIP file if you want a normal source repository

### Option B - Windows CMD

You can do it from classic Windows Command Prompt.

Example:

```cmd
cd C:\Users\YOURNAME\Downloads
powershell -Command "Expand-Archive -Path .\iobroker-ariston-cloud-github-ready.zip -DestinationPath .\iobroker-ariston-cloud-github-ready"
cd iobroker-ariston-cloud-github-ready
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOURNAME/YOURREPO.git
git push -u origin main
```

If Git asks for authentication, use GitHub login or token flow shown by Git.

### Option C - Windows PowerShell

```powershell
cd $HOME\Downloads
Expand-Archive .\iobroker-ariston-cloud-github-ready.zip -DestinationPath .\iobroker-ariston-cloud-github-ready
cd .\iobroker-ariston-cloud-github-ready
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOURNAME/YOURREPO.git
git push -u origin main
```

## Local testing before push

```bash
npm i
node -c main.js
```

## Deployment to ioBroker

1. Ensure `python3` is available on the ioBroker host.
2. Copy/install this custom adapter into your ioBroker environment.
3. Run `npm i` in the adapter directory.
4. Configure credentials in ioBroker admin.
5. Start the adapter and inspect `info.lastError` if bootstrap fails.

## Notes

- bundled binary wheels may still depend on target platform and Python ABI
- `installStrategy=auto` is safest for mixed environments
- this repository is prepared for GitHub upload, but it is not yet an official ioBroker community adapter release
