'use strict';

const utils = require('@iobroker/adapter-core');
const { spawn } = require('node:child_process');
const path = require('node:path');

class AristonRemoteThermoAiAdapter extends utils.Adapter {
    constructor(options = {}) {
        super({ ...options, name: 'ariston-remotethermo-ai' });
        this.pollTimer = null;
        this.syncInProgress = false;
        this.pendingRefresh = false;
        this.pollCounter = 0;
        this.correctedApiUrl = null;
        this.rateLimitedUntil = 0;
        this.on('ready', this.onReady.bind(this));
        this.on('stateChange', this.onStateChange.bind(this));
        this.on('unload', this.onUnload.bind(this));
    }


    normalizeApiUrl(rawUrl) {
        const fallback = 'https://www.ariston-net.remotethermo.com/api/v2/';
        const input = String(rawUrl || '').trim();
        if (!input) return fallback;
        const normalized = input.replace(/\/+$/, '/');
        if (/\/R2\/Account\/?$/i.test(normalized) || /\/Account\/?$/i.test(normalized)) {
            return fallback;
        }
        return normalized;
    }

    async onReady() {
        await this.ensureBaseObjects();
        await this.setStateAsync('info.connection', false, true);
        await this.setStateAsync('info.lastError', '', true);
        await this.setStateAsync('info.lastWriteDebug', '{}', true);
        await this.setStateAsync('info.rateLimitedUntil', '', true);

        this.correctedApiUrl = this.normalizeApiUrl(this.config.apiUrl);
        if (String(this.config.apiUrl || '').trim() !== this.correctedApiUrl) {
            this.log.warn(`Normalized API URL from "${this.config.apiUrl || ''}" to "${this.correctedApiUrl}"`);
        }

        if (!this.config.username || !this.config.password) {
            const msg = 'Missing username or password in adapter config';
            await this.failConnection(msg);
            return;
        }

        await this.subscribeStatesAsync('commands.refresh');
        await this.subscribeStatesAsync('commands.invoke');
        await this.subscribeStatesAsync('devices.*.controls.*');

        await this.scheduleSync({ full: true, reason: 'startup' });
        this.startPolling();
    }

    async ensureBaseObjects() {
        await this.setObjectNotExistsAsync('commands.refresh', {
            type: 'state',
            common: { name: 'Trigger immediate refresh', type: 'boolean', role: 'button', read: false, write: true, def: false },
            native: {},
        });
        await this.setObjectNotExistsAsync('commands.invoke', {
            type: 'state',
            common: { name: 'Invoke device method via JSON payload', type: 'string', role: 'json', read: true, write: true, def: '' },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.connection', {
            type: 'state',
            common: { name: 'Connected to device or service', type: 'boolean', role: 'indicator.connected', read: true, write: false, def: false },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.lastError', {
            type: 'state',
            common: { name: 'Last adapter error', type: 'string', role: 'text', read: true, write: false, def: '' },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.rateLimitedUntil', {
            type: 'state',
            common: { name: 'Rate limit cooldown end', type: 'string', role: 'date', read: true, write: false, def: '' },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.lastWriteDebug', {
            type: 'state',
            common: { name: 'Last control/invoke debug payload', type: 'string', role: 'json', read: true, write: false, def: '{}' },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.lastSync', {
            type: 'state',
            common: { name: 'Last successful sync time', type: 'string', role: 'date', read: true, write: false, def: '' },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.deviceCount', {
            type: 'state',
            common: { name: 'Discovered device count', type: 'number', role: 'value', read: true, write: false, def: 0 },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.availableMethods', {
            type: 'state',
            common: { name: 'All write methods from Python bridge', type: 'string', role: 'json', read: true, write: false, def: '[]' },
            native: {},
        });
        await this.setObjectNotExistsAsync('info.availableControls', {
            type: 'state',
            common: { name: 'All writable controls from Python bridge', type: 'string', role: 'json', read: true, write: false, def: '[]' },
            native: {},
        });
    }

    startPolling() {
        const intervalSec = Math.max(Number(this.config.pollInterval) || 180, 30);
        this.pollTimer = this.setInterval(() => {
            this.scheduleSync({ full: false, reason: 'poll' }).catch(err => this.log.error(`Polling failed: ${err.message}`));
        }, intervalSec * 1000);
    }

