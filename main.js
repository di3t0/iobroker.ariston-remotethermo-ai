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

        this.correctedApiUrl = this.normalizeApiUrl(this.config.apiUrl);
        if (String(this.config.apiUrl || '').trim() !== this.correctedApiUrl) {
            this.log.warn(`Normalized API URL from "${this.config.apiUrl || ''}" to "${this.correctedApiUrl}"`);
        }

        if (!this.config.username || !this.config.password) {
            const msg = 'Missing username or password in adapter config';
            await this.failConnection(msg);
            return;
        }

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
                const state = await this.runBridge('state', String(device.gateway));
                if (!state.ok) throw new Error(state.error || `State update failed for gateway ${device.gateway}`);
                await this.syncDeviceState(String(device.gateway), state);
                for (const method of state.methods || []) allMethods.add(method);
                for (const control of state.controls || []) {
                    allControls.push({ gateway: String(device.gateway), ...control });
                }
            }

            await this.setStateAsync('info.availableMethods', JSON.stringify([...allMethods].sort(), null, 2), true);
            await this.setStateAsync('info.availableControls', JSON.stringify(allControls, null, 2), true);
            await this.setStateAsync('info.connection', true, true);
            await this.setStateAsync('info.lastError', '', true);
            await this.setStateAsync('info.lastSync', new Date().toISOString(), true);
            await this.setStateAsync('info.deviceCount', devices.length, true);
        } catch (error) {
            await this.failConnection(error.message);
            throw error;
        } finally {
            this.syncInProgress = false;
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
        for (const channel of ['info', 'meta', 'values', 'controls']) {
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
            type = 'number';
            role = /temp/i.test(key) ? 'value.temperature' : 'value';
        } else if (typeof value === 'boolean') {
            type = 'boolean';
            role = writable ? 'switch.enable' : 'indicator';
        }
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
        if (typeof control.min === 'number') common.min = control.min;
        if (typeof control.max === 'number') common.max = control.max;
        if (Array.isArray(control.states) && control.states.length) {
            common.states = Object.fromEntries(control.states.map(v => [v, v]));
        }
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

    async onStateChange(id, state) {
        if (!state || state.ack) return;
        try {
            if (id === `${this.namespace}.commands.refresh`) {
                await this.setStateAsync('commands.refresh', false, true);
                await this.scheduleSync({ full: true, reason: 'manual-refresh' });
                return;
            }
            if (id === `${this.namespace}.commands.invoke`) {
                const payload = JSON.parse(String(state.val || '{}'));
                if (!payload.method) throw new Error('commands.invoke requires JSON with field "method"');
                await this.executeInvoke(payload);
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
            await this.executeControl(obj.native, state.val, gatewayId);
            await this.scheduleSync({
                full: false,
                reason: `control:${obj.native.id}`,
                delayMs: Math.max(Number(this.config.controlRefreshDelay) || 6, 1) * 1000,
            });
        } catch (error) {
            this.log.error(`State change handling failed: ${error.message}`);
            await this.setStateAsync('info.lastError', String(error.message), true);
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
