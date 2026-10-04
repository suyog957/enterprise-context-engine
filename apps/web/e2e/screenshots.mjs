// Captures documentation screenshots from a running stack:
//   node e2e/screenshots.mjs  (WEB_BASE_URL, SCREENSHOT_DIR)
import { chromium, devices } from "@playwright/test";

const base = process.env.WEB_BASE_URL ?? "http://localhost:5173";
const out = process.env.SCREENSHOT_DIR ?? "screenshots";

const browser = await chromium.launch();

async function shoot(name, principal, path, steps, options = {}) {
  const context = await browser.newContext(options.device ?? { viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(base + "/");
  await page.evaluate((id) => localStorage.setItem("ecg.principal", id), principal);
  await page.goto(base + path);
  if (steps) await steps(page);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: options.fullPage ?? false });
  await context.close();
  console.log(`saved ${name}.png`);
}

async function askQuestion(page, question) {
  await page.getByLabel("Question").fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.getByTestId("answer").waitFor();
}

await shoot("chat-eligible", "user-alice", "/", (page) => askQuestion(page, "Can PR-1007 be converted to a purchase order?"), { fullPage: true });
await shoot("chat-blocked", "user-alice", "/", (page) => askQuestion(page, "Why can't PR-1011 become a purchase order?"));
await shoot("chat-entity-resolution", "user-alice", "/", (page) => askQuestion(page, "Show all purchases involving Acme."));
await shoot("requisition", "user-alice", "/requisitions/PR-1012");
await shoot("entity", "user-alice", "/entities/supplier-bb05b4fd1d1c0e41");
await shoot("graph", "user-alice", "/graph?focus=PR-1007", async (page) => {
  await page.getByText(/\d+ nodes/).waitFor();
  await page.waitForTimeout(1500);
});
await shoot("policy", "user-alice", "/policy", async (page) => {
  await page.getByRole("button", { name: "Evaluate" }).click();
  await page.getByText("Decisions for PR-1011").waitFor();
});
await shoot("evaluation", "user-auditor", "/evaluation", (page) => page.getByRole("heading", { name: "Gates" }).waitFor(), { fullPage: true });
await shoot("mobile-chat", "user-alice", "/", (page) => askQuestion(page, "Can PR-1007 be converted to a purchase order?"), {
  device: devices["Pixel 7"],
});

await browser.close();
