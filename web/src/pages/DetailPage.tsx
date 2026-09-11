import { useEffect } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useWorkspace } from "../App";
import { useRead } from "../api/queries";
import type {
  Envelope,
  TaskRow,
  Batch,
  Memory,
  Session,
  Rollout,
  Evidence,
} from "../api/types";
import {
  Card,
  Counts,
  EmptyState,
  Payload,
  QualityRate,
  ReadNotice,
  RevisionDiff,
  Status,
  label,
  time,
} from "../components/Common";

function EvidenceList({ items, base }: { items: Evidence[]; base: string }) {
  return (
    <div className="evidence">
      {items.length ? (
        items.map((e) => (
          <article key={e.evidence_id}>
            <span className="eyebrow">来源证据 · {label(e.kind)}</span>
            <p>{e.excerpt ?? "没有可显示的摘录"}</p>
            {e.source ? (
              <Link to={`${base}/sessions/${e.source.session_id}`}>
                查看来源会话 · {time(e.source.received_at)} →
              </Link>
            ) : (
              <small>来源不可定位</small>
            )}
          </article>
        ))
      ) : (
        <p className="muted">没有可显示的证据</p>
      )}
    </div>
  );
}
export default function DetailPage() {
  const { projectId, workspaceId, base } = useWorkspace();
  const { resource, objectId } = useParams();
  const [search, setSearch] = useSearchParams();
  const navigate = useNavigate();
  const q = new URLSearchParams(search);
  q.set("project_id", projectId);
  const endpoint =
    resource === "tasks"
      ? `/projects/${projectId}/tasks/${objectId}?workspace_id=${workspaceId}`
      : `/workspaces/${workspaceId}/${resource}/${objectId}?${q}`;
  const result =
    useRead<Envelope<Batch | Memory | Session | Rollout | TaskRow>>(endpoint);
  const data = result.data?.data;
  useEffect(() => {
    if (data && "batch_id" in data && data.task_id && !search.has("task_id")) {
      const next = new URLSearchParams(search);
      next.set("task_id", data.task_id);
      setSearch(next, { replace: true });
    }
  }, [data, search, setSearch]);
  const close = () =>
    navigate(`${base}/${resource === "rollouts" ? "batches" : resource}`);
  useEffect(() => {
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [base, resource]);
  if (!data)
    return (
      <>
        <ReadNotice error={result.error} />
        <EmptyState
          title={result.isLoading ? "正在读取详情" : "对象不存在或不可访问"}
        />
      </>
    );
  return (
    <>
      <div className="title-row">
        <div>
          <p className="eyebrow">RECORD DETAILS</p>
          <h1>
            {(
              {
                tasks: "任务详情",
                sessions: "会话详情",
                memories: "记忆详情",
                batches: "批次详情",
                rollouts: "运行详情",
              } as Record<string, string>
            )[resource ?? ""] ?? "详情"}
          </h1>
          <p className="small mono">{objectId}</p>
        </div>
        <button className="button" onClick={() => navigate(-1)}>
          ← 返回
        </button>
      </div>
      <ReadNotice
        error={result.error}
        observed={result.data?.meta.observed_at}
      />
      {"goal" in data && (
        <Card title={data.goal}>
          <Status value={data.status} />
          <p>项目任务 · 最近活动 {time(data.last_active)}</p>
          <p>最近检查点 {time(data.checkpoint_at)}</p>
          {data.session_id && (
            <Link to={`${base}/sessions/${data.session_id}?${q}`}>
              关联会话 →
            </Link>
          )}
        </Card>
      )}
      {"batch_id" in data && (
        <>
          <Card title="批次状态">
            <Status value={data.status} />
            <p className="small">
              创建于 {time(data.created_at)} · 尝试 {data.attempt_count} 次
            </p>
            {data.last_error_class && (
              <p className="warning">{data.last_error_class}</p>
            )}
            <Link to={`${base}/sessions/${data.session_id}?${q}`}>
              查看来源会话 →
            </Link>
          </Card>
          {data.proposals.map((p) => (
            <Card key={p.proposal_id} title={`${label(p.operation)}提案`}>
              <div className="record-head">
                <span className="small mono">{p.proposal_id}</span>
                <Status value={p.status} />
              </div>
              {p.availability === "unavailable" ? (
                <p className="muted">内容已删除或不在当前作用域。</p>
              ) : (
                <>
                  <p>{p.rationale}</p>
                  {p.scope && (
                    <p className="small">
                      作用域：{label(p.scope.kind)}{" "}
                      {p.scope.path_pattern ?? p.scope.task_id ?? ""}
                    </p>
                  )}
                  <RevisionDiff
                    oldText={p.old_payload}
                    newText={p.new_payload}
                  />
                  {p.target_id && (
                    <Link to={`${base}/memories/${p.target_id}?${q}`}>
                      目标记忆 · 期望版本 {p.expected_revision ?? "—"} →
                    </Link>
                  )}
                  <EvidenceList items={p.evidence} base={base} />
                </>
              )}
            </Card>
          ))}
        </>
      )}
      {"revisions" in data && (
        <>
          {data.relations.length > 0 && (
            <Card title="取代关系">
              {data.relations.map((r) => (
                <p key={r.memory_id}>
                  <Link to={`${base}/memories/${r.memory_id}?${q}`}>
                    {r.relation === "replaces" ? "取代了" : "被取代为"}{" "}
                    {r.memory_id} →
                  </Link>
                </p>
              ))}
            </Card>
          )}
          <Card title="记忆作用域">
            <Status value={data.state} /> <Status value={data.scope.kind} />{" "}
            <Status value={data.conflict_state} />
            <p className="small">
              {data.scope.path_pattern ??
                data.scope.task_id ??
                "遵循项目与用户范围"}
            </p>
          </Card>
          {data.revisions.map((rev) => (
            <Card
              key={rev.revision}
              title={`版本 ${rev.revision} · ${label(rev.operation)}`}
            >
              <p className="small">{time(rev.created_at)}</p>
              <Payload value={rev.payload} />
              <EvidenceList items={rev.evidence} base={base} />
            </Card>
          ))}
        </>
      )}
      {"injections" in data && (
        <>
          {data.truncated.length > 0 && (
            <div className="notice">
              以下记录仅展示最近 100 条：
              {data.truncated
                .map(
                  (k) =>
                    (
                      ({
                        events: "事件",
                        batches: "批次",
                        candidates: "候选",
                        injections: "注入回执",
                      }) as Record<string, string>
                    )[k],
                )
                .join("、")}
              。
            </div>
          )}
          <Card title="会话状态">
            <p>
              {data.host} · {time(data.opened_at)} —{" "}
              {data.closed_at ? time(data.closed_at) : "尚无结束事件"}
            </p>
            {data.bindings.map((b) => (
              <p key={b.start_event_id}>
                <Link to={`${base}/tasks/${b.task_id}`}>
                  关联任务 {b.task_id.slice(0, 8)} →
                </Link>
              </p>
            ))}
          </Card>
          <Card title="事件记录">
            {data.events.map((e) => (
              <div className="list-row" key={e.event_id}>
                <Status value={e.event_kind} />
                <span className="small">
                  收到 {time(e.received_at)} · 发生 {time(e.occurred_at)}
                </span>
              </div>
            ))}
          </Card>
          <Card title="候选与批次">
            {data.candidates.map((c) => (
              <div className="list-row" key={c.candidate_id}>
                <span className="mono">{c.candidate_id.slice(0, 8)}</span>
                <Status value={c.status} />
                {c.active_batch_id && (
                  <Link to={`${base}/batches/${c.active_batch_id}?${q}`}>
                    查看批次 →
                  </Link>
                )}
              </div>
            ))}
            {data.batches.map((b) => (
              <p key={b.batch_id}>
                <Link to={`${base}/batches/${b.batch_id}?${q}`}>
                  批次 {b.batch_id.slice(0, 8)} →
                </Link>{" "}
                <Status value={b.status} />
              </p>
            ))}
          </Card>
          <Card title="记忆输出与消费证据">
            {!data.injections.length && <p className="muted">暂无注入回执。</p>}
            {data.injections.map((i) => (
              <article className="record" key={i.receipt_id}>
                <Status value={i.status} />
                <p className="small">
                  快照序列 {i.ledger_sequence} · {time(i.created_at)}
                </p>
                {i.reason && <p>{label(i.reason)}</p>}
                {i.memory_ids.map((m, index) => (
                  <p key={m}>
                    <Link to={`${base}/memories/${m}?${q}`}>
                      记忆 {m.slice(0, 8)} · v{i.revisions[index]} →
                    </Link>
                  </p>
                ))}
                <p>
                  消费记录：
                  {i.consumption_records.length
                    ? `${i.consumption_records.length} 条`
                    : "尚无证据"}
                </p>
                <p className="small">预算明细：当前回执未记录</p>
                {i.omissions.map((o, index) => (
                  <p key={index} className="small">
                    省略 {o.memory_id.slice(0, 8)} · {o.reason}
                  </p>
                ))}
              </article>
            ))}
          </Card>
        </>
      )}
      {"session_limit" in data && (
        <>
          <div className="grid">
            <Card title="运行状态">
              <Status value={data.mode} /> <Status value={data.status} />
              <p>开始 {time(data.started_at)}</p>
              <p>截止 {time(data.deadline)}</p>
              <p>停止原因 {label(data.stop_reason)}</p>
            </Card>
            <Card title="配额占用">
              <p>
                会话 {data.session_reservations} / {data.session_limit}
              </p>
              <p>
                候选 {data.candidate_reservations} / {data.candidate_limit}
              </p>
              <p>消费记录 {data.consumption_reports} 条</p>
            </Card>
            <Card title="批次状态">
              <Counts values={data.batches} />
            </Card>
            <Card title="当前投影">
              <Counts values={data.projection_states} />
            </Card>
          </div>
          <Card title="已标注对象的质量观察">
            {Object.entries(data.observations).map(([kind, values]) => (
              <div className="list-row" key={kind}>
                <span>
                  {(
                    {
                      candidate_valid: "候选有效",
                      review_modified: "审核修改",
                      wrong_recall: "错误召回",
                      consumed: "观察到消费",
                    } as Record<string, string>
                  )[kind] ?? kind}
                </span>
                <QualityRate {...values} />
              </div>
            ))}
            <p className="caption">
              仅统计已标注子集；宿主消费记录与人工观察分别展示。
            </p>
          </Card>
          <Card title="Judge 延迟">
            <p>
              样本 {data.judge_latency_ms.samples} · P50{" "}
              {data.judge_latency_ms.p50 ?? "—"} ms · P95{" "}
              {data.judge_latency_ms.p95 ?? "—"} ms
            </p>
          </Card>
          <Card title="停止与清理">
            <Counts values={data.rollback.states} />
            <div className="audit-grid">
              {Object.entries(data.rollback.audit).map(([key, value]) => (
                <p key={key}>
                  <span>{key}</span>
                  <b>{value}</b>
                </p>
              ))}
            </div>
            <p className="caption">
              审计留存与可召回残留分别统计；这里展示已有记录。
            </p>
          </Card>
        </>
      )}
    </>
  );
}
