import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function actAs(page: Page, principal: string) {
  await page.goto("/");
  await page.getByLabel("Principal").selectOption(principal);
}

async function ask(page: Page, question: string) {
  await page.getByLabel("Question").fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  return page.getByTestId("answer");
}

test("allowed: eligible requisition shows sources, reasons and allowed actions", async ({ page }) => {
  await actAs(page, "user-alice");
  const answer = await ask(page, "Can PR-1007 be converted to a purchase order?");

  await expect(answer).toContainText("Yes");
  await expect(page.locator('[data-action="CREATE_PURCHASE_ORDER"] [data-status="AVAILABLE"]')).toBeVisible();
  await expect(page.locator('[data-node="determine_allowed_actions"]')).toBeVisible();
  await expect(page.getByText("Supporting documents")).toBeVisible();
});

test("blocked: supplier block is explained with a rule reference", async ({ page }) => {
  await actAs(page, "user-alice");
  const answer = await ask(page, "Why can't PR-1011 become a purchase order?");

  await expect(answer).toContainText("No");
  await expect(answer).toContainText("SUP-004");
  await expect(page.getByTestId("proposal-panel")).toHaveCount(0);
});

test("approval-required: the agent proposes an approval, not a write", async ({ page }) => {
  await actAs(page, "user-alice");
  const answer = await ask(page, "Create a PO for PR-1012.");

  await expect(answer).not.toBeEmpty();
  const proposal = page.getByTestId("proposal-panel");
  await expect(proposal).toBeVisible();
  await expect(proposal.locator("[data-status]").first()).toHaveAttribute(
    "data-status",
    /APPROVAL_REQUIRED|CONFIRMATION_REQUIRED/,
  );
});

test("dry-run: confirming a purchase order simulates without writing", async ({ page }) => {
  await actAs(page, "user-alice");
  await page.goto("/requisitions/PR-1007");
  await expect(page.locator('[data-action="CREATE_PURCHASE_ORDER"]')).toBeVisible();
  await page.getByRole("button", { name: "Confirm purchase order" }).click();

  await expect(page.getByTestId("proposal-panel")).toContainText("Dry run - no record written");
  await expect(page.getByTestId("proposal-panel").locator('[data-status="SIMULATED"]')).toBeVisible();
});

test("authorization: out-of-scope requisitions are not revealed", async ({ page }) => {
  await actAs(page, "user-alice");
  const answer = await ask(page, "Can PR-1008 be converted to a purchase order?");

  await expect(answer).toContainText("outside your authorized scope");
});

test("graph explorer renders the requisition neighbourhood", async ({ page }) => {
  await actAs(page, "user-alice");
  await page.goto("/graph?focus=PR-1007");

  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByText(/\d+ nodes/)).toBeVisible();
  await expect(page.getByText("hasSupplier").first()).toBeVisible();
});
