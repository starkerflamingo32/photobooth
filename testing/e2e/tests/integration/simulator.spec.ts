// Positive controls of the virtual test setup itself (FSD §17.1): the
// simulated SELPHY, driven through real CUPS, must behave as the real printer
// did in P-1/B-1 before any booth test may rely on it. Plan: T-17.1-a…j.
import { test, expect } from '../../lib/fixtures';
import { sim, type BackendRun } from '../../lib/sim';

const lines = (run: BackendRun) => run.messages.map((m) => m.line);
const stateMessages = (run: BackendRun) =>
    [...new Set(lines(run).filter((l) => l.startsWith('INFO: Printer state:') || l.startsWith('INFO: Print complete')))];

async function printOne() {
    const lp = await sim.lp();
    expect(lp.exit, lp.stderr).toBe(0);
    const job = Number(/selphy-(\d+)/.exec(lp.stdout)?.[1]);
    expect(job).toBeGreaterThan(0);
    return job;
}

const lastRunEnded = (s) => s.backend_runs.length > 0 && s.backend_runs.at(-1).exit !== null;

/** After a backend stop: CUPS has processed it and reports the queue stopped. */
async function queueStopped() {
    await sim.waitFor(lastRunEnded, 'backend exit');
    await expect.poll(async () => (await sim.cupsStatus()).enabled, { timeout: 10_000 }).toBe(false);
    return sim.state();
}

test('T-17.1-a normal print with FAST_RETURN=0 reports the B-1 stage sequence', async () => {
    await sim.setFastReturn(0);
    await sim.scenario('normal');
    const job = await printOne();
    const s = await sim.waitFor((s) => lastRunEnded(s) && s.state === 'idle', 'print complete');
    const run = s.backend_runs.at(-1)!;
    expect(run.cups_job).toBe(job);
    expect(run.fast_return).toBe(false);
    expect(run.exit).toBe(0);
    expect(stateMessages(run)).toEqual([
        'INFO: Printer state: Feeding Paper',
        'INFO: Printer state: Printing YELLOW',
        'INFO: Printer state: Printing MAGENTA',
        'INFO: Printer state: Printing CYAN',
        'INFO: Printer state: Printing LAMINATE',
        'INFO: Print complete (0 copies remaining)',
    ]);
    expect(s.outputs.map((o) => o.cups_job)).toEqual([job]);
    // CUPS marks the job completed shortly after the backend exits.
    await expect.poll(async () => (await sim.cupsStatus()).jobs_not_completed.trim(), { timeout: 10_000 }).toBe('');
});

test('T-17.1-b without FAST_RETURN=0 the backend leaves at Printing YELLOW (P-1 job 118)', async () => {
    await sim.setFastReturn(null);
    await sim.scenario('normal');
    await printOne();
    const s = await sim.waitFor((s) => lastRunEnded(s), 'backend exit');
    const run = s.backend_runs.at(-1)!;
    expect(run.fast_return).toBe(true);
    expect(lines(run)).toContain('INFO: Fast return mode enabled.');
    expect(stateMessages(run).at(-2)).toBe('INFO: Printer state: Printing YELLOW');
    // The printer goes on alone: the photo still comes out, CUPS never sees it.
    expect(s.state).not.toBe('idle');
    await sim.waitFor((s) => s.outputs.length === 1 && s.state === 'idle', 'photo out after fast return');
});

for (const [scenario, code, text] of [
    ['empty-tray', '02', 'Paper Feed (02)'],
    ['tray-missing', '03', 'No Paper (03)'],
    ['no-ink', '07', 'No Ink (07)'],
] as const) {
    test(`T-17.1-c ${scenario}: ${text} stops the queue, job pending, printer prints the held photo after the fix`, async () => {
        await sim.setFastReturn(0);
        await sim.scenario(scenario);
        const job = await printOne();
        let s = await queueStopped();
        const run = s.backend_runs.at(-1)!;
        expect(run.exit).toBe(4); // CUPS_BACKEND_STOP
        expect(lines(run)).toContain(`ERROR: Printer error: ${text}`);
        expect(s.error?.code).toBe(code);
        expect(s.held?.cups_job).toBe(job);
        const status = await sim.cupsStatus();
        expect(status.enabled).toBe(false);
        expect(status.printer).toContain(`Printer error: ${text}`);
        expect(status.jobs_not_completed).toContain(`selphy-${job}`);
        await sim.action('fix');
        s = await sim.waitFor((s) => s.outputs.length === 1 && s.state === 'idle', 'held photo printed by itself');
        expect(s.outputs[0].cups_job).toBe(job);
        // CUPS saw none of it: still pending in a stopped queue (R3).
        expect((await sim.cupsStatus()).jobs_not_completed).toContain(`selphy-${job}`);
    });
}

test('T-17.1-d cupsenable after the self-print resends the job: duplicate visible in the job log (R3, P-1 job 120)', async () => {
    await sim.setFastReturn(0);
    await sim.scenario('empty-tray');
    const job = await printOne();
    await queueStopped();
    await sim.action('fix');
    await sim.waitFor((s) => s.outputs.length === 1 && s.state === 'idle', 'self-print');
    await sim.enable();
    const s = await sim.waitFor((s) => s.backend_runs.length === 2 && lastRunEnded(s) && s.state === 'idle', 'resend done');
    expect(s.received.map((r) => r.cups_job)).toEqual([job, job]);
    expect(s.duplicates).toEqual([job]);
});