    async scheduleSync({ full = false, reason = 'manual', delayMs = 0 } = {}) {
        if (delayMs > 0) await this.delay(delayMs);
        if (this.syncInProgress) {
            this.pendingRefresh = this.pendingRefresh || full;
            this.log.debug(`Sync already running, queued refresh (${reason})`);
            return;
        }
        await this.performSync({ full, reason });
        if (this.pendingRefresh) {
            const queuedFull = this.pendingRefresh;
            this.pendingRefresh = false;
            await this.performSync({ full: Boolean(queuedFull), reason: 'queued' });
        }
    }

    async performSync({ full = false, reason = 'manual' } = {}) {
        await this.clearRateLimitCooldownIfExpired();
        if (this.rateLimitedUntil && Date.now() < this.rateLimitedUntil) {
            this.log.debug(`Skipping sync during cooldown until ${new Date(this.rateLimitedUntil).toISOString()} (${reason})`);
            return;
        }
        this.syncInProgress = true;
        try {
            this.pollCounter += 1;
            const fullEvery = Math.max(Number(this.config.fullStateRefreshEvery) || 10, 1);
            const doFull = full || this.pollCounter === 1 || (this.pollCounter % fullEvery === 0);
            this.log.debug(`Starting ${doFull ? 'full' : 'incremental'} sync (${reason})`);

            const discovery = await this.runBridge('discover');
            if (!discovery.ok) throw new Error(discovery.error || 'Discovery failed');

            let devices = Array.isArray(discovery.devices) ? discovery.devices : [];
            if (this.config.gatewayId) {
                devices = devices.filter(d => String(d.gateway) === String(this.config.gatewayId));
            }
            if (!devices.length) throw new Error('No matching devices discovered');

            const allMethods = new Set();
            const allControls = [];
            for (const device of devices) {
                await this.ensureDeviceObjects(device);
                await this.setStateAsync(`devices.${device.gateway}.info.discovery_raw`, JSON.stringify(device.raw || {}, null, 2), true);
                const doEnergy = this.shouldFetchEnergy();
                const state = await this.runBridge('state', String(device.gateway), doEnergy ? ['--energy'] : []);
                if (!state.ok) throw new Error(state.error || `State update failed for gateway ${device.gateway}`);
                await this.syncDeviceState(String(device.gateway), state);
                await this.syncEstimatedPower(String(device.gateway), state.values || {});
                if (doEnergy) await this.syncEnergy(String(device.gateway), state.energy);
                for (const method of state.methods || []) allMethods.add(method);
                for (const control of state.controls || []) {
                    allControls.push({ gateway: String(device.gateway), ...control });
                }
            }

            await this.setStateAsync('info.availableMethods', JSON.stringify([...allMethods].sort(), null, 2), true);
            await this.setStateAsync('info.availableControls', JSON.stringify(allControls, null, 2), true);
            await this.setStateAsync('info.connection', true, true);
            await this.setStateAsync('info.lastError', '', true);
        await this.setStateAsync('info.lastWriteDebug', '{}', true);
            await this.setStateAsync('info.lastSync', new Date().toISOString(), true);
            await this.setStateAsync('info.deviceCount', devices.length, true);
        } catch (error) {
            if (this.isRateLimitError(error.message)) {
                await this.enterRateLimitCooldown(error.message);
                return;
            }
            await this.failConnection(error.message);
            throw error;
        } finally {
            this.syncInProgress = false;
        }
    }


    // ------------------------------------------------------------------
    // Energy metering (consSequencesApi8 via python bridge --energy)
    // ------------------------------------------------------------------

    static get ENERGY_STATES() {
        const kwh = (name) => ({ type: 'number', role: 'value.energy', unit: 'kWh', name });
        const json = (name) => ({ type: 'string', role: 'json', name });
        return {
            current_hour_kwh: kwh('Consumption of the running hour'),
            last_full_hour_kwh: kwh('Consumption of the last completed hour'),
            last_24h_kwh: kwh('Consumption of the last 24 hours (rolling)'),
            today_kwh: kwh('Consumption since local midnight'),
            yesterday_kwh: kwh('Consumption of the previous day'),
            last_7_days_kwh: kwh('Consumption of the last 7 days'),
            last_30_days_kwh: kwh('Consumption of the last ~30 days'),
            last_month_kwh: kwh('Consumption of the last completed month'),
            last_12_months_kwh: kwh('Consumption of the last 12 months'),
            total_kwh: kwh('Total consumption reported by the cloud'),
            measured_power_w: { type: 'number', role: 'value.power', unit: 'W', name: 'Average power since previous energy poll (measured)' },
            estimated_power_w: { type: 'number', role: 'value.power', unit: 'W', name: 'Estimated power (is_heating x nominal power)' },
            hourly_series: json('Hourly buckets of the last 24 h (kWh, oldest first)'),
            daily7_series: json('Daily buckets of the last 7 days (kWh, oldest first)'),
            daily30_series: json('Daily buckets of the last ~30 days (kWh, oldest first)'),
            monthly_series: json('Monthly buckets (kWh, oldest first)'),
            raw_sequences: json('Raw consSequencesApi8 response'),
            last_update: { type: 'string', role: 'date', name: 'Timestamp of the last successful energy poll' },
            last_error: { type: 'string', role: 'text', name: 'Last energy error' },
        };
    }

