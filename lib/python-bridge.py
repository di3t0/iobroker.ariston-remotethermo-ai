#!/usr/bin/env python3
import argparse
import asyncio
import inspect
import json
import subprocess
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
WHEEL_DIR = BASE_DIR / 'python-wheels'
LOCAL_DEPS_DIR = BASE_DIR / '.pydeps'


def _ensure_pip_module() -> None:
    try:
        import pip  # noqa:F401
        return
    except Exception:
        pass

    try:
        import ensurepip
        ensurepip.bootstrap(upgrade=True)
        return
    except Exception as exc:
        raise RuntimeError(
            'Python module pip is not available and automatic bootstrap via ensurepip failed. '
            f'Install python3-pip on the host or switch installStrategy=system. Details: {exc}'
        )


def _pip_install(target: Path, packages: list[str], offline: bool) -> tuple[int, str]:
    _ensure_pip_module()
    cmd = [
        sys.executable,
        '-m',
        'pip',
        'install',
        '--disable-pip-version-check',
        '--no-input',
        '--upgrade',
        '--target',
        str(target),
    ]
    if offline:
        cmd += ['--find-links', str(WHEEL_DIR), '--no-index']
    cmd += packages
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, (result.stderr or result.stdout or '').strip()


def bootstrap_local_deps(strategy: str) -> None:
    if str(LOCAL_DEPS_DIR) not in sys.path:
        sys.path.insert(0, str(LOCAL_DEPS_DIR))

    try:
        import ariston  # noqa:F401
        return
    except Exception:
        pass

    if strategy == 'system':
        raise RuntimeError('Python package ariston is not importable and installStrategy=system forbids bootstrap')

    if not WHEEL_DIR.exists():
        raise RuntimeError('python-wheels directory not found and ariston is not installed')

    LOCAL_DEPS_DIR.mkdir(parents=True, exist_ok=True)
    package_spec = ['ariston==0.19.9', 'requests>=2.31.0']

    offline_err = ''
    online_err = ''
    if strategy in ('auto', 'offline'):
        code, offline_err = _pip_install(LOCAL_DEPS_DIR, package_spec, offline=True)
        if code == 0:
            sys.path.insert(0, str(LOCAL_DEPS_DIR))
        elif strategy == 'offline':
            raise RuntimeError(f'Offline install from bundled wheels failed: {offline_err}')

    try:
        import ariston  # noqa:F401
        return
    except Exception:
        pass

    if strategy in ('auto', 'online'):
        code, online_err = _pip_install(LOCAL_DEPS_DIR, package_spec, offline=False)
        if code != 0:
            raise RuntimeError(
                'Failed to install Python dependencies. '
                f'Offline error: {offline_err} | Online error: {online_err}'
            )

    if str(LOCAL_DEPS_DIR) not in sys.path:
        sys.path.insert(0, str(LOCAL_DEPS_DIR))
    try:
        import ariston  # noqa:F401
    except Exception as exc:
        raise RuntimeError(f'Dependencies installed, but import still failed: {exc}')


