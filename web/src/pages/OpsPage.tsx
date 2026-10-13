import { Link } from "react-router-dom";
import { useWorkspace } from "../App";
import { useRead } from "../api/queries";
import type { Envelope, Overview } from "../api/types";
import {
  Card,
  EmptyState,
  QualityRate,
  ReadNotice,
  Status,
  label,
  time,
} from "../components/Common";
import { Activity, useToday } from "./OverviewPage";

export default function OpsPage() {
  const { workspaceId, base } = useWorkspace();
  const window = useToday();
  const result = useRead<Envelope<Overview>>(
    `/workspaces/${workspaceId}/overview?${window}`,
  );
  const o = result.data?.data;
  if (!o)
    return (
      <>
        <ReadNotice error={result.error} />
        <EmptyState
          title={result.isLoading ? "正在读取运行状态" : "运行状态暂不可用"}
        />
      </>
    );
  const runtime = o.runtime.value;
  const run = o.rollout.value;
  const changes = o.memory_changes.value;
  const quality = run?.observations.wrong_recall;
  return (
    <>
      <div className="title-row">
        <div>
          <p className="eyebrow">OPERATIONS</p>
          <h1>运维状态</h1>
          <p className="muted">
            运行模式、配额、审核队列与投影，供操作员查看。
          </p>
        </div>
        <div className="snapshot">
          最近快照
          <br />
          <b>{time(result.data?.meta.observed_at)}</b>
        </div>
      </div>
      <ReadNotice
        error={result.error}
        observed={result.data?.meta.observed_at}
      />
      <div className="runtime-strip">
        <div>
          <span className={`dot ${result.error ? "lost" : ""}`} />
          <b>{result.error ? "连接异常" : "服务可达"}</b>
          <span className="sep">/</span>
          <span>
            调度器 {result.error ? "未知" : label(runtime?.scheduler)}
          </span>
        </div>
        <div>
          <Status value={result.error ? "unknown" : runtime?.effective_mode} />
          <span className="small">
            {result.error ? "当前状态未知" : label(runtime?.block_reason)}
          </span>
        </div>
      </div>
      {runtime?.other_workspace_id && (
        <div className="notice">
          另一个工作区有未结束的运行；当前工作区没有占用它的配额。
        </div>
      )}
      <div className="grid">
        <Card
          title="运行与待审核"
          to={run ? `${base}/rollouts/${run.rollout_id}` : `${base}/batches`}
        >
          {o.rollout.availability === "unavailable" ? (
            <p className="muted">运行数据不可用</p>
          ) : run ? (
            <>
              <div className="run-mode">
                <Status value={run.mode} />
                <Status value={run.status} />
              </div>
              <div className="quotas">
                {[
                  ["会话配额占用", run.session_reservations, run.session_limit],
                  [
                    "候选配额占用",
                    run.candidate_reservations,
                    run.candidate_limit,
                  ],
                ].map(([name, used, limit]) => (
                  <div key={name}>
                    <small>{name}</small>
                    <p>
                      <b>{used}</b> / {limit}
                    </p>
                    <meter value={Number(used)} max={Number(limit)} />
                  </div>
                ))}
                <div>
                  <small>截止时间</small>
                  <p>{time(run.deadline)}</p>
                </div>
              </div>
              <Link
                className="queue-link"
                to={`${base}/batches?status=awaiting_review`}
              >
                <span>
                  {run.batches.awaiting_review ?? 0} 个批次等待审核 ·
                  需在命令行完成
                </span>
                <span>查看提案与证据 →</span>
              </Link>
            </>
          ) : (
            <EmptyState title="暂无运行记录">
              已有任务和历史记忆仍可查看。
            </EmptyState>
          )}
        </Card>
        <Card title="投影与质量" to={`${base}/batches`}>
          {o.memory_changes.availability === "unavailable" ? (
            <p className="muted">投影数据不可用</p>
          ) : (
            <p className="small">
              当前投影：
              {Object.entries(changes?.projection_states ?? {})
                .map(([k, v]) => `${label(k)} ${v}`)
                .join(" · ") || "暂无投影记录"}
            </p>
          )}
          <div className="quality-line">
            <span>误召回率</span>
            {o.rollout.availability === "unavailable" ? (
              <span className="muted">质量数据不可用</span>
            ) : quality ? (
              <QualityRate {...quality} />
            ) : (
              <QualityRate yes={0} no={0} unknown={0} />
            )}
          </div>
        </Card>
      </div>
      <Card title="最近活动" to={`${base}/activity?${window}`}>
        {o.activity.value ? (
          <Activity items={o.activity.value.items} base={base} />
        ) : (
          <p className="muted">活动数据不可用</p>
        )}
      </Card>
    </>
  );
}
