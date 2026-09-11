import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import DetailPage from "./DetailPage";

const mocks = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("../App", () => ({
  useWorkspace: () => ({
    workspaceId: "w",
    projectId: "p",
    base: "/projects/p/workspaces/w",
  }),
}));
vi.mock("../api/queries", () => ({ useRead: mocks.read }));
function show() {
  render(
    <MemoryRouter initialEntries={["/batches/b"]}>
      <Routes>
        <Route path="/:resource/:objectId" element={<DetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
describe("proposal evidence display", () => {
  it("hides every payload of an unavailable proposal", () => {
    mocks.read.mockReturnValue({
      data: {
        meta: { observed_at: "2026-09-11T01:00:00Z" },
        data: {
          batch_id: "b",
          status: "settled",
          proposals: [
            {
              proposal_id: "p",
              operation: "new",
              availability: "unavailable",
              old_payload: "private old",
              new_payload: "private new",
              evidence: [],
            },
          ],
        },
      },
    });
    show();
    expect(
      screen.getByText("内容已删除或不在当前作用域。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/private/)).not.toBeInTheDocument();
  });
  it("does not invent a source link or replace an unavailable prior revision", () => {
    mocks.read.mockReturnValue({
      data: {
        meta: { observed_at: "2026-09-11T01:00:00Z" },
        data: {
          batch_id: "b",
          status: "awaiting_review",
          proposals: [
            {
              proposal_id: "p",
              operation: "refine",
              availability: "available",
              old_payload: null,
              new_payload: { rule: "建议规则" },
              evidence: [
                {
                  evidence_id: "e",
                  kind: "user_statement",
                  excerpt: "证据摘录",
                  source: null,
                },
              ],
            },
          ],
        },
      },
    });
    show();
    expect(screen.getByText("旧版本不可用")).toBeInTheDocument();
    expect(screen.getByText("来源不可定位")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /查看来源会话 ·/ }),
    ).not.toBeInTheDocument();
  });
});
