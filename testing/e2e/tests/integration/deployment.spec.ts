// The CUPS configuration photobooth-setup deploys, checked through a
// simulated print (FSD NFR-13.2). The rig reset restores the deployed
// cups-files.conf, so these tests see it as shipped. Plan: T-13-a.
import { test, expect } from '../../lib/fixtures';
import { sim } from '../../lib/sim';

test('T-13-a deployed CUPS runs the backend with FAST_RETURN=0 from cups-files.conf', async () => {
    await sim.scenario('normal');
    const lp = await sim.lp();
    expect(lp.exit, lp.stderr).toBe(0);
    const s = await sim.waitFor((s) => s.backend_runs.length > 0 && s.backend_runs.at(-1)!.exit !== null, 'backend exit');
    const run = s.backend_runs.at(-1)!;
    expect(run.fast_return_env).toBe('0');
    expect(run.fast_return).toBe(false);
    // CUPS 2.4 ignores SetEnv in cupsd.conf and only logs this hint.
    expect(await sim.errorLog()).not.toContain('to cups-files.conf');
});
