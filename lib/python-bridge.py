#!/usr/bin/env python3
import argparse
import asyncio
import inspect
import json
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
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
    ariston_wheels = sorted(WHEEL_DIR.glob('ariston-0.19.9-*.whl'))
    package_spec = [str(ariston_wheels[0])] if ariston_wheels else ['ariston==0.19.9']
    package_spec += ['requests>=2.31.0']

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




def normalize_api_url(value: str) -> str:
    fallback = "https://www.ariston-net.remotethermo.com/api/v2/"
    raw = str(value or "").strip()
    if not raw:
        return fallback
    normalized = raw.rstrip("/") + "/"
    if normalized.lower().endswith("/r2/account/") or normalized.lower().endswith("/account/"):
        return fallback
    return normalized


def _mask_username(username: str) -> str:
    username = str(username or "")
    if "@" in username:
        local, domain = username.split("@", 1)
        shown = (local[:2] + "***") if local else "***"
        return f"{shown}@{domain}"
    if len(username) <= 3:
        return "***"
    return username[:2] + "***"


def diagnose_auth_failure(username: str, password: str, api_url: str, user_agent: str) -> str:
    api_url = normalize_api_url(api_url)
    login_url = api_url + 'accounts/login'
    payload = json.dumps({"usr": username, "pwd": password}).encode('utf-8')
    req = urllib.request.Request(
        login_url,
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': user_agent,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read(400).decode('utf-8', errors='replace')
            return f"Authentication failed. Login endpoint responded HTTP {response.status}. URL={login_url}. User={_mask_username(username)}. Body={body}"
    except urllib.error.HTTPError as exc:
        body = exc.read(400).decode('utf-8', errors='replace')
        return f"Authentication failed. HTTP {exc.code} from {login_url}. User={_mask_username(username)}. Body={body}"
    except Exception as exc:
        return f"Authentication failed while calling {login_url}. User={_mask_username(username)}. Diagnostic error: {exc}"

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
    if 'lydos' in name:
        names = enum_names(LydosPlantMode)
        return names or ['IMEMORY', 'GREEN', 'PROGRAM', 'BOOST']
    if 'nuossplit' in name or 'nuossplit' in name:
        return enum_names(NuosSplitOperativeMode)
    if 'evo' in name:
        return enum_names(EvoPlantMode)
    if 'lux' in name:
        return enum_names(LuxPlantMode)
    if 'bsb' in name:
        return enum_names(BsbOperativeMode)
    return []




def minutes_to_hhmm(value: Any):
    try:
        minutes = int(value)
    except Exception:
        return None
    if minutes < 0:
        return None
    hh = (minutes // 60) % 24
    mm = minutes % 60
    return f"{hh:02d}:{mm:02d}"


def as_bool(value: Any):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    sval = str(value).strip().lower()
    if sval in ('1', 'true', 'on', 'yes'):
        return True
    if sval in ('0', 'false', 'off', 'no'):
        return False
    return None




def normalize_time_control_bounds(current: Any, reported_min: Any, reported_max: Any) -> tuple[int, int]:
    def _to_int(v):
        try:
            return int(v)
        except Exception:
            return None

    cur = _to_int(current)
    mn = _to_int(reported_min)
    mx = _to_int(reported_max)

    candidates = [v for v in (cur, mn, mx) if v is not None and 0 <= v <= 1439]
    if not candidates:
        return 0, 1439

    low = min(candidates)
    high = max(candidates)

    # Some Ariston payloads report min/max reversed or wrap-like values for night mode.
    # Keep the range safe for ioBroker and always include the current value.
    return max(0, low), min(1439, high)

def ordered_min_max(a: Any, b: Any, fallback_min: int = 0, fallback_max: int = 1439):
    vals = []
    for v in (a, b):
        try:
            vals.append(int(v))
        except Exception:
            pass
    if not vals:
        return fallback_min, fallback_max
    if len(vals) == 1:
        return vals[0], max(vals[0], fallback_max)
    return min(vals), max(vals)


def map_water_heater_mode(device, raw_value: Any):
    try:
        enum_cls = device.water_heater_mode
        if isinstance(raw_value, str):
            return raw_value
        if raw_value is None:
            return None
        raw_int = int(raw_value)
        for member in enum_cls:
            if int(member.value) == raw_int:
                return member.name
    except Exception:
        return raw_value
    return raw_value


def enrich_values(device, values: dict[str, Any], units: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    out = dict(values)
    unit_map = dict(units)
    raw_data = to_primitive(getattr(device, 'data', None))
    if not isinstance(raw_data, dict):
        raw_data = {}

    mode_raw = values.get('water_heater_mode')
    if mode_raw is None:
        mode_raw = raw_data.get('mode')
        if mode_raw is not None:
            out['water_heater_mode'] = mode_raw

    mode_name = map_water_heater_mode(device, mode_raw)
    if mode_name is not None:
        out['water_heater_mode_name'] = mode_name

    current_temp = values.get('water_heater_temperature')
    if current_temp is None:
        current_temp = raw_data.get('temp')
    if current_temp is not None:
        out['current_water_heater_temperature'] = current_temp
        unit_map['current_water_heater_temperature'] = units.get('water_heater_temperature') or '°C'

    target_temp = values.get('target_water_heater_temperature')
    mode_name_upper = str(mode_name or '').upper()
    if mode_name_upper == 'BOOST' and raw_data.get('boostReqTemp') is not None:
        target_temp = raw_data.get('boostReqTemp')
    if target_temp is None and raw_data.get('reqTemp') is not None:
        target_temp = raw_data.get('reqTemp')
    if target_temp is None:
        target_temp = values.get('water_heater_temperature')
    if target_temp is None and values.get('proc_req_temp') not in (None, 0, 16):
        target_temp = values.get('proc_req_temp')
    if target_temp is not None:
        out['target_water_heater_temperature'] = target_temp
        unit_map['target_water_heater_temperature'] = units.get('water_heater_temperature') or '°C'

    boost_temp = raw_data.get('boostReqTemp')
    if boost_temp is not None:
        out['boost_water_heater_temperature'] = boost_temp
        unit_map['boost_water_heater_temperature'] = units.get('water_heater_temperature') or '°C'

    if raw_data.get('procReqTemp') is not None and out.get('proc_req_temp') is None:
        out['proc_req_temp'] = raw_data.get('procReqTemp')
        unit_map['proc_req_temp'] = units.get('water_heater_temperature') or '°C'

    if values.get('water_heater_power') is None and raw_data.get('on') is not None:
        out['water_heater_power'] = bool(raw_data.get('on'))

    if values.get('is_heating') is None and raw_data.get('heatReq') is not None:
        out['is_heating'] = bool(raw_data.get('heatReq'))

    for bool_key in ('anti_cooling', 'night_mode', 'permanent_boost', 'water_anti_leg'):
        normalized = as_bool(out.get(bool_key))
        if normalized is not None:
            out[bool_key] = normalized

    if values.get('av_shw') is not None:
        out['available_showers_estimate'] = values.get('av_shw')
    elif raw_data.get('avShw') is not None:
        out['available_showers_estimate'] = raw_data.get('avShw')

    if values.get('night_mode_begin_as_minutes') is not None:
        out['night_mode_begin_hhmm'] = minutes_to_hhmm(values.get('night_mode_begin_as_minutes'))
    if values.get('night_mode_end_as_minutes') is not None:
        out['night_mode_end_hhmm'] = minutes_to_hhmm(values.get('night_mode_end_as_minutes'))

    for attr in [
        'water_heater_maximum_setpoint_temperature_minimum',
        'water_heater_maximum_setpoint_temperature_maximum',
        'anti_cooling_temperature_minimum',
        'anti_cooling_temperature_maximum',
        'electric_consumption_for_water_last_two_hours',
        'online',
        'available',
    ]:
        try:
            value = getattr(device, attr)
        except Exception:
            continue
        primitive = to_primitive(value)
        if primitive is not None:
            out[attr] = primitive
            if 'temperature' in attr:
                unit_map[attr] = '°C'
    return out, unit_map

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
        mode_map = {}
        for name in operation_modes:
            mapped = None
            try:
                enum_cls = device.water_heater_mode
                member = getattr(enum_cls, name, None)
                if member is not None:
                    mapped = int(member.value)
            except Exception:
                mapped = None
            if mapped is not None:
                mode_map[str(mapped)] = name
        add_control(
            controls,
            'mode',
            type='string',
            role='level.mode',
            method='async_set_water_heater_operation_mode',
            states=operation_modes,
            current=map_water_heater_mode(device, values.get('water_heater_mode')),
            mode_map=mode_map,
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
            current=values.get('target_water_heater_temperature') or values.get('water_heater_temperature') or values.get('req_temp') or values.get('proc_req_temp'),
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
        add_control(controls, 'antilegionella', type='boolean', role='switch.enable', method='async_set_antilegionella', current=as_bool(values.get('water_anti_leg')))

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

    if 'async_set_permanent_boost_value' in methods:
        add_control(controls, 'permanent_boost', type='boolean', role='switch.enable', method='async_set_permanent_boost_value', current=as_bool(values.get('permanent_boost')))

    if 'async_set_anti_cooling_value' in methods:
        add_control(controls, 'anti_cooling', type='boolean', role='switch.enable', method='async_set_anti_cooling_value', current=as_bool(values.get('anti_cooling')))

    if 'async_set_cooling_temperature_value' in methods:
        add_control(controls, 'anti_cooling_temperature', type='number', role='level.temperature', method='async_set_cooling_temperature_value', unit='°C', min=values.get('anti_cooling_temperature_minimum') or 30, max=values.get('anti_cooling_temperature_maximum') or 80, current=values.get('anti_cooling_temperature'))

    if 'async_set_night_mode_value' in methods:
        add_control(controls, 'night_mode', type='boolean', role='switch.enable', method='async_set_night_mode_value', current=as_bool(values.get('night_mode')))

    if 'async_set_night_mode_begin_as_minutes_value' in methods:
        begin_min, begin_max = normalize_time_control_bounds(
            values.get('night_mode_begin_as_minutes'),
            values.get('night_mode_begin_min_as_minutes'),
            values.get('night_mode_begin_max_as_minutes'),
        )
        add_control(controls, 'night_mode_begin_as_minutes', type='number', role='level', method='async_set_night_mode_begin_as_minutes_value', min=begin_min, max=begin_max, current=values.get('night_mode_begin_as_minutes'))

    if 'async_set_night_mode_end_as_minutes_value' in methods:
        end_min, end_max = normalize_time_control_bounds(
            values.get('night_mode_end_as_minutes'),
            values.get('night_mode_end_min_as_minutes'),
            values.get('night_mode_end_max_as_minutes'),
        )
        add_control(controls, 'night_mode_end_as_minutes', type='number', role='level', method='async_set_night_mode_end_as_minutes_value', min=end_min, max=end_max, current=values.get('night_mode_end_as_minutes'))

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
    api_url = normalize_api_url(args.api_url)
    try:
        ok = await client.async_connect(args.username, args.password, api_url, args.user_agent)
    except Exception as exc:
        raise RuntimeError(f"{diagnose_auth_failure(args.username, args.password, api_url, args.user_agent)} | Library error: {exc}")
    if not ok:
        raise RuntimeError(diagnose_auth_failure(args.username, args.password, api_url, args.user_agent))

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
    api_url = normalize_api_url(args.api_url)
    try:
        ok = await client.async_connect(args.username, args.password, api_url, args.user_agent)
    except Exception as exc:
        raise RuntimeError(f"{diagnose_auth_failure(args.username, args.password, api_url, args.user_agent)} | Library error: {exc}")
    if not ok:
        raise RuntimeError(diagnose_auth_failure(args.username, args.password, api_url, args.user_agent))
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

    values, units = enrich_values(device, values, units)
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
        'raw': {
            'data': to_primitive(getattr(device, 'data', None)),
            'plant_settings': to_primitive(getattr(device, 'plant_settings', None)),
            'errors': to_primitive(getattr(device, 'errors', None)),
            'features': to_primitive(getattr(device, 'features', None)),
        },
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
    if cid == 'mode':
        sval = str(raw_value).strip()
        mode_map = control.get('mode_map') or {}
        if sval in mode_map:
            return mode_map[sval]
        if sval.isdigit() and str(int(sval)) in mode_map:
            return mode_map[str(int(sval))]
        allowed = control.get('states') or []
        for candidate in allowed:
            if str(candidate).upper() == sval.upper():
                return str(candidate)
        return sval.upper()
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
    args.api_url = normalize_api_url(args.api_url)

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