test('T-17.1-e ink sheet error (0c) during printing: fix alone does nothing, power cycle loses the photo (P-1 job 123)', async () => {
    await sim.setFastReturn(0);
    await sim.scenario('ink-sheet-0c');
    await printOne();
    let s = await queueStopped();
    expect(lines(s.backend_runs.at(-1)!)).toContain('ERROR: Printer error: Unknown Error (0c)');
    expect(lines(s.backend_runs.at(-1)!)).toContain('INFO: Printer state: Printing MAGENTA');
    await sim.action('fix');
    await new Promise((r) => setTimeout(r, 3000));
    s = await sim.state();
    expect(s.state).toBe('error');
    expect(s.outputs).toHaveLength(0);
    await sim.action('power_cycle');
    s = await sim.state();
    expect(s.state).toBe('idle');
    expect(s.held).toBeNull();
    expect(s.outputs).toHaveLength(0);
});

test('T-17.1-f incorrect media (0a): backend cancels the job, queue keeps running, nothing printed (R9)', async () => {
    await sim.setFastReturn(0);
    await sim.scenario('incorrect-media-0a');
    const job = await printOne();
    const s = await sim.waitFor(lastRunEnded, 'backend exit');
    expect(s.backend_runs.at(-1)!.exit).toBe(5); // CUPS_BACKEND_CANCEL
    expect(lines(s.backend_runs.at(-1)!)).toContain('ERROR: Printer error: Incorrect media for job (0a)');
    const status = await sim.cupsStatus();
    expect(status.enabled).toBe(true);
    expect(status.jobs_not_completed).not.toContain(`selphy-${job}`);
    expect(s.outputs).toHaveLength(0);
});

test('T-17.1-g cancelling a held job in CUPS does not remove it from the printer (R18, B-1 job 126)', async () => {
    await sim.setFastReturn(0);
    await sim.scenario('empty-tray');
    const job = await printOne();
    await queueStopped();
    await sim.cancel(job);
    await sim.action('fix');
    const s = await sim.waitFor((s) => s.outputs.length === 1 && s.state === 'idle', 'printer prints the cancelled job');
    expect(s.outputs[0].cups_job).toBe(job);
});

test('T-17.1-h printer status query: real -s output, printing then Idle after the fix (R14)', async () => {
    await sim.setFastReturn(0);
    await sim.scenario('empty-tray');
    await printOne();
    await queueStopped();
    let q = await sim.query();
    expect(q.exit).toBe(0);
    expect(q.lines).toContain('INFO: Printer state: Feeding Paper');
    expect(q.lines).toContain('INFO: Printer error: Paper Feed');
    await sim.action('fix');
    const seen = new Set<string>();
    const end = Date.now() + 30_000;
    while (Date.now() < end) {
        q = await sim.query();
        const st = q.lines.find((l: string) => l.startsWith('INFO: Printer state:'));
        if (st) seen.add(st);
        // The printer starts the held print a few (simulated) seconds after the fix.
        if (st === 'INFO: Printer state: Idle' && seen.has('INFO: Printer state: Printing LAMINATE')) break;
        await new Promise((r) => setTimeout(r, 100));
    }
    expect([...seen]).toContain('INFO: Printer state: Printing YELLOW');
    expect([...seen]).toContain('INFO: Printer state: Idle');
    expect((await sim.state()).outputs).toHaveLength(1);
    const s = await sim.state();
    expect(s.queries.every((x) => x.queue_enabled === false)).toBe(true);
});

test('T-17.1-i printer status query hangs at a layer change with libusb error -7 (R14)', async () => {
    await sim.setFastReturn(0);
    await sim.scenario('query-hangs');
    await printOne();
    await queueStopped();
    await sim.action('fix');
    await sim.waitFor((s) => s.state === 'printing', 'held print running');
    let hang: any = null;
    const end = Date.now() + 30_000;
    while (!hang && Date.now() < end) {
        const t0 = Date.now();
        const q = await sim.query();
        if (q.lines.some((l: string) => l.includes('libusb error -7'))) hang = { q, ms: Date.now() - t0 };
    }
    expect(hang, 'a query hung').not.toBeNull();
    expect(hang.q.exit).not.toBe(0);
    expect(hang.q.lines).toContain('ERROR: Unable to attach to printer!');
    expect(hang.ms).toBeGreaterThan(17_000 * Number(process.env.SIM_TIME_SCALE ?? '0.1') * 0.9);
});

test('T-17.1-j a printer status query while the queue is enabled is recorded as a violation (FR-9.11 instrument)', async () => {
    await sim.scenario('normal');
    await sim.query();
    const s = await sim.state();
    expect(s.violations.map((v) => v.what)).toContain('printer query while the queue is enabled (FR-9.11)');
    // Positive control done: clear it so the rig check passes.
    await sim.reset();
});
