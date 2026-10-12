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
const memories = {
  data: {
    data: {
      items: [
        {
          memory_id: "m1",
          summary: "测试统一放 tests/unit",
          scope_kind: "project",
          memory_type: "convention",
          created_at: "2026-09-11T01:00:00Z",
        },
      ],
    },
  },
};
function route(overview: unknown, memoryPage: unknown = memories) {
  mocks.read.mockImplementation((path: string) =>
    String(path).includes("/memories") ? memoryPage : overview,
  );
}
describe("overview states", () => {
  it("leads with memory content and moves operations behind one status line", () => {
    route({ data: snapshot() });
    show();
    expect(screen.getByText("测试统一放 tests/unit")).toBeInTheDocument();
    expect(screen.getByText("暂无关联任务")).toBeInTheDocument();
    expect(screen.getByText("未启用")).toBeInTheDocument();
    expect(screen.getByText("查看运维状态 →")).toBeInTheDocument();
    expect(screen.queryByText("误召回率")).not.toBeInTheDocument();
    expect(screen.queryByText(/配额占用/)).not.toBeInTheDocument();
  });
  it("does not present a failed module as a zero count", () => {
    const data = snapshot();
    data.data.memory_changes = { availability: "unavailable", value: null };
    route({ data });
    show();
    expect(screen.getByText("变更数据不可用")).toBeInTheDocument();
    expect(screen.queryByText("新增")).not.toBeInTheDocument();
  });
  it("marks collection-only hosts instead of claiming memories were carried in", () => {
    const data = snapshot();
    data.data.sessions = moduleValue({
      items: [
        {
          session_id: "s-claude",
          host: "claude-code",
          opened_at: "2026-09-11T01:00:00Z",
        },
        {
          session_id: "s-codex",
          host: "codex",
          opened_at: "2026-09-11T01:00:00Z",
          injected_count: 2,
        },
      ],
    });
    route({ data });
    show();
    expect(screen.getAllByText(/仅采集/).length).toBeGreaterThan(0);
    expect(screen.getByText(/带入 2 条记忆/)).toBeInTheDocument();
  });
  it("does not present a stale successful response as current health", () => {
    route({
      data: snapshot(),
      error: new ConsoleApiError("connection_lost", true),
    });
    show();
    expect(screen.getByText(/连接异常/)).toBeInTheDocument();
    expect(screen.queryByText(/服务可达/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在展示");
  });
  it("shows an empty memory state instead of a zero list", () => {
    route({ data: snapshot() }, { data: { data: { items: [] } } });
    show();
    expect(screen.getByText("还没有适用于本项目的记忆")).toBeInTheDocument();
  });
});