    shouldFetchEnergy() {
        if (this.config.energyEnabled === false) return false;
        const every = Math.max(Number(this.config.energyEveryN) || 3, 1);
        return this.pollCounter === 1 || this.pollCounter % every === 0;
    }

    async putEnergy(gateway, key, value) {
        const def = AristonRemoteThermoAiAdapter.ENERGY_STATES[key];
        const id = `devices.${gateway}.energy.${key}`;
        if (!this.energyObjectsCreated?.has(id)) {
            await this.setObjectNotExistsAsync(`devices.${gateway}.energy`, {
                type: 'channel', common: { name: 'Energy' }, native: {},
            });
            await this.setObjectNotExistsAsync(id, {
                type: 'state',
                common: { ...def, read: true, write: false },
                native: {},
            });
            (this.energyObjectsCreated ||= new Set()).add(id);
        }
        if (value === undefined) return;
        await this.setStateAsync(id, def.type === 'string' && typeof value !== 'string' ? JSON.stringify(value) : value, true);
    }

    async syncEstimatedPower(gateway, values) {
        const nominal = Math.max(Number(this.config.nominalPowerW) || 1500, 0);
        const heating = values.is_heating === true || values.is_heating === 1;
        await this.putEnergy(gateway, 'estimated_power_w', heating ? nominal : 0);
    }

    async syncEnergy(gateway, energy) {
        if (!energy || !energy.ok) {
            const msg = energy?.error || 'no energy payload';
            this.log.warn(`Energy poll for ${gateway} failed: ${msg}`);
            await this.putEnergy(gateway, 'last_error', msg);
            return;
        }
        for (const key of ['current_hour_kwh', 'last_full_hour_kwh', 'last_24h_kwh', 'today_kwh', 'yesterday_kwh',
            'last_7_days_kwh', 'last_30_days_kwh', 'last_month_kwh', 'last_12_months_kwh', 'total_kwh']) {
            if (energy[key] !== null && energy[key] !== undefined) await this.putEnergy(gateway, key, energy[key]);
        }
        const s = energy.series || {};
        await this.putEnergy(gateway, 'hourly_series', s.hourly || []);
        await this.putEnergy(gateway, 'daily7_series', s.daily7 || []);
        await this.putEnergy(gateway, 'daily30_series', s.daily30 || []);
        await this.putEnergy(gateway, 'monthly_series', s.monthly || []);
        await this.putEnergy(gateway, 'raw_sequences', energy.raw_sequences || []);
        await this.putEnergy(gateway, 'last_update', energy.fetched_at || new Date().toISOString());
        await this.putEnergy(gateway, 'last_error', '');

        // Measured average power from the kWh increase of "today" between two energy polls.
        const now = Date.now();
        const day = new Date().toDateString();
        const today = Number(energy.today_kwh);
        this.lastEnergySample ||= {};
        const prev = this.lastEnergySample[gateway];
        if (prev && prev.day === day && Number.isFinite(today) && today >= prev.kwh) {
            const dtHours = (now - prev.ts) / 3600000;
            if (dtHours > 0.01) {
                await this.putEnergy(gateway, 'measured_power_w', Math.round(((today - prev.kwh) / dtHours) * 1000));
            }
        }
        if (Number.isFinite(today)) this.lastEnergySample[gateway] = { ts: now, kwh: today, day };
        this.log.debug(`Energy ${gateway}: today=${energy.today_kwh} 24h=${energy.last_24h_kwh} total=${energy.total_kwh}`);
    }

