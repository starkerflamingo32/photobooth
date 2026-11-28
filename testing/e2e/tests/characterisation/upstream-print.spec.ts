// FSD §17.5 upstream characterisation: print limit, auto-print and the
// per-session print rate limit, as upstream ships them. Characterisation only:
// each scenario records what happens as "answer" annotations (published by
// upstream-characterisation.yml); a test fails only if its instruments fail.
// Runs against whatever BOOTH_REF the stack was built from.
import { test, expect, snap } from '../../lib/fixtures';
import { sim, simMs } from '../../lib/sim';
import { adminReset, adminSet, openBooth, printFromResult, stage, takePhoto } from '../../lib/booth';
import type { Page } from '@playwright/test';

const RATE_WINDOW_MS = 61_000; // api/print.php: 10 prints per 60 s per session

function answer(text: string) {
    test.info().annotations.push({ type: 'answer', description: text });
}

type PrintResult = { n: number; ok: boolean; message: string };

async function overlayClosed(page: Page) {
    // The print overlay stays print.time (5 s) or shows an error for a while.
    await expect(page.locator('.overlay, .photobooth-overlay').first()).toBeHidden({ timeout: 20_000 }).catch(() => {});
}

/** Press print on the result screen n times, paced below the rate limit. */
async function printRepeatedly(page: Page, n: number, paced = true): Promise<PrintResult[]> {
    const out: PrintResult[] = [];
    let windowStart = Date.now();
    let inWindow = 0;
    for (let i = 1; i <= n; i++) {
        if (paced && inWindow === 10) {
            await page.waitForTimeout(Math.max(0, windowStart + RATE_WINDOW_MS - Date.now()));
            windowStart = Date.now();
            inWindow = 0;
        }
        // The result screen closes after a while (or after a refused print):
        // take a new photo then, as a guest would.
        const onResult = await stage(page, 'result').evaluate((el) => el.classList.contains('stage--active'));
        if (!onResult) {
            await openBooth(page);
            await takePhoto(page);
        }
        const r = await printFromResult(page);
        inWindow++;
        const body = r.body ?? {};
        const res = { n: i, ok: body.status !== 'error' && !body.error, message: String(body.error ?? body.msg ?? body.status ?? '') };
        out.push(res);
        test.info().annotations.push({ type: 'print', description: JSON.stringify(res) });
        await overlayClosed(page);
    }
    return out;
}

async function printDb(page: Page) {
    const res = await page.request.get('/api/serverInfo.php').catch(() => null);
    return res && res.ok() ? (await res.text()).slice(0, 2000) : '';
}

async function freshBooth(admin: Page, settings: Record<string, string | number | boolean>) {
    await adminReset(admin, { remove_print_db: true });
    await adminSet(admin, {
        'print[from_result]': true,
        'print[from_gallery]': true,
        'print[auto]': false,
        'print[max_multi]': 1,
        'button[show_printUnlock]': true,
        ...settings,
    });
    await sim.scenario('normal');
}

const summary = (rs: PrintResult[]) =>
    rs.map((r) => `${r.n}:${r.ok ? 'ok' : 'refused(' + r.message + ')'}`).join(' ');

test('T-17.5-a S1 limit 18, 18 prints, booth unlock button, print again (L-Q1)', async ({ booth, admin }) => {
    test.setTimeout(10 * 60_000);
    await freshBooth(admin, { 'print[limit]': 18 });
    await openBooth(booth);
    await takePhoto(booth);
    const first = await printRepeatedly(booth, 18);
    answer('S1 prints 1-18: ' + summary(first));
    const after = await printRepeatedly(booth, 1, false);
    answer('S1 print 19 before unlock: ' + summary(after));
    await openBooth(booth);
    const unlock = booth.locator('[data-command="print-unlock-button"]');
    answer('S1 unlock button visible on the start screen: ' + (await unlock.isVisible()));
    if (await unlock.isVisible()) {
        await unlock.click();
        await booth.waitForTimeout(2500);
    }
    await snap(booth, 'after-unlock');
    await takePhoto(booth);
    const post = await printRepeatedly(booth, 3, false);
    answer('S1 prints after unlock: ' + summary(post));
    const s = await sim.waitFor((s) => s.state === 'idle', 'printer idle', 300_000);
    answer(`S1 jobs received by the printer: ${s.received.length}, photos out: ${s.outputs.length}`);
});

