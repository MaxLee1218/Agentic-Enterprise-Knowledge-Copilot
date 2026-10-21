import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import { ESLint } from "eslint";

const eslint = new ESLint();

test("the real Node screenshot script can be linted without TypeScript services", async () => {
  const results = await eslint.lintFiles([
    "scripts/capture-readme-screenshots.mjs",
  ]);
  assert.equal(results.length, 1);
  assert.equal(results[0].fatalErrorCount, 0);
  assert.equal(results[0].errorCount, 0, JSON.stringify(results[0].messages));
});

test("TypeScript application files still reject floating promises", async (context) => {
  // Type-aware single-run linting reads the on-disk project, including in CI.
  const directory = await mkdtemp(
    join(process.cwd(), "src/eslint-regression-"),
  );
  context.after(() => rm(directory, { recursive: true, force: true }));
  const fixture = join(directory, "floating.ts");
  await writeFile(fixture, "async function run(): Promise<void> {}\nrun();\n");
  const [result] = await eslint.lintFiles([fixture]);
  assert.equal(result.fatalErrorCount, 0);
  assert.ok(
    result.messages.some(
      (message) => message.ruleId === "@typescript-eslint/no-floating-promises",
    ),
    "Typed rules must still run for application TypeScript",
  );
});