    isRateLimitError(message) {
        const msg = String(message || '').toLowerCase();
        return msg.includes('requests are blocked') || msg.includes('http 429') || msg.includes('(429') || msg.includes('too many requests');
    }

    getCooldownMs() {
        return Math.max(Number(this.config.rateLimitCooldownSec) || 900, 60) * 1000;
    }

    async enterRateLimitCooldown(message) {
        const until = Date.now() + this.getCooldownMs();
        if (until > this.rateLimitedUntil) {
            this.rateLimitedUntil = until;
        }
        const untilIso = new Date(this.rateLimitedUntil).toISOString();
        const fullMessage = `${message}. Cooling down until ${untilIso}`;
        this.log.warn(fullMessage);
        await this.setStateAsync('info.lastError', fullMessage, true);
        await this.setStateAsync('info.rateLimitedUntil', untilIso, true);
    }

    async clearRateLimitCooldownIfExpired() {
        if (this.rateLimitedUntil && Date.now() >= this.rateLimitedUntil) {
            this.rateLimitedUntil = 0;
            await this.setStateAsync('info.rateLimitedUntil', '', true);
        }
    }

    async failConnection(message) {
        this.log.error(message);
        await this.setStateAsync('info.connection', false, true);
        await this.setStateAsync('info.lastError', String(message), true);
    }

    async ensureDeviceObjects(device) {
        const gateway = String(device.gateway);
        await this.setObjectNotExistsAsync(`devices.${gateway}`, {
            type: 'device',
            common: { name: device.name || gateway },
            native: { gateway, serial: device.serial || '', raw: device.raw || {} },
        });
        for (const channel of ['info', 'meta', 'values', 'controls', 'raw']) {
            await this.setObjectNotExistsAsync(`devices.${gateway}.${channel}`, {
                type: 'channel',
                common: { name: channel.charAt(0).toUpperCase() + channel.slice(1) },
                native: {},
            });
        }
        await this.setObjectNotExistsAsync(`devices.${gateway}.info.discovery_raw`, {
            type: 'state',
            common: { name: 'Discovery payload', type: 'string', role: 'json', read: true, write: false, def: '{}' },
            native: {},
        });
        await this.setObjectNotExistsAsync(`devices.${gateway}.raw.full_state`, {
            type: 'state',
            common: { name: 'Full raw state payload', type: 'string', role: 'json', read: true, write: false, def: '{}' },
            native: {},
        });
    }

    normalizeIdPart(part) {
        return String(part)
            .replace(/[^a-zA-Z0-9_]/g, '_')
            .replace(/_+/g, '_')
            .replace(/^_+|_+$/g, '')
            .toLowerCase();
    }

    inferCommon(key, value, unit, writable = false) {
        let type = 'string';
        let role = writable ? 'state' : 'text';
        if (typeof value === 'number') {
            if (/(^|_)(anti_cooling|night_mode|permanent_boost|water_anti_leg|is_heating)(_|$)/i.test(key) && (value === 0 || value === 1)) {
                type = 'boolean';
                role = writable ? 'switch.enable' : 'indicator';
            } else {
                type = 'number';
                role = /temp/i.test(key) ? 'value.temperature' : 'value';
            }
        } else if (typeof value === 'boolean') {
            type = 'boolean';
            role = writable ? 'switch.enable' : 'indicator';
        }
        if (/mode_name$/i.test(key)) role = 'text';
        if (/hhmm$/i.test(key)) { type = 'string'; role = 'text'; }
        const common = { name: key, type, role, read: true, write: writable };
        if (unit) common.unit = String(unit);
        return common;
    }

    buildControlCommon(control) {
        const common = {
            name: control.name || control.id,
            type: control.type || 'string',
            role: control.role || 'state',
            read: true,
            write: true,
        };
        if (control.unit) common.unit = control.unit;
        if (typeof control.min === 'number' && typeof control.max === 'number') {
            common.min = Math.min(control.min, control.max);
            common.max = Math.max(control.min, control.max);
        } else {
            if (typeof control.min === 'number') common.min = control.min;
            if (typeof control.max === 'number') common.max = control.max;
        }
        if (Array.isArray(control.states) && control.states.length) {
            common.states = Object.fromEntries(control.states.map(v => [v, v]));
        }
        if (control.id === 'mode') common.role = 'level.mode';
        return common;
    }

