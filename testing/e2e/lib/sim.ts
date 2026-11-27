// Client for the simulated SELPHY's control API (testing/stack/cups-sim).
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from '@playwright/test';

export const SIM_URL = process.env.SIM_URL ?? 'http://cups:8631';
export const SCALE = Number(process.env.SIM_TIME_SCALE ?? '0.1');
const SCENARIO_DIR = process.env.SCENARIO_DIR ?? join(__dirname, '..', 'scenarios');

export type BackendRun = {
    run: number;
    cups_job: number;
    fast_return: boolean;
    exit: number | string | null;
    messages: { time: string; line: string }[];
};
export type SimState = {
    state: 'idle' | 'feeding' | 'printing' | 'error';
    state_line: string;
    error: { code: string; text: string } | null;
    held: { cups_job: number } | null;
    received: { cups_job: number }[];
    outputs: { cups_job: number; time: string }[];
    duplicates: number[];
    backend_runs: BackendRun[];
    queries: { time: string; result: string; state?: string; queue_enabled: boolean }[];
    violations: { time: string; what: string }[];
};

async function call(method: string, path: string, body?: unknown, raw = false): Promise<any> {
    const res = await fetch(SIM_URL + path, {
        method,
        body: body === undefined ? undefined : raw ? (body as string) : JSON.stringify(body),
        headers: { 'Content-Type': raw ? 'application/yaml' : 'application/json' },
    });
    const text = await res.text();
    if (!res.ok) {
        throw new Error(`simulator ${method} ${path}: HTTP ${res.status} ${text}`);
    }
    return res.headers.get('content-type')?.includes('json') ? JSON.parse(text) : text;
}

export const sim = {
    /** Clean rig: no CUPS jobs, queue enabled and accepting, default printer. */
    reset: async () => {
        const r = await call('POST', '/reset', {});
        expect(r.ok, 'simulator reset: ' + JSON.stringify(r.steps)).toBe(true);
    },
    /** Load a scenario file from testing/scenarios (printer part). */
    scenario: (name: string) => call('POST', '/scenario', readFileSync(join(SCENARIO_DIR, name + '.yaml'), 'utf8'), true),
    scenarioText: (yaml: string) => call('POST', '/scenario', yaml, true),
    action: (action: string, args: Record<string, unknown> = {}) => call('POST', '/action', { action, ...args }),
    state: (): Promise<SimState> => call('GET', '/state'),
    cupsStatus: () => call('GET', '/cups/status'),
    errorLog: (): Promise<string> => call('GET', '/cups/error_log'),
    lp: () => call('POST', '/cups/lp', {}),
    enable: () => call('POST', '/cups/enable', {}),
    cancel: (job?: number) => call('POST', '/cups/cancel', job ? { job } : {}),
    setFastReturn: (value: number | null) => call('POST', '/cups/fast-return', { value }),
    query: () => call('GET', '/query?caller=runner'),
    /** Poll the simulator until cond holds; fails with the last state. */
    waitFor: async (cond: (s: SimState) => boolean, what: string, timeoutMs = 60_000): Promise<SimState> => {
        const end = Date.now() + timeoutMs;
        let s = await sim.state();
        while (!cond(s)) {
            if (Date.now() > end) {
                throw new Error(`timeout waiting for: ${what}\nlast simulator state: ${JSON.stringify(s, null, 1).slice(0, 4000)}`);
            }
            await new Promise((r) => setTimeout(r, 250));
            s = await sim.state();
        }
        return s;
    },
};

/** Real milliseconds for simulated seconds. */
export const simMs = (seconds: number) => seconds * SCALE * 1000;
