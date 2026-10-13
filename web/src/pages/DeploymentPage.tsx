import { Link } from "react-router-dom";
import { useRead } from "../api/queries";
import type { DeploymentStatus, Directory, Envelope } from "../api/types";
import {
  Command,
  EmptyState,
  ReadNotice,
  Status,
  label,
} from "../components/Common";

const configured = (value: boolean) => (value ? "已配置" : "未配置");

type Step = {
  title: string;
  state: "done" | "todo" | "unknown";
  note: string;
  command?: string;
};
const mark = { done: "✓", todo: "○", unknown: "?" };
const stateLabel = {
  done: "已完成",
  todo: "未完成",
  unknown: "无法从控制台判断",
};

export function onboardingSteps(
  deployment: DeploymentStatus | undefined,
  directory: Directory | undefined,
  reachable: boolean,
): Step[] {
  const registered = directory
    ? directory.items.some((p) => p.workspaces.length)
    : undefined;
  return [
    {
      title: "本地服务可达",
      state: reachable ? "done" : "todo",
      note: reachable ? "控制台已读到部署状态。" : "启动仅绑定本机的服务。",
      command: reachable ? undefined : "./scripts/install.sh --start",
    },
    {
      title: "安装工作区 hooks",
      state: "unknown",
      note: "控制台不读取工作区文件，请在终端确认或安装。",
      command:
        "./scripts/install.sh --with-hooks --workspace /absolute/path/to/project",
    },
    {
      title: "注册项目与工作区",
      state:
        registered === undefined ? "unknown" : registered ? "done" : "todo",
      note: registered
        ? "已有可查看的工作区。"
        : "在 Ledger 中登记项目名称与仓库路径。",
      command: registered
        ? undefined
        : 'uv run sagactl project-register "项目名" /absolute/path/to/project',
    },
    {
      title: "选择运行模式",
      state: deployment && deployment.rollout_mode !== "off" ? "done" : "todo",
      note:
        deployment && deployment.rollout_mode !== "off"
          ? `当前为${label(deployment.rollout_mode)}模式。`
          : "默认关闭。开启观察或受控模式需要单独授权，见集成指南。",
    },
  ];
}
function Onboarding({ steps }: { steps: Step[] }) {
  return (
    <section className="onboarding" aria-label="接入进度">
      <h2>接入进度</h2>
      <ol>
        {steps.map((step) => (
          <li key={step.title} className={`step-${step.state}`}>
            <span className="step-mark" aria-label={stateLabel[step.state]}>
              {mark[step.state]}
            </span>
            <div>
              <b>{step.title}</b>
              <p className="small">{step.note}</p>
              {step.command && <Command text={step.command} />}
            </div>
          </li>
        ))}
      </ol>
      <p className="caption">控制台只读；复制按钮只复制命令，不会执行。</p>
    </section>
  );
}

export default function DeploymentPage() {
  const result = useRead<DeploymentStatus>("/deployment");
  const directory = useRead<Envelope<Directory>>("/projects");
  const deployment = result.data;
  const steps = onboardingSteps(
    deployment,
    directory.data?.data,
    Boolean(deployment) && !result.error,
  );

  return (
    <main className="deployment-page">
      <Link className="logo" to="/">
        Saga<span>Context</span>
        <i>记忆运行控制台</i>
      </Link>
      <div className="title-row">
        <div>
          <p className="eyebrow">DEPLOYMENT</p>
          <h1>部署状态</h1>
          <p className="muted">仅展示本地配置，不检查外部依赖是否可达。</p>
        </div>
        <Link className="button" to="/">
          返回项目目录
        </Link>
      </div>
      <ReadNotice error={result.error} />
      <Onboarding steps={steps} />
      {!deployment ? (
        <EmptyState
          title={result.isLoading ? "正在读取部署状态" : "部署状态暂不可用"}
        >
          本地服务恢复后可重新读取。
        </EmptyState>
      ) : (
        <>
          <section className="deployment-summary" aria-label="部署标识">
            <div>
              <span className="eyebrow">PRODUCT</span>
              <h2>{deployment.product}</h2>
              <p>版本 {deployment.version}</p>
            </div>
            <div>
              <span className="eyebrow">INSTANCE</span>
              <p className="mono">{deployment.instance_id}</p>
            </div>
            <div>
              <span className="eyebrow">MODE</span>
              <Status value={deployment.rollout_mode} />
            </div>
          </section>
          <section className="deployment-grid" aria-label="部署配置">
            <article>
              <h2>运行控制</h2>
              <dl>
                <div>
                  <dt>后台工作器</dt>
                  <dd>{configured(deployment.worker_enabled)}</dd>
                </div>
                <div>
                  <dt>调度器</dt>
                  <dd>
                    <Status value={deployment.scheduler} />
                  </dd>
                </div>
                <div>
                  <dt>停止开关</dt>
                  <dd>{deployment.stop_active ? "已生效" : "未生效"}</dd>
                </div>
              </dl>
            </article>
            <article>
              <h2>依赖配置</h2>
              <dl>
                <div>
                  <dt>OpenViking</dt>
                  <dd>{configured(deployment.openviking_configured)}</dd>
                </div>
                <div>
                  <dt>语言模型</dt>
                  <dd>{configured(deployment.llm_configured)}</dd>
                </div>
                <div>
                  <dt>控制台资源</dt>
                  <dd>
                    {deployment.console_assets_available ? "已打包" : "缺失"}
                  </dd>
                </div>
              </dl>
            </article>
            <article>
              <h2>已接入宿主</h2>
              {deployment.hosts.length ? (
                <dl>
                  {deployment.hosts.map((h) => (
                    <div key={h.host}>
                      <dt>
                        {label(h.host)}{" "}
                        <small className="mono">{h.frozen_version}</small>
                      </dt>
                      <dd>
                        {h.injection === "enabled"
                          ? "采集并注入"
                          : "仅采集，不注入"}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="muted">未配置宿主</p>
              )}
              <p className="caption">
                版本为窗口期冻结值；hooks 是否已装请在终端确认。
              </p>
            </article>
          </section>
        </>
      )}
    </main>
  );
}
