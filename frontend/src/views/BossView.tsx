// 招聘接入（BOSS 直聘自动化）视图：自动化引擎状态面板 + 触达审核面板。

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { onChange, t } from "../i18n";
import { Button } from "../ui/Button";
import { StatusDot } from "../ui/StatusDot";

interface AutomationStatus {
  running: boolean;
}

interface OutreachAction {
  kind: string;
  target: string;
  text?: string;
  request_resume?: boolean;
  job_keyword?: string;
  command?: string;
  remark?: string;
}

interface OutreachRecord {
  id: string;
  action: OutreachAction;
  status: string;
  result: string;
  error: string;
}

export interface BossViewProps {
  onToast: (message: string) => void;
}

function kindLabel(action: OutreachAction): string {
  if (action.kind === "action" && action.command === "request-attachment-resume") {
    return t("outreachKindRequestResume");
  }
  if (action.kind === "action" && action.command === "agree-resume") {
    return t("outreachKindAgreeResume");
  }
  if (action.kind === "send") return t("outreachKindSend");
  if (action.kind === "action") return t("outreachKindAction");
  return t("outreachKindGreet");
}

function statusLabel(status: string): string {
  if (status === "sent") return t("outreachStatusSent");
  if (status === "failed") return t("outreachStatusFailed");
  if (status === "rejected") return t("outreachStatusRejected");
  return status;
}

function actionDetail(action: OutreachAction): string {
  if (action.kind === "action" && action.command === "request-attachment-resume") {
    return "将发送默认话术「方便发一份你的简历过来吗？」";
  }
  if (action.kind === "action" && action.command === "agree-resume") {
    return "将同意接收对方发来的附件简历";
  }
  if (action.kind === "send") {
    return action.text ? `将发送：${action.text}` : "将发送消息";
  }
  if (action.kind === "action" && action.remark) {
    return `备注：${action.remark}`;
  }
  return "点击 BOSS「打招呼」按钮";
}

