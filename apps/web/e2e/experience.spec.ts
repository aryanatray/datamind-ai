import { test, expect, type Page } from "@playwright/test";

async function account(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Create an account", exact: true }).click();
  await page.getByLabel("Your name", { exact: true }).fill("Experience QA");
  await page.getByLabel("Email", { exact: true }).fill(`experience-${Date.now()}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("experience-only-password-123!");
  await page.getByRole("button", { name: "Create workspace", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Ask DataMind" })).toBeVisible();
  const me = await (await page.request.get("/api/me")).json();
  return { "X-CSRF-Token": me.csrf, "X-Workspace-ID": me.workspace_id };
}

test("setup, ready source defaults, saved source scope, web toggle, pins and personalization survive reload", async ({ page }) => {
  const headers = await account(page);
  await expect(page.getByRole("button", { name: "Automatic web search" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Setup checklist", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your workspace, ready for insight." })).toBeVisible();
  await expect(page.getByText("0 of 4 steps complete")).toBeVisible();
  await page.getByRole("button", { name: "Document library", exact: true }).click();
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles({ name: "scope.csv", mimeType: "text/csv", buffer: Buffer.from("amount\n120\n") });
  await expect(page.getByRole("button", { name: /scope.csv/ })).toContainText("ready", { timeout: 90_000 });
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await page.getByRole("button", { name: "1 sources", exact: true }).click();
  await expect(page.getByLabel("Use scope.csv")).toBeChecked();
  await page.getByRole("button", { name: "Clear all", exact: true }).click();
  await expect(page.getByLabel("Use scope.csv")).not.toBeChecked();
  await page.getByRole("button", { name: "Select all ready", exact: true }).click();
  await page.getByRole("button", { name: "Use selected sources", exact: true }).click();
  const sources = await (await page.request.get("/api/sources")).json();
  const chat = await (await page.request.post("/api/conversations", { headers })).json();
  await page.request.patch(`/api/conversations/${chat.id}`, { headers, data: { source_ids: [sources[0].id], pinned: true, web_enabled: false } });
  await page.reload();
  await page.getByRole("button", { name: "New conversation", exact: true }).waitFor();
  await page.locator(".conversation>button:first-child").first().click();
  await expect(page.getByRole("button", { name: "Automatic web search" })).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("button", { name: "Unpin New conversation", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "1 sources", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Experience QA.*workspace/ }).click();
  await page.getByLabel("What should DataMind know about you?", { exact: true }).fill("I am a finance analyst.");
  await page.getByLabel("How should DataMind respond?", { exact: true }).fill("Use USD and concise tables.");
  await page.getByRole("button", { name: "Save preferences", exact: true }).click();
  await expect.poll(async () => (await (await page.request.get("/api/me")).json()).preferences.custom_instructions).toBe("Use USD and concise tables.");
  await page.reload();
  await page.getByRole("button", { name: /Experience QA.*workspace/ }).click();
  await expect(page.getByLabel("How should DataMind respond?", { exact: true })).toHaveValue("Use USD and concise tables.");
});

test("document replacement and file-history restoration execute real processing", async ({ page }) => {
  await account(page);
  await page.getByRole("button", { name: "Document library", exact: true }).click();
  await page.getByLabel("Upload documents", { exact: true }).setInputFiles({ name: "versioned.csv", mimeType: "text/csv", buffer: Buffer.from("amount\n120.25\n") });
  await expect(page.getByRole("button", { name: /versioned.csv/ })).toContainText("ready", { timeout: 90_000 });
  await page.getByRole("button", { name: /versioned.csv/ }).click();
  await expect(page.getByRole("region", { name: "Document processing" })).toContainText("Ready");
  await page.getByText("File version history (1)", { exact: true }).click();
  await page.getByLabel("Replace document file", { exact: true }).setInputFiles({ name: "revised.csv", mimeType: "text/csv", buffer: Buffer.from("amount\n80.75\n") });
  await expect(page.getByText(/Index version 2 · ready/)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("File version history (2)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Restore v1", exact: true }).click();
  await expect(page.getByText(/Index version 3 · ready/)).toBeVisible({ timeout: 90_000 });
  await page.getByLabel("SQL query", { exact: true }).fill("SELECT SUM(amount) AS total FROM data");
  await page.getByRole("button", { name: "Run read-only query", exact: true }).click();
  await expect(page.getByRole("cell", { name: "120.25", exact: true })).toBeVisible();
});

test("offline streamed fixture exposes clickable citations, latency, usage and public source details", async ({ page }) => {
  await account(page);
  let payload: Record<string, unknown> = {};
  await page.route("**/api/conversations/*/chat", async route => {
    payload = route.request().postDataJSON();
    const artifact = {
      results: [], evidence: [{ chunk_id: "fixture-passage", filename: "Fixture contract.md", source_id: "fixture-source", version: 2, page: 1, heading: "Notice", text: "Notice is 31 days." }],
      selected_sources: [{ id: "fixture-source", name: "Fixture contract", kind: "document" }],
      observability: { total_ms: 1250, used_source_ids: ["fixture-source"], public_web_used: true },
      web: [{ title: "Public fixture", url: "https://example.com", text: "A mocked public snippet" }],
      trace: [{ stage: "gemini_synthesis", latency_ms: 750 }],
      usage: { total_tokens: 24, usage_reported: true, calls: [{ input_tokens: 16, output_tokens: 8 }] },
    };
    await route.fulfill({ contentType: "text/event-stream", body: `data: ${JSON.stringify({ type: "done", id: "fixture-answer", text: "Notice is 31 days [D:fixture-passage].", artifact })}\n\n` });
  });
  await page.getByRole("textbox", { name: "Ask DataMind" }).fill("Show the offline fixture");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await page.getByRole("link", { name: "D:fixture-passage", exact: true }).click();
  expect(payload.web_enabled).toBe(true);
  await expect(page.getByRole("region", { name: "Latency and usage breakdown" })).toContainText("1.3s");
  await expect(page.getByText("Input / output tokens", { exact: true })).toBeVisible();
  await page.getByText("Fixture contract.md · p. 1", { exact: true }).click();
  await expect(page.getByText("Notice is 31 days.", { exact: true })).toBeVisible();
  const panel = await page.locator(".inspector").boundingBox();
  expect(panel!.x).toBeGreaterThan(800);
  await page.screenshot({ path: "../../docs/screenshots/experience-evidence.png", fullPage: true });
  await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await expect(page.locator(".inspector")).toHaveCount(0);
  await page.screenshot({ path: "../../docs/screenshots/experience-chat-light.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  const composer = await page.locator(".composer").boundingBox();
  expect(composer!.width).toBeGreaterThan(300);
  await page.screenshot({ path: "../../docs/screenshots/experience-mobile.png", fullPage: true });
});
