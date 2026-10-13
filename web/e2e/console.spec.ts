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
  await expect(page.getByText("记录失败原因").first()).toBeVisible();
  await page.getByRole("link", { name: /查看运维状态/ }).click();
  await expect(page.getByRole("heading", { name: "运维状态" })).toBeVisible();
  await page.getByRole("link", { name: /1 个批次等待审核/ }).click();
  await expect(page.getByText("审核需在命令行完成")).toBeVisible();
  await expect(page.getByRole("combobox")).toHaveValue("awaiting_review");
  await page.getByRole("combobox").selectOption("");
  await expect(page.getByRole("link", { name: /批次 / }).first()).toBeVisible();
  await page.getByRole("combobox").selectOption("awaiting_review");
  // Let the filtered list settle before clicking; the unfiltered rows re-render otherwise.
  await expect(page).toHaveURL(/status=awaiting_review/);
  await expect(page.getByText("当前范围暂无记录")).toHaveCount(0);
  await page.getByRole("link", { name: /批次 / }).first().click();
  await expect(page.getByRole("heading", { name: "补全提案" })).toBeVisible();
  await page
    .getByRole("link", { name: /查看来源会话 ·/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "这次会话带入了什么" }),
  ).toBeVisible();
  await expect(page.getByText("尚无消费证据")).toBeVisible();
  await expect(page.getByText("超出注入预算")).toBeVisible();
  await page.getByRole("link", { name: "记录失败原因", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "记录失败原因" }),
  ).toBeVisible();
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
  // A background refetch can re-render the list under the first Tab; keyboard reach is what matters.
  await expect(async () => {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe(
      "BODY",
    );
  }).toPass({ timeout: 5000 });
});

test("disconnection keeps snapshot and marks current state unknown", async ({
  page,
}) => {
  await page.goto("/console/");
  await page.getByRole("link", { name: /查看运维状态/ }).click();
  await expect(page.getByText("服务可达", { exact: true })).toBeVisible();
  await page.route("**/console/v1/**", (route) => route.abort());
  // Refetch on focus instead of waiting for the polling interval, so a slow host cannot miss the window.
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.getByText("连接异常", { exact: true })).toBeVisible({
    timeout: 12000,
  });
  await expect(
    page.getByRole("status").filter({ hasText: "正在展示" }),
  ).toBeVisible();
  await expect(page.getByText("当前状态未知", { exact: true })).toBeVisible();
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
  await page.getByRole("link", { name: "运维", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "暂无运行记录" }),
  ).toBeVisible();
  await expect(page.getByText(/另一个工作区有未结束的运行/)).toBeVisible();
  await page.getByRole("link", { name: "◇ other", exact: true }).click();
  await expect(page.getByRole("heading", { name: "暂无会话" })).toBeVisible();
  await page.screenshot({ path: "test-results/empty.png", fullPage: true });
});

test("deployment shows hosts and onboarding without claiming hooks state", async ({
  page,
}) => {
  await page.goto("/console/deployment");
  await expect(page.getByRole("heading", { name: "接入进度" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "已接入宿主" })).toBeVisible();
  await expect(page.getByText("采集并注入")).toBeVisible();
  await expect(page.getByRole("link", { name: "返回项目目录" })).toBeVisible();
});
