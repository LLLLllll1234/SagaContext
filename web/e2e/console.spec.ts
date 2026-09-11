import { test, expect } from "@playwright/test";

test("responsive workspace grid and snapshot evidence", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/console/");
    await expect(
      page.getByRole("heading", { name: "工作区总览", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("演示环境 · 使用临时合成数据")).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth,
      ),
    ).toBe(false);
    await page.screenshot({
      path: `test-results/overview-${width}.png`,
      fullPage: true,
    });
  }
  expect(errors).toEqual([]);
});

test("review batch to evidence, session, memory and history", async ({
  page,
}) => {
  await page.goto("/console/");
  await page.getByRole("link", { name: /1 个批次等待审核/ }).click();
  await expect(page.getByRole("combobox")).toHaveValue("awaiting_review");
  await page.getByRole("link", { name: /批次 / }).first().click();
  await expect(page.getByRole("heading", { name: "补全提案" })).toBeVisible();
  await page
    .getByRole("link", { name: /查看来源会话 ·/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "记忆输出与消费证据" }),
  ).toBeVisible();
  await expect(page.getByText("消费记录：尚无证据")).toBeVisible();
  await page.getByRole("link", { name: /记忆 .*v1/ }).click();
  await expect(page.getByRole("heading", { name: "记忆作用域" })).toBeVisible();
  await page.screenshot({
    path: "test-results/memory-detail.png",
    fullPage: true,
  });
  await page.goBack();
  await expect(page.getByRole("heading", { name: "会话详情" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("heading", { name: "会话", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe(
    "BODY",
  );
});

test("disconnection keeps snapshot and marks current state unknown", async ({
  page,
}) => {
  await page.goto("/console/");
  await expect(page.getByText("服务可达", { exact: true })).toBeVisible();
  await page.route("**/console/v1/**", (route) => route.abort());
  await expect(page.getByText("连接异常", { exact: true })).toBeVisible({
    timeout: 12000,
  });
  await expect(
    page.getByRole("status").filter({ hasText: "正在展示" }),
  ).toBeVisible();
  await expect(page.getByText("当前状态未知", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "完善错误处理约定" }),
  ).toBeVisible();
  await expect(page.getByText("服务可达", { exact: true })).toHaveCount(0);
  await page.screenshot({
    path: "test-results/disconnected.png",
    fullPage: true,
  });
});

test("workspace without bindings does not inherit tasks or run quotas", async ({
  page,
}) => {
  await page.goto("/console/");
  await page.getByRole("link", { name: "◇ worktree", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "暂无关联任务" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "暂无运行记录" }),
  ).toBeVisible();
  await expect(page.getByText(/另一个工作区有未结束的运行/)).toBeVisible();
  await page.getByRole("link", { name: "◇ other", exact: true }).click();
  await expect(page.getByRole("heading", { name: "暂无会话" })).toBeVisible();
  await page.screenshot({ path: "test-results/empty.png", fullPage: true });
});
