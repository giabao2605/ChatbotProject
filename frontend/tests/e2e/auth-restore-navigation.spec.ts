import { expect, test } from "@playwright/test";

test("Back and Forward after reload share auth restoration and keep the destination", async ({ page, context }) => {
  let holdRestore = false;
  let restoreCalls = 0;
  let started!: () => void;
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { started = resolve; });
  const held = new Promise<void>(resolve => { finish = resolve; });
  const navigations: string[] = [];
  page.on("framenavigated", frame => {
    if (frame === page.mainFrame()) navigations.push(frame.url());
  });
  await context.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/auth/me") {
      if (holdRestore) {
        restoreCalls += 1;
        started();
        await held;
      }
      return route.fulfill({ json: { user: {
        user_id: 7, username: "fixture", roles: ["reviewer"],
        allowed_departments: ["Technical"], allowed_sites: ["factory"],
        max_security_level: "internal", csrf_token: "fixture", preferred_language: "vi",
      } } });
    }
    if (route.request().method() !== "GET") return route.abort();
    if (path === "/api/documents") return route.fulfill({ json: [] });
    if (path === "/api/lifecycle") {
      return route.fulfill({ json: { expired: [], expiring_soon: [], needs_review: [], counts: {} } });
    }
    await route.fulfill({ json: { items: [], rows: [], stats: {}, total: 0 } });
  });

  await page.goto("/dashboard");
  await page.locator('a[href="/documents"]').click();
  await expect(page).toHaveURL(/\/documents$/);
  await page.evaluate(() => history.replaceState(history.state, "", "/documents?tab=expired"));
  holdRestore = true;
  await page.reload({ waitUntil: "domcontentloaded" });
  await pending;
  await page.evaluate(() => history.back());
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.evaluate(() => history.forward());
  await expect(page).toHaveURL(/\/documents\?tab=expired$/);
  finish();

  await expect(page.locator(".account-name")).toHaveText("fixture");
  await expect(page).toHaveURL(/\/documents\?tab=expired$/);
  expect(restoreCalls).toBe(1);
  expect(navigations.some(url => new URL(url).pathname === "/login")).toBe(false);
  await page.screenshot({ path: "../reports/rag-backlog-20261026/ui-nav-history.png", fullPage: true });
});
