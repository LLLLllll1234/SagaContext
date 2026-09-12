import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, it, expect, vi } from "vitest";
import OverviewPage from "./OverviewPage";
import { ConsoleApiError } from "../api/client";

const mocks = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("../App", () => ({
  useWorkspace: () => ({
    workspaceId: "w",
    projectId: "p",
    base: "/projects/p/workspaces/w",
  }),
}));
vi.mock("../api/queries", () => ({ useRead: mocks.read }));
const moduleValue = (value: unknown) => ({ availability: "available", value });
function snapshot() {
  return {
    meta: { observed_at: "2026-09-11T01:00:00Z" },
    data: {
      runtime: moduleValue({
        effective_mode: "off",
        scheduler: "disabled",
        block_reason: "configured_off",
      }),
      tasks: moduleValue({ items: [] }),
      sessions: moduleValue({ items: [] }),
      rollout: moduleValue(null),
      memory_changes: moduleValue({
        operations: { new: 2 },
        projection_states: {},
      }),
      activity: moduleValue({ items: [] }),
    },
  };
}
function show() {
  render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
}
beforeEach(() => mocks.read.mockReset());
describe("overview states", () => {
  it("shows off and no-run independently without inventing progress or quality", () => {
    mocks.read.mockReturnValue({ data: snapshot() });
    show();
    expect(screen.getByText("未启用")).toBeInTheDocument();
    expect(screen.getByText("暂无运行记录")).toBeInTheDocument();
    expect(screen.getByText("暂无关联任务")).toBeInTheDocument();
    expect(screen.getByText(/待采样/)).toBeInTheDocument();
  });
  it("does not present a failed module as a zero count", () => {
    const data = snapshot();
    data.data.memory_changes = { availability: "unavailable", value: null };
    mocks.read.mockReturnValue({ data });
    show();
    expect(screen.getByText("变更数据不可用")).toBeInTheDocument();
    expect(screen.queryByText("新增")).not.toBeInTheDocument();
  });
  it("does not present a stale successful response as current health", () => {
    mocks.read.mockReturnValue({
      data: snapshot(),
      error: new ConsoleApiError("connection_lost", true),
    });
    show();
    expect(screen.getByText("连接异常")).toBeInTheDocument();
    expect(screen.getByText("当前状态未知")).toBeInTheDocument();
    expect(screen.queryByText("服务可达")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在展示");
  });
});