test('T-17.5-b S2 limit 18, 18 prints, admin reset without and with "print database" (L-Q1)', async ({ booth, admin }) => {
    test.setTimeout(12 * 60_000);
    await freshBooth(admin, { 'print[limit]': 18 });
    await openBooth(booth);
    await takePhoto(booth);
    answer('S2 prints 1-18: ' + summary(await printRepeatedly(booth, 18)));
    await adminReset(admin, { remove_print_db: false });
    await openBooth(booth);
    await takePhoto(booth);
    answer('S2 after reset WITHOUT print database: ' + summary(await printRepeatedly(booth, 2, false)));
    await adminReset(admin, { remove_print_db: true });
    await openBooth(booth);
    await takePhoto(booth);
    answer('S2 after reset WITH print database: ' + summary(await printRepeatedly(booth, 2, false)));
});

test('T-17.5-c S3 limit 0, 20 prints; then a stale print.lock (L-Q2)', async ({ booth, admin }) => {
    test.setTimeout(12 * 60_000);
    await freshBooth(admin, { 'print[limit]': 0 });
    await openBooth(booth);
    await takePhoto(booth);
    const rs = await printRepeatedly(booth, 20);
    answer(`S3 limit 0, 20 paced prints: ${rs.filter((r) => r.ok).length} accepted; ` + summary(rs.filter((r) => !r.ok)));
    // Make a print.lock the way it arises: limit 1, one print locks the booth;
    // then back to limit 0 (the event configuration).
    await adminSet(admin, { 'print[limit]': 1 });
    await openBooth(booth);
    await takePhoto(booth);
    answer('S3 with limit 1: ' + summary(await printRepeatedly(booth, 2, false)));
    await adminSet(admin, { 'print[limit]': 0 });
    await openBooth(booth);
    await takePhoto(booth);
    answer('S3 limit back to 0 with the lock left: ' + summary(await printRepeatedly(booth, 2, false)));
});

test('T-17.5-d S4 auto-print with 2 s delay: reloads, stopped queue, running print (L-Q3)', async ({ booth, admin }) => {
    test.setTimeout(10 * 60_000);
    await freshBooth(admin, { 'print[limit]': 0, 'print[auto]': true, 'print[auto_delay]': 2000, 'filters[enabled]': false });
    // a) reload during and after the delay
    await openBooth(booth);
    await takePhoto(booth);
    await booth.waitForTimeout(800);
    await booth.reload();
    await booth.waitForTimeout(4000);
    await booth.reload();
    await booth.waitForTimeout(4000);
    let s = await sim.waitFor((s) => s.state === 'idle', 'idle', 120_000);
    answer(`S4a one photo, page reloaded 0.8 s into the 2 s delay and again later (reload returns to the start screen): jobs received ${s.received.length}`);
    // b) queue stopped (paper error on the next job)
    await sim.scenario('empty-tray');
    await openBooth(booth);
    await takePhoto(booth);
    await booth.waitForTimeout(4000);
    await expect.poll(async () => (await sim.cupsStatus()).enabled, { timeout: 60_000 }).toBe(false);
    await openBooth(booth);
    await takePhoto(booth);
    await booth.waitForTimeout(4000);
    await snap(booth, 'auto-print-queue-stopped');
    const st = await sim.cupsStatus();
    answer('S4b second auto-print while the queue is stopped: CUPS jobs not completed: ' + st.jobs_not_completed.trim().split('\n').length);
    // c) a print running. Real-time clock: at the default scale a whole print
    // (56 simulated s) fits into the waits below and the prints never overlap.
    await sim.reset();
    await sim.scenarioText('id: normal-real-time\nprinter: {tray: ok, ink: ok}\nclock: {scale: 1}\n');
    await openBooth(booth);
    await takePhoto(booth);
    await sim.waitFor((s) => s.received.length === 1, 'first auto-print at the printer', 30_000);
    await openBooth(booth);
    await takePhoto(booth);
    await booth.waitForTimeout(4000);
    s = await sim.state();
    // Precondition: the first print is still running when the second photo is done.
    expect(s.outputs.length, 'first print already finished; S4c would not test a running print').toBe(0);
    const pending = (await sim.cupsStatus()).jobs_not_completed.trim();
    answer(
        `S4c second photo with auto-print while the first print runs: printer state ${s.state}, ` +
            `jobs received ${s.received.length}, CUPS jobs not completed ${pending ? pending.split('\n').length : 0}`,
    );
    s = await sim.waitFor((s) => s.outputs.length === 2 && s.state === 'idle', 'both photos out', 150_000);
    answer(`S4c after the first print: photos out ${s.outputs.length}, jobs received ${s.received.length}`);
});

