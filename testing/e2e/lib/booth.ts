// Upstream Photobooth's guest and admin pages as a person uses them.
import { expect, type Page } from '@playwright/test';

export const stage = (page: Page, name: 'start' | 'loader' | 'result') =>
    page.locator(`.stage[data-stage="${name}"]`);

export async function openBooth(page: Page, path = '/') {
    await page.goto(path);
    await expect(stage(page, 'start')).toHaveClass(/stage--active/);
}

/** Start a photo from the start screen and wait for the result screen. */
export async function takePhoto(page: Page) {
    await page.locator('[data-command="takePic"]').first().click();
    // Countdown (picture.cntdwn_time, default 5 s), capture via the fake DSLR,
    // processing, FTP upload to web-ftp.
    await expect(stage(page, 'result')).toHaveClass(/stage--active/, { timeout: 60_000 });
}

/** Press the print button on the result screen. Returns the print API answer. */
export async function printFromResult(page: Page) {
    const answer = page.waitForResponse((r) => r.url().includes('/api/print.php'), { timeout: 30_000 });
    await stage(page, 'result').locator('[data-command="printbtn"]').click();
    const res = await answer;
    return { status: res.status(), body: await res.json().catch(async () => ({ raw: await res.text() })) };
}

/** Filenames of the photos in the booth (newest first), from the gallery API. */
export async function latestImage(page: Page): Promise<string> {
    const res = await page.request.get('/api/gallery.php');
    expect(res.ok()).toBe(true);
    const body = await res.json().catch(() => null);
    const names: string[] = JSON.stringify(body ?? '').match(/[A-Za-z0-9_-]+\.jpg/g) ?? [];
    expect(names.length, 'a photo exists').toBeGreaterThan(0);
    return names[0];
}

/**
 * Change admin panel settings the way staff do: open /admin/, set the fields,
 * press save. Fields are set through the form element (some live in collapsed
 * sections or other view modes); the save goes through the panel's own button.
 */
export async function adminSet(page: Page, values: Record<string, string | number | boolean>) {
    await page.goto('/admin/');
    await expect(page.locator('#save-admin-btn')).toBeAttached();
    const missing = await page.evaluate((values) => {
        const form = document.querySelector('form') as HTMLFormElement;
        const missing: string[] = [];
        for (const [name, value] of Object.entries(values)) {
            const els = form.querySelectorAll<HTMLInputElement>(`[name="${name}"]`);
            if (!els.length) {
                missing.push(name);
                continue;
            }
            els.forEach((el) => {
                if (el.type === 'checkbox') {
                    el.checked = Boolean(value);
                } else if (el.type === 'hidden' && els.length > 1) {
                    // toggle: hidden "false" + checkbox; leave the hidden one
                } else {
                    el.value = String(value);
                }
                el.dispatchEvent(new Event('change', { bubbles: true }));
            });
        }
        return missing;
    }, values);
    expect(missing, 'admin fields exist').toEqual([]);
    const saved = page.waitForResponse((r) => r.url().includes('/api/admin.php') && r.request().method() === 'POST');
    await page.locator('#save-admin-btn').click();
    const res = await saved;
    const body = await res.json().catch(() => ({}));
    expect(body.status ?? 'success', JSON.stringify(body)).toBe('success');
}

/** The admin panel's reset button with the given checkboxes (others off). */
export async function adminReset(page: Page, opts: { remove_print_db: boolean }) {
    await page.goto('/admin/');
    await expect(page.locator('#reset-btn')).toBeAttached();
    await page.evaluate((opts) => {
        for (const name of ['remove_media', 'remove_mail_db', 'remove_print_db', 'remove_config']) {
            const box = document.querySelector<HTMLInputElement>(`input[type="checkbox"][name="reset[${name}]"]`);
            if (box) box.checked = name === 'remove_print_db' ? opts.remove_print_db : false;
        }
    }, opts);
    page.once('dialog', (d) => d.accept());
    const done = page.waitForResponse((r) => r.url().includes('/api/admin.php') && r.request().method() === 'POST');
    await page.locator('#reset-btn').click();
    const res = await done;
    expect(res.ok()).toBe(true);
    await page.waitForLoadState();
}
