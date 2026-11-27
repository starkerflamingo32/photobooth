import { defineConfig } from '@playwright/test';

// Suites of the virtual test setup (FSD §17.0/§17.1). Each project is one tier
// or suite; select with --project. Evidence per test: video, screenshots,
// trace, plus the simulator state and CUPS error_log attached by lib/fixtures.
export default defineConfig({
    testDir: './tests',
    outputDir: './test-results/artifacts',
    timeout: 180_000,
    expect: { timeout: 15_000 },
    workers: 1, // one booth, one simulated printer: tests share them
    retries: 0,
    reporter: [
        ['list'],
        ['html', { outputFolder: './test-results/report', open: 'never' }],
        ['json', { outputFile: './test-results/results.json' }],
    ],
    use: {
        baseURL: process.env.BOOTH_URL ?? 'http://host.docker.internal:18080',
        video: 'on',
        screenshot: 'on',
        trace: 'on',
        locale: 'de-DE',
    },
    projects: [
        { name: 'integration', testDir: './tests/integration' },
        { name: 'e2e', testDir: './tests/e2e' },
        { name: 'characterisation', testDir: './tests/characterisation' },
    ],
});
