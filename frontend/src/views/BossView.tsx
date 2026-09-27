// 招聘接入（BOSS 直聘自动化）视图：自动化引擎状态面板 + 触达审核面板。

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { onChange, t } from "../i18n";
import { registerView } from "../router";
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

function kindLabel(kind: string): string {
  if (kind === "send") return t("outreachKindSend");
  if (kind === "action") return t("outreachKindAction");
  return t("outreachKindGreet");
}

function statusLabel(status: string): string {
  if (status === "sent") return t("outreachStatusSent");
  if (status === "failed") return t("outreachStatusFailed");
  if (status === "rejected") return t("outreachStatusRejected");
  return status;
}

export function BossView({ onToast }: BossViewProps) {
  const [running, setRunning] = useState(false);
  const [outreaches, setOutreaches] = useState<OutreachRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [, rerender] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [status, list] = await Promise.all([
        api<AutomationStatus>("/api/boss/automation/status"),
        api<{ outreaches: OutreachRecord[] }>("/api/boss/outreaches"),
      ]);
      setRunning(status.running);
      setOutreaches(list.outreaches || []);
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setLoading(false);
    }
  }, [onToast]);

  useEffect(() => {
    registerView("boss", { enter: () => void load() });
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

  const pending = outreaches.filter((item) => item.status === "pending");
  const processed = outreaches.filter((item) => item.status !== "pending");

  return (
    <section id="bossView" className="boss-view">
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
        <div className="subsection-heading">
          <h3>{t("bossOutreachReview")}</h3>
          <span>{t("bossPendingCount", { count: pending.length })}</span>
        </div>
        {pending.length === 0 ? (
          <p className="boss-empty">{t("bossPendingEmpty")}</p>
        ) : (
          <ul className="boss-outreach-list">
            {pending.map((item) => (
              <li className="boss-outreach-item" key={item.id}>
                <div className="boss-outreach-main">
                  <strong>{item.action.target}</strong>
                  <span className="boss-outreach-kind">{kindLabel(item.action.kind)}</span>
                  <span className="boss-outreach-meta">
                    {item.action.job_keyword ? `· ${item.action.job_keyword}` : ""}
                    {item.action.text ? `· ${item.action.text}` : ""}
                    {item.action.remark ? `· ${item.action.remark}` : ""}
                  </span>
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
                    <span className="boss-outreach-kind">{kindLabel(item.action.kind)}</span>
                  </div>
                  <span className={`boss-outreach-status is-${item.status}`}>{statusLabel(item.status)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}