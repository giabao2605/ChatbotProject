import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  expect,
  request as createRequest,
  test,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const ALLOWED_ACTOR = "demo_owner_it";
const DENIED_ACTOR = "demo_viewer";
const CREDENTIALS_PATH = fileURLToPath(
  new URL("../../../.local/demo-wave-credentials.json", import.meta.url),
);
const RAG_BASE_URL = process.env.E2E_RAG_BASE_URL || "http://127.0.0.1:8100";
const IT_QUESTION = "Phiên bản hiện hành của quy trình hỗ trợ IT quy định gì? Cách hỏi 1.";
const tracedContexts = new WeakSet<BrowserContext>();

type PublicProfile = {
  username: string;
  roles: string[];
  csrf_token: string;
  [key: string]: unknown;
};

function demoPassword(actor: string): string {
  let credentials: unknown;
  try {
    credentials = JSON.parse(readFileSync(CREDENTIALS_PATH, "utf8"));
  } catch {
    throw new Error(
      `Không đọc được demo credentials tại ${CREDENTIALS_PATH}. Chạy bootstrap demo-wave trước khi chạy E2E.`,
    );
  }
  if (!credentials || typeof credentials !== "object") {
    throw new Error(`Demo credentials tại ${CREDENTIALS_PATH} phải là JSON object.`);
  }
  const password = (credentials as Record<string, unknown>)[actor];
  if (typeof password !== "string" || !password) {
    throw new Error(`Demo credentials thiếu actor bắt buộc: ${actor}.`);
  }
  return password;
}

async function login(page: Page, actor: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập").fill(actor);
  await page.getByLabel("Mật khẩu").fill(demoPassword(actor));
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

async function startSafeTrace(context: BrowserContext): Promise<void> {
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  tracedContexts.add(context);
}

test.afterEach(async ({ context }, testInfo) => {
  if (!tracedContexts.has(context)) return;
  const failed = testInfo.status !== testInfo.expectedStatus;
  const tracePath = failed ? testInfo.outputPath("trace.zip") : undefined;
  await context.tracing.stop(tracePath ? { path: tracePath } : undefined);
  if (tracePath) {
    await testInfo.attach("trace", { path: tracePath, contentType: "application/zip" });
  }
});

test("đăng nhập thật, đọc public profile và hoàn tất chat SSE", async ({
  page,
  context,
}) => {
  await login(page, ALLOWED_ACTOR);

  const meResponse = await context.request.get("/api/auth/me");
  expect(meResponse.status()).toBe(200);
  const me = (await meResponse.json()) as { user: PublicProfile };
  expect(me.user.username).toBe(ALLOWED_ACTOR);
  expect(me.user.roles).toContain("uploader");
  expect(me.user.csrf_token).toEqual(expect.any(String));
  expect(me.user.csrf_token.length).toBeGreaterThan(20);
  expect(Object.keys(me.user).some((key) => /password|hash|secret/i.test(key))).toBe(false);

  await page.goto("/chat");
  const composer = page.getByPlaceholder("Hỏi bất cứ điều gì");
  await expect(composer).toBeVisible();
  await composer.fill(IT_QUESTION);

  const streamResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/chat/message") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Gửi", exact: true }).click();
  const streamResponse = await streamResponsePromise;
  expect(streamResponse.status()).toBe(200);
  expect(streamResponse.headers()["content-type"]).toContain("text/event-stream");

  const assistant = page.locator("article.message.assistant").last();
  await expect(assistant.getByText("Hoàn tất", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  await startSafeTrace(context);
  await expect(page.getByText("Sẵn sàng", { exact: true })).toBeVisible();
  await expect(assistant.locator(".message-content")).toContainText("4 giờ");
  const citations = assistant.locator(".citation-card");
  await expect(citations.first()).toContainText(/· trang \d+/);
  await expect(citations.first().getByRole("link", { name: "Tải bản gốc" })).toHaveAttribute(
    "href",
    /\S+/,
  );
});

test("từ chối session, CSRF và dữ liệu IT ngoài phạm vi", async ({
  page,
  context,
  request,
}) => {
  const anonymousMe = await request.get("/api/auth/me");
  expect(anonymousMe.status()).toBe(401);

  await login(page, DENIED_ACTOR);
  await page.goto("/chat");
  const composer = page.getByPlaceholder("Hỏi bất cứ điều gì");
  await composer.fill(IT_QUESTION);
  await page.getByRole("button", { name: "Gửi", exact: true }).click();

  const assistant = page.locator("article.message.assistant").last();
  await expect(assistant.getByText("Hoàn tất", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  const missingCsrf = await context.request.post("/api/auth/refresh");
  expect(missingCsrf.status()).toBe(403);
  expect(await missingCsrf.json()).toMatchObject({
    detail: "Invalid CSRF token",
  });

  await startSafeTrace(context);
  const deniedAnswer = assistant.locator(".message-content");
  await expect(deniedAnswer).toContainText(/chưa đủ quyền truy cập|không đề cập đến quy trình hỗ trợ IT/i);
  await expect(deniedAnswer).not.toContainText(/4 giờ|IT-100|it_effective_(?:core|process|reference)\.md/i);
  expect((await assistant.locator(".citation-card").allTextContents()).join(" ")).not.toMatch(
    /\bIT\b|it[_-]/i,
  );
});

test("RAG health giữ baseline all-off", async ({ context }) => {
  const ragRequest = await createRequest.newContext({ baseURL: RAG_BASE_URL });
  try {
    const response = await ragRequest.get("/health");
    expect(response.status()).toBe(200);
    const health = (await response.json()) as {
      status: string;
      rag_loaded: boolean;
      activation_valid: boolean;
      live_authorized: boolean;
      feature_flags: Record<string, boolean>;
    };
    await startSafeTrace(context);
    expect(health).toMatchObject({
      status: "ok",
      rag_loaded: true,
      activation_valid: true,
      live_authorized: true,
    });
    expect(Object.keys(health.feature_flags).length).toBeGreaterThan(0);
    expect(Object.values(health.feature_flags).every((enabled) => enabled === false)).toBe(true);
  } finally {
    await ragRequest.dispose();
  }
});
