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
  it("hides a cached body when the object becomes inaccessible", () => {
    mocks.read.mockReturnValue({
      error: { message: "not_found", retryable: false },
      data: {
        meta: { observed_at: "2026-09-11T01:00:00Z" },
        data: {
          batch_id: "b",
          proposals: [
            { availability: "available", new_payload: "cached private body" },
          ],
        },
      },
    });
    show();
    expect(screen.getByRole("heading", {name:"对象不存在或不可访问"})).toBeInTheDocument();
    expect(screen.queryByText(/cached private/)).not.toBeInTheDocument();
  });
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
function showAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/:resource/:objectId" element={<DetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
const sessionData = (host: string) => ({
  meta: { observed_at: "2026-09-11T01:00:00Z" },
  data: {
    session_id: "s",
    host,
    opened_at: "2026-09-11T01:00:00Z",
    events: [],
    batches: [],
    candidates: [],
    bindings: [],
    truncated: [],
    injections: [
      {
        receipt_id: "r",
        created_at: "2026-09-11T01:00:00Z",
        status: "emitted",
        reason: null,
        memory_ids: ["m1", "m2"],
        revisions: [1, 3],
        summaries: ["测试统一放 tests/unit", null],
        omissions: [
          { memory_id: "o1", revision: 1, reason: "budget", summary: "超长的项目说明" },
          { memory_id: "o2", revision: 2, reason: "budget", summary: null },
        ],
        consumption_records: [],
      },
    ],
  },
});
describe("what a session carried in", () => {
  it("lists carried memories by content and groups omissions by translated reason", () => {
    mocks.read.mockReturnValue({ data: sessionData("codex") });
    showAt("/sessions/s");
    expect(screen.getByText("测试统一放 tests/unit")).toBeInTheDocument();
    expect(screen.getByText("无可读正文")).toBeInTheDocument();
    expect(screen.getByText("超出注入预算")).toBeInTheDocument();
    expect(screen.getByText("超长的项目说明")).toBeInTheDocument();
    expect(screen.getByText("正文不可见")).toBeInTheDocument();
    expect(screen.getByText("尚无消费证据")).toBeInTheDocument();
  });
  it("explains collection-only hosts instead of showing receipts", () => {
    mocks.read.mockReturnValue({ data: sessionData("claude-code") });
    showAt("/sessions/s");
    expect(screen.getByText(/此宿主仅采集事件，不注入记忆/)).toBeInTheDocument();
    expect(screen.queryByText("测试统一放 tests/unit")).not.toBeInTheDocument();
  });
});
describe("review guidance", () => {
  it("shows the existing CLI command for a batch awaiting review", () => {
    mocks.read.mockReturnValue({
      data: {
        meta: { observed_at: "2026-09-11T01:00:00Z" },
        data: { batch_id: "b-1", session_id: "s", status: "awaiting_review", attempt_count: 0, proposals: [] },
      },
    });
    showAt("/batches/b-1");
    expect(
      screen.getByText("bin/sagacontext-daemon-control review --batch-id b-1 --decision approve"),
    ).toBeInTheDocument();
  });
});