export function BossView({ onToast }: BossViewProps) {
  const [running, setRunning] = useState(false);
  const [outreaches, setOutreaches] = useState<OutreachRecord[]>([]);
  const [candidates, setCandidates] = useState<{ stage: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [approvingAll, setApprovingAll] = useState(false);
  const [, rerender] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [status, list, cands] = await Promise.all([
        api<AutomationStatus>("/api/boss/automation/status"),
        api<{ outreaches: OutreachRecord[] }>("/api/boss/outreaches"),
        api<{ candidates: { stage: string }[] }>("/api/recruitment/candidates"),
      ]);
      setRunning(status.running);
      setOutreaches(list.outreaches || []);
      setCandidates(cands.candidates || []);
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setLoading(false);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    onChange(() => {
      if (active) rerender((n) => n + 1);
    });
    return () => {
      active = false;
    };
  }, []);

  const runOnce = async () => {
    try {
      await api("/api/boss/automation/run", { method: "POST" });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    }
  };

  const toggleEngine = async () => {
    try {
      await api(`/api/boss/automation/${running ? "stop" : "start"}`, { method: "POST" });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    }
  };

  const decide = async (id: string, decision: "approve" | "reject") => {
    setBusyId(id);
    try {
      await api(`/api/boss/outreaches/${id}/${decision}`, { method: "POST" });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const approveAll = async () => {
    const count = pending.length;
    if (count === 0) return;
    if (!window.confirm(t("bossApproveAllConfirm", { count }))) return;
    setApprovingAll(true);
    try {
      const result = await api<{ sent: number; failed: number }>("/api/boss/outreaches/approve-all", { method: "POST" });
      onToast(t("bossApproveAllDone", { sent: result.sent, failed: result.failed }));
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setApprovingAll(false);
    }
  };

  const pending = outreaches.filter((item) => item.status === "pending");
  const processed = outreaches.filter((item) => item.status !== "pending");

  const stageCount = (stages: string[]) => candidates.filter((c) => stages.includes(c.stage)).length;
  const flow = [
    { label: "拉候选人", count: stageCount(["discovered", "greeting_pending"]) },
    { label: "已打招呼", count: stageCount(["greeted"]) },
    { label: "已收简历", count: stageCount(["resume_received"]) },
    { label: "已评分", count: stageCount(["screening", "screened"]) },
    { label: "面试/结束", count: stageCount(["interviewing", "offered", "rejected", "skipped"]) },
  ];

  return (
    <div className="boss-view">
      <div className="boss-panel">
        <div className="subsection-heading">
          <h3>{t("bossAutomation")}</h3>
          <span className="boss-engine-state">
            <StatusDot status={running ? "ready" : "error"} />
            {running ? t("bossEngineRunning") : t("bossEngineStopped")}
          </span>
        </div>
        <p className="boss-lead">{t("bossAutomationLead")}</p>
        <div className="boss-actions">
          <Button variant="primary" busy={loading} onClick={() => void toggleEngine()}>
            {running ? t("bossStop") : t("bossStart")}
          </Button>
          <Button variant="secondary" disabled={loading} onClick={() => void runOnce()}>
            {t("bossRunOnce")}
          </Button>
          <Button variant="secondary" disabled={loading} onClick={() => void load()}>
            {t("bossRefresh")}
          </Button>
        </div>
      </div>

      <div className="boss-panel">
        <div className="subsection-heading"><h3>触达方案 · 新媒体运营总监</h3></div>
        <p className="boss-lead">系统自动完成「拉候选人 → 打招呼 → 求简历 → 下载评分」，你只需审核触达动作、看评分结果。</p>
        <div className="outreach-flow">
          {flow.map((step, i) => (
            <div className="outreach-flow-item" key={step.label}>
              {i > 0 && <span className="outreach-flow-arrow">→</span>}
              <div className="outreach-flow-step">
                <span className="outreach-flow-count">{step.count}</span>
                <span className="outreach-flow-label">{step.label}</span>
              </div>
            </div>
          ))}
        </div>
        <ul className="outreach-plan">
          <li><strong>打招呼</strong>：对「拉候选人」自动点击 BOSS「打招呼」（消耗打招呼次数）。</li>
          <li><strong>求简历</strong>：打招呼后对方未发简历时，自动发默认话术「方便发一份你的简历过来吗？」。</li>
          <li><strong>下载 + 评分</strong>：对方发来简历后自动下载进系统并评分，S / A 级进入电话约谈。</li>
        </ul>
        <div className={`boss-guide${pending.length > 0 ? " is-attention" : ""}`}>
          <strong>你现在要做的：</strong>
          {pending.length > 0 ? `有 ${pending.length} 个触达动作待你审核（见下方）` : "暂无待审核，系统自动运行中"}
        </div>
      </div>

      <div className="boss-panel">
        <div className="subsection-heading">
          <h3>{t("bossOutreachReview")}</h3>
          <span>{t("bossPendingCount", { count: pending.length })}</span>
          <Button
            variant="primary"
            busy={approvingAll}
            disabled={loading || pending.length === 0}
            onClick={() => void approveAll()}
          >
            {t("bossApproveAll")}
          </Button>
        </div>
        {pending.length === 0 ? (
          <p className="boss-empty">{t("bossPendingEmpty")}</p>
        ) : (
          <ul className="boss-outreach-list">
            {pending.map((item) => (
              <li className="boss-outreach-item" key={item.id}>
                <div className="boss-outreach-main">
                  <div className="boss-outreach-title">
                    <strong>{item.action.target}</strong>
                    <span className="boss-outreach-kind">{kindLabel(item.action)}</span>
                  </div>
                  {item.action.job_keyword ? <span className="boss-outreach-meta">岗位：{item.action.job_keyword}</span> : null}
                  <span className="boss-outreach-detail">{actionDetail(item.action)}</span>
                </div>
                <div className="boss-outreach-actions">
                  <Button variant="primary" busy={busyId === item.id} onClick={() => void decide(item.id, "approve")}>
                    {t("bossApprove")}
                  </Button>
                  <Button variant="danger" disabled={busyId === item.id} onClick={() => void decide(item.id, "reject")}>
                    {t("bossReject")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {processed.length > 0 && (
          <>
            <div className="subsection-heading boss-processed-heading">
              <h3>{t("bossProcessed")}</h3>
            </div>
            <ul className="boss-outreach-list">
              {processed.map((item) => (
                <li className="boss-outreach-item is-processed" key={item.id}>
                  <div className="boss-outreach-main">
                    <strong>{item.action.target}</strong>
                    <span className="boss-outreach-kind">{kindLabel(item.action)}</span>
                  </div>
                  <span className={`boss-outreach-status is-${item.status}`}>{statusLabel(item.status)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}