import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleApiError } from "../api/client";
import DeploymentPage from "./DeploymentPage";

const mocks = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("../api/queries", () => ({ useRead: mocks.read }));

const deployment = {
  product: "sagacontext" as const,
  version: "1.0.0" as const,
  instance_id: "fixture-instance",
  rollout_mode: "off" as const,
  worker_enabled: false,
  scheduler: "disabled" as const,
  stop_active: false,
  openviking_configured: false,
  llm_configured: true,
  console_assets_available: true,
  hosts: [
    {
      host: "claude-code",
      frozen_version: "2.1.285",
      injection: "collection_only" as const,
    },
    { host: "codex", frozen_version: "0.159.2", injection: "enabled" as const },
  ],
};
const directory = (items: unknown[]) => ({ data: { data: { items } } });
function route(
  deploymentResult: unknown,
  directoryResult: unknown = directory([]),
) {
  mocks.read.mockImplementation((path: string) =>
    path === "/projects" ? directoryResult : deploymentResult,
  );
}

function show() {
  render(
    <MemoryRouter>
      <DeploymentPage />
    </MemoryRouter>,
  );
}

beforeEach(() => mocks.read.mockReset());

describe("deployment status", () => {
  it("shows configuration facts without describing dependency reachability", () => {
    route({ data: deployment });
    show();
    expect(
      screen.getByRole("heading", { name: "部署状态" }),
    ).toBeInTheDocument();
    expect(screen.getByText("fixture-instance")).toBeInTheDocument();
    expect(screen.getByText("已配置")).toBeInTheDocument();
    expect(
      screen.getByText("仅展示本地配置，不检查外部依赖是否可达。"),
    ).toBeInTheDocument();
  });

  it("keeps a stale response visible and marks the current read as unknown", () => {
    route({
      data: deployment,
      error: new ConsoleApiError("connection_lost", true),
    });
    show();
    expect(screen.getByText("fixture-instance")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("连接中断");
  });

  it("shows an empty state when no response is available", () => {
    route({ data: undefined, isLoading: false });
    show();
    expect(screen.getByText("部署状态暂不可用")).toBeInTheDocument();
    expect(
      screen.getByText("./scripts/install.sh --start"),
    ).toBeInTheDocument();
  });

  it("states each host's injection capability and frozen version", () => {
    route({ data: deployment });
    show();
    expect(screen.getByText("2.1.285")).toBeInTheDocument();
    expect(screen.getByText("仅采集，不注入")).toBeInTheDocument();
    expect(screen.getByText("采集并注入")).toBeInTheDocument();
  });

  it("never claims hooks are installed and offers commands for unfinished steps", () => {
    route({ data: deployment });
    show();
    const hooks = screen.getByText("安装工作区 hooks").closest("li")!;
    expect(hooks).toHaveClass("step-unknown");
    expect(screen.getByText(/sagactl project-register/)).toBeInTheDocument();
  });

  it("marks registration done once a workspace exists", () => {
    route(
      { data: deployment },
      directory([
        {
          project_id: "p",
          name: "示例",
          workspaces: [{ workspace_id: "w", name: "main" }],
        },
      ]),
    );
    show();
    expect(screen.getByText("注册项目与工作区").closest("li")).toHaveClass(
      "step-done",
    );
    expect(
      screen.queryByText(/sagactl project-register/),
    ).not.toBeInTheDocument();
  });
});
