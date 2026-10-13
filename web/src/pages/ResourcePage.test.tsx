import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResourcePage from "./ResourcePage";

const mocks = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("../App", () => ({
  useWorkspace: () => ({
    workspaceId: "w",
    projectId: "p",
    base: "/projects/p/workspaces/w",
  }),
}));
vi.mock("../api/queries", () => ({ useRead: mocks.read }));
function Where() {
  return <span data-testid="where">{useLocation().search}</span>;
}
function show(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path="/:resource"
          element={
            <>
              <ResourcePage />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}
const row = (id: string, extra: object) => ({
  session_id: id,
  host: "codex",
  opened_at: "2026-09-11T01:00:00Z",
  ...extra,
});
beforeEach(() => mocks.read.mockReset());
describe("session injection filter", () => {
  it("passes the filter to the API and labels each row by its latest receipt", () => {
    mocks.read.mockReturnValue({
      data: {
        meta: { observed_at: "2026-09-11T01:00:00Z" },
        data: {
          items: [
            row("s1", {
              injection_status: "emitted",
              injected_count: 3,
              omitted_count: 1,
            }),
            row("s2", { injection_status: "blocked", injected_count: 0 }),
            row("s3", {}),
          ],
        },
      },
    });
    show("/sessions?injection=blocked");
    expect(mocks.read.mock.calls[0][0]).toContain("injection=blocked");
    expect(screen.getByText(/带入 3 条 · 省略 1 条/)).toBeInTheDocument();
    expect(screen.getByText(/最近一次注入被拦截/)).toBeInTheDocument();
    expect(screen.getByText(/无注入回执/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "none" },
    });
    expect(screen.getByTestId("where")).toHaveTextContent("injection=none");
  });
});