def to_primitive(value: Any):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return [to_primitive(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_primitive(v) for k, v in value.items()}
    if hasattr(value, 'value') and not inspect.isroutine(getattr(value, 'value')):
        try:
            return to_primitive(value.value)
        except Exception:
            pass
    return str(value)


def enum_names(enum_cls) -> list[str]:
    try:
        return [m.name for m in enum_cls]
    except Exception:
        return []


def get_zone_numbers(values: dict[str, Any]) -> list[int]:
    zones = set()
    for key in values:
        if key.startswith('zone_'):
            parts = key.split('_')
            if len(parts) > 2 and parts[1].isdigit():
                zones.add(int(parts[1]))
    return sorted(zones) or [1]


def add_control(controls: list[dict[str, Any]], control_id: str, **kwargs) -> None:
    controls.append({'id': control_id, **kwargs})


def guess_operation_modes(device):
    from ariston.const import (
        BsbOperativeMode,
        EvoPlantMode,
        LydosPlantMode,
        LuxPlantMode,
        NuosSplitOperativeMode,
        VelisPlantMode,
    )

    name = device.__class__.__name__.lower()
    if 'galevo' in name:
        getter = getattr(device, '_get_item_by_id', None)
        if getter:
            try:
                from ariston.const import DeviceProperties, PropertyType
                vals = getter(DeviceProperties.DHW_MODE, PropertyType.OPT_TEXTS)
                if isinstance(vals, list) and vals:
                    return [str(v) for v in vals]
            except Exception:
                pass
        return []
    if 'velis' in name:
        return enum_names(VelisPlantMode)
    if 'lydos' in name and 'hybrid' not in name:
        return enum_names(LydosPlantMode)
    if 'nuossplit' in name or 'nuossplit' in name:
        return enum_names(NuosSplitOperativeMode)
    if 'evo' in name:
        return enum_names(EvoPlantMode)
    if 'lux' in name:
        return enum_names(LuxPlantMode)
    if 'bsb' in name:
        return enum_names(BsbOperativeMode)
    return []


def build_controls(device, methods: list[str], values: dict[str, Any], units: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    zones = get_zone_numbers(values)
    operation_modes = guess_operation_modes(device)

    if 'async_set_power' in methods:
        current = values.get('power')
        if current is None and values.get('water_heater_mode'):
            current = str(values.get('water_heater_mode')).upper() not in ('OFF', 'PROGRAM_OFF')
        add_control(controls, 'power', type='boolean', role='switch.enable', method='async_set_power', current=current)

    if 'async_set_water_heater_operation_mode' in methods:
        add_control(
            controls,
            'mode',
            type='string',
            role='level.mode',
            method='async_set_water_heater_operation_mode',
            states=operation_modes,
            current=values.get('water_heater_mode'),
        )

    if 'async_set_water_heater_temperature' in methods:
        add_control(
            controls,
            'dhw_set_temperature',
            type='number',
            role='level.temperature',
            method='async_set_water_heater_temperature',
            unit=units.get('water_heater_temperature') or '°C',
            min=30,
            max=80,
            current=values.get('water_heater_temperature') or values.get('proc_req_temp'),
        )

    if 'async_set_water_heater_reduced_temperature' in methods:
        add_control(
            controls,
            'dhw_reduced_temperature',
            type='number',
            role='level.temperature',
            method='async_set_water_heater_reduced_temperature',
            unit=units.get('water_heater_reduced_temperature') or '°C',
            min=30,
            max=80,
            current=values.get('water_heater_reduced_temperature'),
        )

    if 'async_set_eco_mode' in methods:
        add_control(controls, 'eco_mode', type='boolean', role='switch.mode.eco', method='async_set_eco_mode', current=values.get('water_heater_eco'))

    if 'async_set_antilegionella' in methods:
        add_control(controls, 'antilegionella', type='boolean', role='switch.enable', method='async_set_antilegionella', current=values.get('antilegionella'))

    if 'async_set_water_heater_number_of_showers' in methods:
        add_control(controls, 'number_of_showers', type='number', role='level', method='async_set_water_heater_number_of_showers', min=1, max=8, current=values.get('water_heater_number_of_showers'))

    if 'async_set_water_heater_boost' in methods:
        add_control(controls, 'boost', type='boolean', role='switch.enable', method='async_set_water_heater_boost', current=values.get('water_heater_boost'))

    if 'async_set_preheating' in methods:
        add_control(controls, 'preheating', type='boolean', role='switch.enable', method='async_set_preheating', current=values.get('preheating'))

    if 'async_set_holiday' in methods:
        add_control(controls, 'holiday_until', type='string', role='text', method='async_set_holiday', current=values.get('holiday_end'), help='YYYY-MM-DD or empty string')

    if 'async_set_automatic_thermoregulation' in methods:
        add_control(controls, 'automatic_thermoregulation', type='boolean', role='switch.enable', method='async_set_automatic_thermoregulation', current=values.get('automatic_thermoregulation'))

    if 'async_set_is_quiet' in methods:
        add_control(controls, 'quiet', type='boolean', role='switch.enable', method='async_set_is_quiet', current=values.get('is_quiet'))

    if 'async_set_hybrid_mode' in methods:
        add_control(controls, 'hybrid_mode', type='string', role='level.mode', method='async_set_hybrid_mode', current=values.get('hybrid_mode'))

    if 'async_set_buffer_control_mode' in methods:
        add_control(controls, 'buffer_control_mode', type='string', role='level.mode', method='async_set_buffer_control_mode', current=values.get('buffer_control_mode'))

    if 'async_set_heating_rate' in methods:
        add_control(controls, 'heating_rate', type='number', role='level', method='async_set_heating_rate', min=0, max=100, current=values.get('heating_rate'))

    if 'async_set_max_setpoint_temp' in methods:
        add_control(controls, 'max_setpoint_temp', type='number', role='level.temperature', method='async_set_max_setpoint_temp', unit='°C', min=30, max=80, current=values.get('max_setpoint_temp'))

    for zone in zones:
        if 'async_set_comfort_temp' in methods:
            add_control(
                controls,
                f'ch_set_temperature_zone_{zone}',
                type='number',
                role='level.temperature',
                method='async_set_comfort_temp',
                extra_args=[zone],
                unit='°C',
                min=5,
                max=35,
                current=values.get(f'zone_{zone}_comfort_temp') or values.get(f'target_temp_zone_{zone}'),
            )
        if 'async_set_reduced_temp' in methods:
            add_control(
                controls,
                f'ch_reduced_temperature_zone_{zone}',
                type='number',
                role='level.temperature',
                method='async_set_reduced_temp',
                extra_args=[zone],
                unit='°C',
                min=5,
                max=35,
                current=values.get(f'zone_{zone}_reduced_temp'),
            )
        if 'async_set_zone_mode' in methods:
            add_control(
                controls,
                f'ch_mode_zone_{zone}',
                type='string',
                role='level.mode',
                method='async_set_zone_mode',
                extra_args=[zone],
                states=['OFF', 'MANUAL_NIGHT', 'MANUAL', 'TIME_PROGRAM'],
                current=values.get(f'zone_{zone}_mode'),
            )
        if 'async_set_heating_flow_temp' in methods:
            add_control(
                controls,
                f'ch_flow_temperature_zone_{zone}',
                type='number',
                role='level.temperature',
                method='async_set_heating_flow_temp',
                extra_args=[zone],
                unit='°C',
                min=20,
                max=80,
                current=values.get(f'zone_{zone}_heating_flow_temp') or values.get('heating_flow_temp'),
            )
        if 'async_set_heating_flow_offset' in methods:
            add_control(
                controls,
                f'ch_flow_offset_zone_{zone}',
                type='number',
                role='level.temperature',
                method='async_set_heating_flow_offset',
                extra_args=[zone],
                unit='°C',
                min=-20,
                max=20,
                current=values.get(f'zone_{zone}_heating_flow_offset'),
            )

    return controls


async def connect_device(args):
    from ariston import Ariston, DeviceAttribute

    client = Ariston()
    ok = await client.async_connect(args.username, args.password, args.api_url, args.user_agent)
    if not ok:
        raise RuntimeError('Authentication failed')

    devices = await client.async_discover()
    if not devices:
        raise RuntimeError('No devices discovered')

    selected = None
    for dev in devices:
        if args.gateway_id and str(dev.get(DeviceAttribute.GW)) == str(args.gateway_id):
            selected = dev
            break
    if selected is None:
        selected = devices[0]

    device = await client.async_hello(selected.get(DeviceAttribute.GW), args.metric)
    if device is None:
        raise RuntimeError('Device hello failed')
    if hasattr(device, 'async_get_features'):
        await device.async_get_features()
    return client, device, devices


async def discover_all(args):
    from ariston import Ariston, DeviceAttribute

    client = Ariston()
    ok = await client.async_connect(args.username, args.password, args.api_url, args.user_agent)
    if not ok:
        raise RuntimeError('Authentication failed')
    devices = await client.async_discover()
    payload = []
    for dev in devices:
        payload.append({
            'name': dev.get(DeviceAttribute.NAME),
            'gateway': dev.get(DeviceAttribute.GW),
            'serial': dev.get(DeviceAttribute.SN),
            'raw': {str(k): to_primitive(v) for k, v in dev.items()},
        })
    return payload


def collect_state_payload(device, devices: Iterable[Any]):
    values: dict[str, Any] = {}
    units: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    methods: list[str] = []

    for name in dir(device):
        if name.startswith('_'):
            continue
        try:
            attr = getattr(device, name)
        except Exception:
            continue
        if inspect.ismethod(attr) or inspect.isfunction(attr) or inspect.iscoroutinefunction(attr):
            if name.startswith('async_set_') or name.startswith('set_'):
                methods.append(name)
            continue
        if name.endswith('_value'):
            values[name[:-6]] = to_primitive(attr)
        elif name.endswith('_unit'):
            units[name[:-5]] = to_primitive(attr)
        elif name in ('name', 'system_type', 'whe_type', 'has_metering', 'has_dhw', 'available', 'online'):
            meta[name] = to_primitive(attr)

    meta['device_class'] = device.__class__.__name__
    controls = build_controls(device, methods, values, units)
    return {
        'ok': True,
        'device': meta,
        'values': values,
        'units': units,
        'methods': sorted(methods),
        'controls': controls,
        'device_count': len(list(devices)),
    }


async def cmd_discover(args):
    print(json.dumps({'ok': True, 'devices': await discover_all(args)}, ensure_ascii=False))


async def cmd_state(args):
    _client, device, devices = await connect_device(args)
    if hasattr(device, 'async_update_state'):
        await device.async_update_state()
    print(json.dumps(collect_state_payload(device, devices), ensure_ascii=False))


def parse_control_value(control: dict[str, Any], raw_value: Any):
    ctype = control.get('type')
    cid = control.get('id')
    if cid == 'holiday_until':
        sval = str(raw_value or '').strip()
        return None if sval == '' else date.fromisoformat(sval)
    if ctype == 'boolean':
        if isinstance(raw_value, bool):
            return raw_value
        sval = str(raw_value).strip().lower()
        if sval in ('1', 'true', 'on', 'yes'):
            return True
        if sval in ('0', 'false', 'off', 'no'):
            return False
        raise RuntimeError(f'Cannot parse boolean value: {raw_value}')
    if ctype == 'number':
        num = float(raw_value)
        return int(num) if num.is_integer() else num
    return str(raw_value)


async def cmd_invoke(args):
    _client, device, _devices = await connect_device(args)
    method_name = args.method
    if not hasattr(device, method_name):
        raise RuntimeError(f'Method not found: {method_name}')
    method = getattr(device, method_name)
    parsed_args = json.loads(args.invoke_args or '[]')
    if not isinstance(parsed_args, list):
        raise RuntimeError('invoke-args must be a JSON array')
    result = method(*parsed_args)
    if inspect.isawaitable(result):
        result = await result
    print(json.dumps({'ok': True, 'result': to_primitive(result)}, ensure_ascii=False))


async def cmd_control(args):
    _client, device, devices = await connect_device(args)
    if hasattr(device, 'async_update_state'):
        await device.async_update_state()

    payload = collect_state_payload(device, devices)
    control = next((c for c in payload['controls'] if c.get('id') == args.control_id), None)
    if control is None:
        raise RuntimeError(f'Unknown control: {args.control_id}')

    method_name = control.get('method')
    parsed_value = parse_control_value(control, args.value)
    invoke_args = [parsed_value] + list(control.get('extra_args', []))

    if not method_name or not hasattr(device, method_name):
        raise RuntimeError(f'Method not found for control {args.control_id}: {method_name}')
    method = getattr(device, method_name)
    result = method(*invoke_args)
    if inspect.isawaitable(result):
        result = await result
    print(json.dumps({'ok': True, 'result': to_primitive(result), 'method': method_name, 'args': to_primitive(invoke_args)}, ensure_ascii=False))


async def main_async():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['discover', 'state', 'invoke', 'control'])
    parser.add_argument('--username', required=True)
    parser.add_argument('--password', required=True)
    parser.add_argument('--api-url', required=True)
    parser.add_argument('--user-agent', required=True)
    parser.add_argument('--gateway-id', default='')
    parser.add_argument('--metric', action='store_true')
    parser.add_argument('--method', default='')
    parser.add_argument('--invoke-args', default='[]')
    parser.add_argument('--control-id', default='')
    parser.add_argument('--value', default='')
    parser.add_argument('--install-strategy', default='auto', choices=['auto', 'offline', 'online', 'system'])
    args = parser.parse_args()

    try:
        bootstrap_local_deps(args.install_strategy)
        if args.command == 'discover':
            await cmd_discover(args)
        elif args.command == 'state':
            await cmd_state(args)
        elif args.command == 'invoke':
            await cmd_invoke(args)
        elif args.command == 'control':
            await cmd_control(args)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'traceback': traceback.format_exc()}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main_async()))
