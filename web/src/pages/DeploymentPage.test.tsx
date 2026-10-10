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
};

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
    mocks.read.mockReturnValue({ data: deployment });
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
    mocks.read.mockReturnValue({
      data: deployment,
      error: new ConsoleApiError("connection_lost", true),
    });
    show();
    expect(screen.getByText("fixture-instance")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("连接中断");
  });

  it("shows an empty state when no response is available", () => {
    mocks.read.mockReturnValue({ data: undefined, isLoading: false });
    show();
    expect(screen.getByText("部署状态暂不可用")).toBeInTheDocument();
  });
});
