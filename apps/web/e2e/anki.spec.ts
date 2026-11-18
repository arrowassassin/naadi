import { mkdirSync, mkdtempSync } from "node:fs";
import path from "node:path";
import { canMintUsers } from "./auth-users";
import { expect, expectNoAxeViolations, expectNoHorizontalOverflow, quiet, test } from "./fixtures";
import { freshStudent, watchConsole, writeApkg } from "./support/student";

/**
 * Segment 20: import an .apkg, review an imported card, export the deck, import the export again (a no-op), against
 * the real API and database. The package is made up in code (apps/api/tests/anki/builders.py); no deck is in the repo.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const SHOTS = path.resolve(import.meta.dirname, "../../../.tmp/shots/anki");

test.describe("Anki import and export", () => {
  test("import → review → export → re-import is a no-op", async ({ browser, baseURL }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "anki");
    // .tmp/ is gitignored, so a fresh checkout (CI) has none until some spec makes it: create it here.
    const tmpRoot = path.resolve(import.meta.dirname, "../../../.tmp");
    mkdirSync(tmpRoot, { recursive: true });
    const work = mkdtempSync(path.join(tmpRoot, "anki-"));
    try {
      const file = writeApkg(path.join(work, "bio.apkg"), "Biology::Cells", [
        ["What organelle makes ATP?", "Mitochondria"],
        ["What is the cell's control centre?", "The nucleus"],
      ]);
      const page = await student.open("/cards");
      const { errors, guard } = watchConsole(page);
      await quiet(page);

      // import
      await page.getByRole("button", { name: "Import deck" }).click();
      const dialog = page.getByRole("dialog", { name: "Import an Anki deck" });
      await expect(dialog).toBeVisible();
      await dialog.locator("input[type=file]").setInputFiles(file);
      await expectNoAxeViolations(page);
      mkdirSync(SHOTS, { recursive: true });
      await guard.duringTool(() =>
        page.screenshot({ path: path.join(SHOTS, `import--${info.project.name}.png`), animations: "disabled" }),
      );
      await dialog.getByRole("button", { name: "Import deck" }).click();
      const report = page.getByRole("dialog", { name: "Import finished" });
      await expect(report.getByTestId("anki-report")).toContainText("2 cards added");
      await expect(report).toContainText("Biology / Cells: 2 added, 0 already there");
      await expectNoAxeViolations(page);
      await guard.duringTool(() =>
        page.screenshot({ path: path.join(SHOTS, `report--${info.project.name}.png`), animations: "disabled" }),
      );

      // review an imported card: it starts as a new card
      await report.getByRole("button", { name: "Review these cards" }).click();
      await expect(page.getByText(/What organelle makes ATP\?|What is the cell's control centre\?/)).toBeVisible();
      await page.getByRole("button", { name: "Show answer" }).click();
      await expect(page.getByRole("button", { name: /^Good/ })).toBeVisible();
      await page.getByRole("button", { name: /^Good/ }).click();

      // export the deck
      await page.getByRole("button", { name: "Export deck" }).first().click();
      const exportDialog = page.getByRole("dialog", { name: "Export a deck" });
      await exportDialog.getByLabel("Deck").selectOption({ label: "Biology / Cells (2 cards)" });
      await expectNoAxeViolations(page);
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        exportDialog.getByRole("button", { name: "Download .apkg" }).click(),
      ]);
      expect(download.suggestedFilename()).toMatch(/^naadi-biology-cells\.apkg$/);
      const exported = path.join(work, "export.apkg");
      await download.saveAs(exported);
      await exportDialog.getByRole("button", { name: "Close" }).last().click();

      // import the export again: everything is already there
      await page.getByRole("button", { name: "Import deck" }).first().click();
      const again = page.getByRole("dialog", { name: "Import an Anki deck" });
      await again.locator("input[type=file]").setInputFiles(exported);
      await again.getByRole("button", { name: "Import deck" }).click();
      const second = page.getByRole("dialog", { name: "Import finished" });
      await expect(second.getByTestId("anki-report")).toContainText("0 cards added");
      await expect(second).toContainText("2 already in your library");
      await expectNoHorizontalOverflow(page);
      expect(errors, "console errors").toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a file that is not an Anki package says so", async ({ browser, baseURL }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info, "ankibad");
    try {
      const page = await student.open("/cards");
      await quiet(page);
      await page.getByRole("button", { name: "Import deck" }).first().click();
      const dialog = page.getByRole("dialog", { name: "Import an Anki deck" });
      await dialog.locator("input[type=file]").setInputFiles({
        name: "notes.apkg",
        mimeType: "application/octet-stream",
        buffer: Buffer.from("this is not a zip"),
      });
      await dialog.getByRole("button", { name: "Import deck" }).click();
      await expect(dialog.getByRole("alert")).toContainText("This is not an Anki package");
      await expectNoAxeViolations(page);
    } finally {
      await student.done();
    }
  });
});
