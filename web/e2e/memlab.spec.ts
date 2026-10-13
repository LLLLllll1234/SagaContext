import { test, expect } from "@playwright/test";

const tabs: Record<string, Record<string, RegExp>> = {
  codex: {
    overview: /Codex 是怎么/,
    assets: /真正注入每个新会话的/,
    pipeline: /逐会话回放/,
    context: /短期上下文：增长、压缩/,
    source: /源码对照：/,
  },
  claude: {
    overview: /Claude Code 的记忆/,
    assets: /自动记忆文件/,
    pipeline: /形成过程/,
    context: /短期上下文（Claude Code）/,
    source: /源码对照/,
  },
};

test("every Memory Lab tab renders resolved parameters without errors", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const [product, pages] of Object.entries(tabs)) {
    for (const [tab, heading] of Object.entries(pages)) {
      await page.goto(`/memlab/#${product}/${tab}`);
      await expect(page.locator("#main h1, #main h2").first()).toHaveText(
        heading,
      );
      await expect(page.getByText("正在读取本地数据…")).toHaveCount(0);
      const text = await page.locator("#main").innerText();
      expect(text, `${product}/${tab}`).not.toContain("${");
      expect(text, `${product}/${tab}`).not.toContain("undefined");
      expect(text, `${product}/${tab}`).not.toContain("读取失败");
    }
  }
  expect(errors).toEqual([]);
});

test("Codex narrative uses source parameters and marks them unverified", async ({
  page,
}) => {
  await page.goto("/memlab/#codex/overview");
  await expect(
    page.getByText("memory_summary 截断到 2500 tokens 注入 developer 指令"),
  ).toBeVisible();
  await page.goto("/memlab/#codex/assets");
  await expect(page.getByText("⚠ 未复核").first()).toBeVisible();
});

test("Memory Lab and the console link to each other", async ({ page }) => {
  await page.goto("/memlab/");
  await page.getByRole("link", { name: /记忆运行控制台/ }).click();
  await expect(
    page.getByRole("heading", { name: "工作区总览", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: /Memory Lab/ }).click();
  await expect(page.getByText("SagaContext Memory Lab")).toBeVisible();
});