/**
 * Print the photo open in the gallery viewer with the given number of copies.
 * The viewer ignores the print button while the previous print's overlay is
 * up (print.time), so the button is pressed again until a request goes out.
 */
async function galleryPrint(page: Page, copies: number, giveUpMs = 20_000) {
    const end = Date.now() + giveUpMs;
    let presses = 0;
    while (Date.now() < end) {
        const ans = page.waitForResponse((r) => r.url().includes('/api/print.php'), { timeout: 1_500 }).catch(() => null);
        presses++;
        await page.locator('.pswp__button--print, .pswp__button--custom-print').first().click();
        const dialog = page.locator('dialog.dialog');
        if (await dialog.isVisible({ timeout: 500 }).catch(() => false)) {
            await dialog.locator('.form-input-copies').fill(String(copies));
            await dialog.locator('.dialog-buttonbar button').first().click();
        }
        const r = await ans;
        if (r) {
            const body = await r.json().catch(() => ({}));
            return { n: 0, presses, ok: body.status !== 'error' && !body.error, message: String(body.error ?? '') };
        }
    }
    throw new Error(`no print request after ${presses} presses in ${giveUpMs} ms`);
}

test('T-17.5-e S5 limit 0: 10 gallery reprints in quick succession; one request with 3 copies (L-Q4)', async ({ booth, admin }) => {
    test.setTimeout(8 * 60_000);
    await freshBooth(admin, { 'print[limit]': 0, 'print[max_multi]': 1 });
    await openBooth(booth);
    await takePhoto(booth);
    await booth.goto('/gallery/');
    await booth.locator('[data-pswp-width], .gallery__img, .gallery-list a').first().click();
    // Reprint as fast as the viewer allows, for longer than the 60 s window.
    const rs: PrintResult[] = [];
    const t0 = Date.now();
    while (Date.now() - t0 < 70_000) {
        const r = await galleryPrint(booth, 1);
        rs.push({ ...r, n: rs.length + 1 });
        test.info().annotations.push({ type: 'print', description: JSON.stringify({ ...r, n: rs.length, ms: Date.now() - t0 }) });
    }
    await snap(booth, 'gallery-after-reprints');
    answer(`S5 gallery reprints as fast as the viewer allows, ${Math.round((Date.now() - t0) / 1000)} s (same session): ` + summary(rs));
    const shown = await booth.locator('text=/Rate limit/i').first().isVisible().catch(() => false);
    answer('S5 the guest sees the English "Rate limit exceeded…" text: ' + shown);
    // One request with 3 copies (copies dialog needs print.max_multi > 1),
    // after the rate window has passed, with the default print command.
    await sim.waitFor((s) => s.state === 'idle', 'reprints done', 240_000);
    await booth.waitForTimeout(RATE_WINDOW_MS);
    await adminSet(admin, { 'print[max_multi]': 3 });
    await booth.goto('/gallery/');
    await booth.locator('[data-pswp-width], .gallery__img, .gallery-list a').first().click();
    const before = await sim.state();
    const three = await galleryPrint(booth, 3);
    await snap(booth, 'three-copies-answer');
    await booth.waitForTimeout(simMs(60) + 3000);
    const s = await sim.state();
    answer(
        `S5 one request with 3 copies (max_multi 3, default command "lp -o landscape -o fit-to-page %s"): ` +
            `${three.ok ? 'accepted' : 'refused(' + three.message + ')'}; jobs received by the printer: ` +
            `${s.received.length - before.received.length}`,
    );
});