    async ensureStateObject(id, common, native = {}) {
        const existing = await this.getObjectAsync(id);
        if (!existing) {
            await this.setObjectNotExistsAsync(id, { type: 'state', common, native });
            return;
        }
        const merged = {
            ...existing,
            common: { ...existing.common, ...common },
            native: { ...existing.native, ...native },
        };
        await this.extendObjectAsync(id, merged);
    }

    serializeValue(value) {
        if (value === null || value === undefined) return null;
        if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value;
        return JSON.stringify(value);
    }

    async syncDeviceState(gateway, payload) {
        const values = payload.values || {};
        const units = payload.units || {};
        const meta = payload.device || {};
        const controls = payload.controls || [];
        const raw = payload.raw || {};

        await this.setStateAsync(`devices.${gateway}.raw.full_state`, JSON.stringify(payload, null, 2), true);

        for (const [key, value] of Object.entries(meta)) {
            const id = `devices.${gateway}.meta.${this.normalizeIdPart(key)}`;
            await this.ensureStateObject(id, this.inferCommon(key, value), { originalKey: key, section: 'meta' });
            await this.setStateAsync(id, this.serializeValue(value), true);
        }

        for (const [key, value] of Object.entries(values)) {
            const id = `devices.${gateway}.values.${this.normalizeIdPart(key)}`;
            await this.ensureStateObject(id, this.inferCommon(key, value, units[key]), { originalKey: key, section: 'values' });
            await this.setStateAsync(id, this.serializeValue(value), true);
        }

        for (const control of controls) {
            const id = `devices.${gateway}.controls.${this.normalizeIdPart(control.id)}`;
            await this.ensureStateObject(id, this.buildControlCommon(control), { ...control, gateway });
            if (control.current !== undefined) {
                await this.setStateAsync(id, this.serializeValue(control.current), true);
            }
        }
    }

    bridgeArgs(command, gatewayId = '') {
        const args = [
            path.join(__dirname, 'lib', 'python-bridge.py'),
            command,
            '--username', String(this.config.username),
            '--password', String(this.config.password),
            '--api-url', String(this.correctedApiUrl || this.normalizeApiUrl(this.config.apiUrl)),
            '--user-agent', String(this.config.userAgent || ''),
            '--install-strategy', String(this.config.installStrategy || 'auto'),
        ];
        const gw = gatewayId || this.config.gatewayId || '';
        if (gw) args.push('--gateway-id', String(gw));
        if (this.config.metric !== false) args.push('--metric');
        return args;
    }

    runBridge(command, gatewayId = '', extraArgs = []) {
        return new Promise((resolve, reject) => {
            const args = this.bridgeArgs(command, gatewayId).concat(extraArgs);
            const proc = spawn(this.config.pythonBin || 'python3', args, { cwd: __dirname, env: { ...process.env, PYTHONUNBUFFERED: '1' } });
            let stdout = '';
            let stderr = '';
            proc.stdout.on('data', chunk => (stdout += chunk.toString()));
            proc.stderr.on('data', chunk => (stderr += chunk.toString()));
            proc.on('error', reject);
            proc.on('close', code => {
                if (stderr.trim()) this.log.warn(`python stderr: ${stderr.trim()}`);
                const rawStdout = stdout.trim();
                let parsed = null;
                if (rawStdout) {
                    try {
                        parsed = JSON.parse(rawStdout);
                    } catch (err) {
                        return reject(new Error(`Invalid JSON from python bridge (exit ${code}): ${rawStdout || stderr}`));
                    }
                }
                if (code !== 0) {
                    const detail = parsed?.error || stderr.trim() || rawStdout || `Python bridge exited with ${code}`;
                    return reject(new Error(detail));
                }
                resolve(parsed || { ok: true });
            });
        });
    }

    async executeControl(native, value, gatewayId) {
        const result = await this.runBridge('control', gatewayId, [
            '--control-id', String(native.id),
            '--value', value === null || value === undefined ? '' : String(value),
        ]);
        if (!result.ok) throw new Error(result.error || `Control ${native.id} failed`);
        return result;
    }

    async executeInvoke(payload) {
        const result = await this.runBridge('invoke', payload.gatewayId || '', [
            '--method', String(payload.method),
            '--invoke-args', JSON.stringify(Array.isArray(payload.args) ? payload.args : []),
        ]);
        if (!result.ok) throw new Error(result.error || 'Invoke failed');
        return result;
    }

    async markControlAccepted(relativeId, value) {
        await this.setStateAsync(relativeId, value, true);
    }

