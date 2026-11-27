// Browser roles of the e2e setup (Method → test infrastructure, browser test
// setup) and per-test evidence. Several roles can be used in one test; each
// gets its own browser context, video and trace.
import { test as base, devices, type BrowserContext, type Page } from '@playwright/test';
import { sim } from './sim';

type Roles = {
    /** Guest at the booth: Acer Switch 3 screen (1920×1200), kiosk page. */
    booth: Page;
    /** Staff setting up, desktop browser. */
    admin: Page;
    /** Operator phone (360×800, touch). */
    operator: Page;
    /** A second operator phone, for two-phone decisions. */
    operator2: Page;
    /** A booth-sized page opened earlier and used later (stale page). */
    stale: Page;
};

const roleOptions = {
    booth: { viewport: { width: 1920, height: 1200 }, hasTouch: true },
    admin: { viewport: { width: 1440, height: 900 } },
    operator: { ...devices['Pixel 5'], viewport: { width: 360, height: 800 }, defaultBrowserType: undefined },
    operator2: { ...devices['Pixel 5'], viewport: { width: 360, height: 800 }, defaultBrowserType: undefined },
    stale: { viewport: { width: 1920, height: 1200 }, hasTouch: true },
} as const;

function role(name: keyof Roles) {
    return async ({ browser }, use, testInfo) => {
        const { defaultBrowserType: _ignored, ...opts } = roleOptions[name] as any;
        const context: BrowserContext = await browser.newContext({
            ...opts,
            baseURL: testInfo.project.use.baseURL,
            locale: 'de-DE',
            recordVideo: { dir: testInfo.outputPath('video-' + name), size: opts.viewport },
        });
        // The trace comes from the config (use.trace) and covers every role's
        // context; videos are recorded here, one per role.
        const page = await context.newPage();
        await use(page);
        await testInfo.attach(`${name}-final.png`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' }).catch(() => {});
        // With use.trace on, Playwright may already have collected this
        // context's trace chunk when the test failed; closing then reports a
        // missing temp file, which is not a test result.
        await context.close().catch((e) => console.warn(`closing ${name} context: ${e.message}`));
    };
}

export const test = base.extend<Roles & { rig: void }>({
    // Every test starts on a clean rig and leaves its evidence attached.
    rig: [
        async ({}, use, testInfo) => {
            await sim.reset();
            await use();
            const state = await sim.state();
            await testInfo.attach('simulator-state.json', { body: JSON.stringify(state, null, 1), contentType: 'application/json' });
            await testInfo.attach('cups-error_log.txt', { body: await sim.errorLog(), contentType: 'text/plain' });
            await testInfo.attach('cups-status.json', { body: JSON.stringify(await sim.cupsStatus(), null, 1), contentType: 'application/json' });
            if (state.violations.length) {
                throw new Error('simulator recorded violations: ' + JSON.stringify(state.violations));
            }
        },
        { auto: true },
    ],
    booth: role('booth'),
    admin: role('admin'),
    operator: role('operator'),
    operator2: role('operator2'),
    stale: role('stale'),
});

/** Screenshot of a role's page as a named step, kept as evidence. */
export async function snap(page: Page, name: string) {
    const info = test.info();
    await info.attach(name, { body: await page.screenshot(), contentType: 'image/png' });
}

export { expect } from '@playwright/test';
