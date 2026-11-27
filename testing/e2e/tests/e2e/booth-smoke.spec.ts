// The browser test setup works end to end against the virtual stack (§17.1.1).
import { test, expect, snap } from '../../lib/fixtures';
import { sim } from '../../lib/sim';
import { openBooth, takePhoto, printFromResult, stage } from '../../lib/booth';

test('T-17.1-k booth browser: take a photo and print it; exactly one job reaches the simulated SELPHY', async ({ booth }) => {
    await sim.scenario('normal');
    await openBooth(booth);
    await snap(booth, 'start');
    await takePhoto(booth);
    await snap(booth, 'result');
    const answer = await printFromResult(booth);
    expect(answer.status).toBe(200);
    expect(answer.body.status, JSON.stringify(answer.body)).not.toBe('error');
    const s = await sim.waitFor((s) => s.outputs.length === 1 && s.state === 'idle', 'photo out of the simulated SELPHY');
    await snap(booth, 'after-print');
    expect(s.received).toHaveLength(1);
    expect(s.duplicates).toEqual([]);
});

test('T-17.1-l browser roles: admin, operator phone and stale page open the booth in parallel', async ({ booth, admin, operator, stale }) => {
    await openBooth(stale);
    await openBooth(booth);
    await admin.goto('/admin/');
    await expect(admin.locator('form').first()).toBeVisible();
    await operator.goto('/login/');
    await expect(operator).toHaveURL(/login|admin|menu/);
    const width = await operator.evaluate(() => window.innerWidth);
    expect(width).toBe(360);
    expect(await operator.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
    await snap(admin, 'admin');
    await snap(operator, 'operator');
    await expect(stage(stale, 'start')).toHaveClass(/stage--active/);
});
