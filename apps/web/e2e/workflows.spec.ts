import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

const stamp = Date.now();
const password = "QA-only-correct-horse-128!";
async function signup(page: Page, email: string, enterprise = false) {
  await page.goto("/"); await page.getByRole("button", { name: "Create an account", exact: true }).click();
  await page.getByLabel("Your name", { exact: true }).fill("Avery Morgan");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Workspace type").selectOption(enterprise ? "enterprise" : "personal");
  await page.getByLabel("Workspace name", { exact: true }).fill(enterprise ? "Aster Systems" : "Avery’s workspace");
  if (enterprise) await page.getByLabel("Member capacity").fill("3");
  await page.getByRole("button", { name: "Create workspace", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Ask DataMind" })).toBeVisible();
}
async function auth(request: APIRequestContext) { const response = await request.get("/api/me"); expect(response.ok()).toBeTruthy(); const me = await response.json(); return { me, headers: { "X-CSRF-Token": me.csrf, "X-Workspace-ID": me.workspace_id } }; }

test("personal signup, upload, real SQLite query, chart controls, profile, responsive layout and login persistence", async ({ page }) => {
  const email = `qa-personal-${stamp}@example.com`; await signup(page, email);
  await expect(page.getByRole("heading", { name: /What can your data/ })).toBeVisible();
  await expect(page.locator(".welcome")).toHaveCSS("opacity", "1");
  mkdirSync("../../docs/screenshots", { recursive: true });
  const symbol = await page.locator(".welcome-symbol").boundingBox();
  const topbar = await page.locator(".topbar").boundingBox();
  expect(symbol!.y).toBeGreaterThanOrEqual(topbar!.y + topbar!.height);
  await page.screenshot({ path: "../../docs/screenshots/workspace-light.png", fullPage: true });
  await page.getByRole("button", { name: "Document library", exact: true }).click();
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles({ name: "quarterly-orders.csv", mimeType: "text/csv", buffer: Buffer.from("customer,revenue,month\nAster,120,2026-01-01\nBirch,80,2026-02-01\n") });
  const document = page.getByRole("button", { name: /quarterly-orders.csv/ });
  await expect(document).toContainText("ready", { timeout: 90_000 }); await document.click();
  await expect(page.getByText("Read-only query inspector", { exact: true })).toBeVisible();
  await page.getByLabel("SQL query", { exact: true }).fill("SELECT customer, revenue, month FROM data ORDER BY revenue DESC");
  await page.getByRole("button", { name: "Run read-only query", exact: true }).click();
  await expect(page.getByRole("cell", { name: "120", exact: true })).toBeVisible();
  await page.getByLabel("Chart type").selectOption("bar");
  await expect(page.getByRole("img", { name: /bar chart/ })).toBeVisible();
  await page.getByLabel("Filter result").fill("Aster"); await expect(page.getByText("1 visible rows", { exact: true })).toBeVisible();
  await page.getByLabel("Close dialog", { exact: true }).click();
  await page.getByRole("button", { name: "Data connections", exact: true }).click();
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("Connection name").fill("Aster commerce"); await page.getByLabel("Database type").selectOption("sqlite");
  await page.getByLabel("File inside the configured SQLite source directory").fill("aster.db");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await page.getByRole("button", { name: "Test & discover", exact: true }).click();
  await page.getByLabel("orders", { exact: true }).check(); await page.getByLabel("customers", { exact: true }).check();
  await page.getByLabel("Business metric definitions (JSON)").fill('{"revenue":"sum orders.revenue in USD; booked orders"}');
  await page.getByRole("button", { name: "Save resources and definitions", exact: true }).click();
  await page.getByLabel("SQL query", { exact: true }).fill("SELECT customer_id, SUM(revenue) AS booked_revenue FROM orders GROUP BY customer_id ORDER BY customer_id");
  await page.getByRole("button", { name: "Run read-only query", exact: true }).click();
  await expect(page.getByRole("cell", { name: "4620", exact: true })).toBeVisible();
  await page.getByLabel("Close dialog", { exact: true }).click();
  await page.getByRole("button", { name: /Avery Morgan.*Avery/ }).click();
  await page.getByLabel("Appearance").selectOption("dark"); await page.getByRole("button", { name: "Save preferences", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await expect(page.locator(".welcome")).toHaveCSS("opacity", "1");
  await page.screenshot({ path: "../../docs/screenshots/workspace-dark.png", fullPage: true });
  const sourceButton = page.getByRole("button", { name: /^(Sources|\d+ sources)$/ });
  await sourceButton.focus(); await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Choose your sources" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Choose your sources" })).not.toBeVisible();
  await page.getByRole("button", { name: "Collapse sidebar" }).click(); await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("textbox", { name: "Ask DataMind" })).toBeVisible();
  const mobileComposer = await page.getByRole("textbox", { name: "Ask DataMind" }).boundingBox();
  expect(mobileComposer?.width).toBeGreaterThan(250);
  expect(await page.evaluate(() => window.document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "../../docs/screenshots/workspace-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 }); await page.getByRole("button", { name: "Expand sidebar" }).click();
  await page.getByRole("button", { name: /Avery Morgan.*Avery/ }).click(); await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByLabel("Email", { exact: true }).fill(email); await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Ask DataMind" })).toBeVisible();
});

test("enterprise invitation, deny-by-default grants, revocation and private conversations", async ({ page, browser }) => {
  await signup(page, `qa-admin-${stamp}@example.com`, true);
  const { me, headers } = await auth(page.request);
  const created = await page.request.post("/api/connections", { headers, data: { name: "Team commerce", kind: "sqlite", config: { path: "aster.db" } } }); expect(created.status()).toBe(201);
  const sourceId = (await created.json()).id;
  expect((await page.request.post(`/api/connections/${sourceId}/test`, { headers })).ok()).toBeTruthy();
  expect((await page.request.patch(`/api/sources/${sourceId}/catalog`, { headers, data: { resources: ["orders"], catalog: {} } })).ok()).toBeTruthy();
  await page.reload();
  await page.getByRole("button", { name: "Workspace settings", exact: true }).click();
  await page.getByRole("button", { name: "Invite member", exact: true }).click();
  const email = `qa-member-${stamp}@example.com`; await page.getByLabel("Email address", { exact: true }).fill(email);
  await page.getByRole("button", { name: "Create invitation link", exact: true }).click();
  const invitation = await page.locator(".secret-output code").innerText(); expect(invitation).toContain("invite=");
  await page.getByLabel("Close dialog", { exact: true }).click();
  const memberContext = await browser.newContext(); const member = await memberContext.newPage(); await signup(member, email);
  await member.goto(invitation); await expect(member.getByLabel("Switch workspace")).toHaveValue(me.workspace_id);
  await member.getByRole("button", { name: "Data connections", exact: true }).click(); await expect(member.getByText("Bring your data together")).toBeVisible();
  const memberAuth = await auth(member.request); const memberHeaders = { ...memberAuth.headers, "X-Workspace-ID": me.workspace_id };
  expect((await member.request.post(`/api/sources/${sourceId}/sql`, { headers: memberHeaders, data: { sql: "SELECT * FROM orders" } })).status()).toBe(403);
  await page.getByRole("button", { name: "Data connections", exact: true }).click(); await page.getByRole("button", { name: /Team commerce/ }).click();
  await page.getByRole("button", { name: "Permissions", exact: true }).click();
  await page.getByLabel(`Allow Avery Morgan (${email})`, { exact: true }).check();
  await expect.poll(async () => (await member.request.get("/api/sources", { headers: memberHeaders })).json()).toMatchObject([{ id: sourceId }]);
  expect((await member.request.post(`/api/sources/${sourceId}/sql`, { headers: memberHeaders, data: { sql: "SELECT COUNT(*) AS count FROM orders" } })).status()).toBe(200);
  await page.getByLabel(`Allow Avery Morgan (${email})`, { exact: true }).uncheck();
  await expect.poll(async () => (await member.request.post(`/api/sources/${sourceId}/sql`, { headers: memberHeaders, data: { sql: "SELECT COUNT(*) AS count FROM orders" } })).status()).toBe(403);
  const privateChat = await page.request.post("/api/conversations", { headers }); const privateId = (await privateChat.json()).id;
  expect((await member.request.get(`/api/conversations/${privateId}`, { headers: memberHeaders })).status()).toBe(404);
  await memberContext.close();
});

test("real PostgreSQL, MySQL and MongoDB source configuration and bounded execution through HTTP", async ({ page }) => {
  await signup(page, `qa-connectors-${stamp}@example.com`);
  const { headers } = await auth(page.request); const demoPassword = process.env.DEMO_DB_PASSWORD;
  expect(demoPassword, "Run setup.py and load_services.py first").toBeTruthy();
  const configurations = [
    { kind: "postgres", port: 15432, resources: ["public.orders"], sql: "SELECT COUNT(*) AS count FROM public.orders", expected: 120 },
    { kind: "mysql", port: 13306, resources: ["invoices"], sql: "SELECT COUNT(*) AS count FROM invoices", expected: 20 },
    { kind: "mongo", port: 17017, resources: ["tickets"], sql: "", expected: 41 },
  ];
  for (const item of configurations) {
    const docker = process.env.E2E_DOCKER === "1";
    const servicePort = item.kind === "postgres" ? 5432 : item.kind === "mysql" ? 3306 : 27017;
    const response = await page.request.post("/api/connections", { headers, data: { name: `Aster ${item.kind}`, kind: item.kind, config: { host: docker ? item.kind : "127.0.0.1", port: docker ? servicePort : item.port, database: "aster", username: "aster_reader", password: demoPassword, auth_database: "aster" } } }); expect(response.status()).toBe(201);
    const sourceId = (await response.json()).id;
    expect((await page.request.post(`/api/connections/${sourceId}/test`, { headers })).ok()).toBeTruthy();
    expect((await page.request.patch(`/api/sources/${sourceId}/catalog`, { headers, data: { resources: item.resources, catalog: {} } })).ok()).toBeTruthy();
    const result = await page.request.post(`/api/sources/${sourceId}/${item.kind === "mongo" ? "mongo" : "sql"}`, { headers, data: item.kind === "mongo" ? { collection: "tickets", pipeline: [{ $count: "count" }] } : { sql: item.sql } });
    expect(result.status()).toBe(200); expect((await result.json()).rows[0].count).toBe(item.expected);
  }
});

test("document model ingestion and evidence retrieval completes with actual HF models", async ({ page }) => {
  await signup(page, `qa-document-${stamp}@example.com`); const { headers } = await auth(page.request);
  const uploaded = await page.request.post("/api/documents", { headers, multipart: { file: { name: "contract-C001.md", mimeType: "text/markdown", buffer: Buffer.from("# C001 signed renewal\n\nCustomer C001 requires 31 days notice before renewal on 2026-11-01. The signed contract controls over older CRM notes.") } } });
  expect(uploaded.status()).toBe(201); const id = (await uploaded.json()).id;
  await expect.poll(async () => (await page.request.get(`/api/documents/${id}`, { headers })).json(), { timeout: 110_000, intervals: [1000, 2000] }).toMatchObject({ status: "ready" });
  await page.getByRole("button", { name: "Document library", exact: true }).click(); await page.getByRole("button", { name: /contract-C001.md/ }).click();
  await expect(page.getByText(/Customer C001 requires 31 days notice/)).toBeVisible();
  await page.screenshot({ path: "../../docs/screenshots/document-inspection.png", fullPage: true });
});

test("missing provider configuration shows an explicit error and no fabricated answer", async ({ page }) => {
  test.skip(Boolean(process.env.GEMINI_API_KEY), "Provider is configured; covered by the separately enabled live test");
  await signup(page, `qa-provider-${stamp}@example.com`);
  await page.getByRole("textbox", { name: "Ask DataMind" }).fill("Hello");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "GEMINI_API_KEY" })).toBeVisible();
  await expect(page.locator(".message.assistant")).toHaveCount(0);
  await expect(page.locator(".message.error")).toContainText("GEMINI_API_KEY");
  await page.getByRole("button", { name: "Sources & execution", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Behind the answer", exact: true })).toBeVisible();
});

test("live Gemini natural-language SQL, streaming, chart saving and dashboard refresh", async ({ page }) => {
  test.skip(!process.env.GEMINI_API_KEY || process.env.RUN_LIVE_AI !== "1", "Requires explicitly enabled live Gemini budget; no model response is simulated");
  if (process.env.E2E_REUSE_LIVE_EMAIL) {
    await page.goto("/");
    await page.getByLabel("Email", { exact: true }).fill(process.env.E2E_REUSE_LIVE_EMAIL);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByRole("button", { name: "Show booked revenue in USD by month from orders, ordered by month.", exact: true }).click();
  } else {
  await signup(page, `qa-live-${stamp}@example.com`);
  const { headers } = await auth(page.request);
  const response = await page.request.post("/api/connections", { headers, data: { name: "Aster sales", kind: "sqlite", config: { path: "aster.db" } } }); const id = (await response.json()).id;
  await page.request.post(`/api/connections/${id}/test`, { headers });
  await page.request.patch(`/api/sources/${id}/catalog`, { headers, data: { resources: ["orders"], catalog: { metrics: { revenue: "sum of orders.revenue in USD" } } } });
  await page.reload(); await page.getByRole("button", { name: /^(Sources|\d+ sources)$/ }).click(); await page.getByLabel("Use Aster sales").check(); await page.getByRole("button", { name: "Use selected sources" }).click();
  await page.getByRole("textbox", { name: "Ask DataMind" }).fill("Show booked revenue in USD by month from orders, ordered by month."); await page.getByRole("button", { name: "Send message" }).click();
  }
  await expect(page.getByRole("button", { name: "Sources & execution" })).toBeVisible({ timeout: 110_000 });
  await expect(page.locator(".message.error")).toHaveCount(0);
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  await page.screenshot({ path: "../../docs/screenshots/live-sql-answer.png", fullPage: true });
  await page.getByRole("button", { name: "Save insight", exact: true }).first().click();
  const saveDialog = page.getByRole("dialog", { name: "Save this insight" });
  await expect(saveDialog).toBeVisible();
  await saveDialog.getByRole("button", { name: "Save insight", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh", exact: true })).toBeVisible();
  const [refreshed] = await Promise.all([page.waitForResponse(r => r.url().endsWith("/refresh") && r.request().method() === "POST"), page.getByRole("button", { name: "Refresh", exact: true }).click()]);
  expect(refreshed.ok()).toBeTruthy();
  await page.screenshot({ path: "../../docs/screenshots/live-dashboard.png", fullPage: true });
});