    async writeDebug(kind, payload) {
        try {
            const body = { timestamp: new Date().toISOString(), kind, payload };
            await this.setStateAsync('info.lastWriteDebug', JSON.stringify(body, null, 2), true);
            this.log.info(`${kind} debug: ${JSON.stringify(payload)}`);
        } catch (err) {
            this.log.warn(`Failed to persist debug payload: ${err.message}`);
        }
    }

    async schedulePostControlRefresh(controlId) {
        const delaySec = Math.max(Number(this.config.controlRefreshDelay) || 6, 1);
        await this.scheduleSync({ full: false, reason: `control:${controlId}:fast`, delayMs: delaySec * 1000 });
        await this.scheduleSync({ full: true, reason: `control:${controlId}:verify`, delayMs: Math.max(delaySec + 6, 10) * 1000 });
    }

    async onStateChange(id, state) {
        if (!state || state.ack) return;
        await this.clearRateLimitCooldownIfExpired();
        if (this.rateLimitedUntil && Date.now() < this.rateLimitedUntil) {
            const untilIso = new Date(this.rateLimitedUntil).toISOString();
            const msg = `Requests are blocked. Adapter is cooling down until ${untilIso}`;
            this.log.warn(msg);
            await this.setStateAsync('info.lastError', msg, true);
            await this.writeDebug('control-skipped-rate-limit', { stateId: id, until: untilIso, requestedValue: state.val });
            return;
        }
        try {
            if (id === `${this.namespace}.commands.refresh`) {
                await this.setStateAsync('commands.refresh', false, true);
                await this.scheduleSync({ full: true, reason: 'manual-refresh' });
                return;
            }
            if (id === `${this.namespace}.commands.invoke`) {
                const payload = JSON.parse(String(state.val || '{}'));
                if (!payload.method) throw new Error('commands.invoke requires JSON with field "method"');
                const invokeResult = await this.executeInvoke(payload);
                await this.writeDebug('invoke-success', invokeResult);
                await this.setStateAsync('commands.invoke', '', true);
                await this.scheduleSync({
                    full: true,
                    reason: `invoke:${payload.method}`,
                    delayMs: Math.max(Number(this.config.controlRefreshDelay) || 6, 1) * 1000,
                });
                return;
            }

            const relativeId = id.replace(`${this.namespace}.`, '');
            if (!relativeId.includes('.controls.')) return;
            const obj = await this.getObjectAsync(relativeId);
            if (!obj?.native?.id) throw new Error(`Missing native control mapping for ${relativeId}`);
            const gatewayId = obj.native.gateway || relativeId.split('.')[1] || this.config.gatewayId || '';
            const result = await this.executeControl(obj.native, state.val, gatewayId);
            this.log.info(`Control ${obj.native.id} accepted for gateway ${gatewayId}: ${JSON.stringify(result.args || [state.val])}`);
            await this.writeDebug('control-success', { gatewayId, controlId: obj.native.id, requestedValue: state.val, result });
            await this.markControlAccepted(relativeId, state.val);
            await this.schedulePostControlRefresh(obj.native.id);
        } catch (error) {
            if (this.isRateLimitError(error.message)) {
                await this.enterRateLimitCooldown(error.message);
                await this.writeDebug('control-rate-limited', { stateId: id, message: String(error.message), requestedValue: state.val });
                return;
            }
            this.log.error(`State change handling failed: ${error.message}`);
            await this.setStateAsync('info.lastError', String(error.message), true);
            await this.writeDebug('control-error', { stateId: id, message: String(error.message), stack: error.stack || '' });
            try {
                const relativeId = id.replace(`${this.namespace}.`, '');
                const obj = await this.getObjectAsync(relativeId);
                if (obj?.native?.current !== undefined) {
                    await this.setStateAsync(relativeId, this.serializeValue(obj.native.current), true);
                }
            } catch {
                // ignore rollback failures
            }
        }
    }

    delay(ms) {
        return new Promise(resolve => this.setTimeout(resolve, ms));
    }

    async onUnload(callback) {
        try {
            if (this.pollTimer) this.clearInterval(this.pollTimer);
            await this.setStateAsync('info.connection', false, true);
            callback();
        } catch {
            callback();
        }
    }
}

if (require.main !== module) {
    module.exports = options => new AristonRemoteThermoAiAdapter(options);
} else {
    new AristonRemoteThermoAiAdapter();
}
