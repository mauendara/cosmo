import { expect, test } from "@playwright/test";

test("home page shows the backend greeting", async ({ page }) => {
  await page.goto("/");
  const greeting = page.getByTestId("greeting");
  await expect(greeting).toHaveText("hello from cosmo gate fixture", { timeout: 10_000 });
});
