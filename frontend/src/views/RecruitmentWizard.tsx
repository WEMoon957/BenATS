// 招聘作业台四步向导：选岗位 → 招聘标准 → 执行方案 → 检查并启动（被动咨询）。

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { Button } from "../ui/Button";

interface Position {
  name: string;
  status: string;
}

interface Plan {
  id: number;
  job_keyword: string;
  mode: string;
  state: string;
}

interface CheckItem {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
}

const STEPS = [
  { key: 1, label: "职位与账号" },
  { key: 2, label: "招聘标准" },
  { key: 3, label: "执行方案" },
  { key: 4, label: "执行前检查" },
];

export function RecruitmentWizard({ onToast }: { onToast: (message: string) => void }) {
  const [step, setStep] = useState(1);
  const [positions, setPositions] = useState<Position[]>([]);
  const [jobKeyword, setJobKeyword] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [checks, setChecks] = useState<CheckItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const loadPositions = useCallback(async () => {
    try {
      const data = await api<Position[]>("/api/boss/positions");
      setPositions(Array.isArray(data) ? data : []);
    } catch {
      setPositions([]);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadPositions();
  }, [loadPositions]);

  const refreshChecks = useCallback(async (next: Plan) => {
    try {
      const data = await api<{ checks: CheckItem[] }>(`/api/recruitment/plans/${next.id}/checks`);
      setChecks(data.checks || []);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [onToast]);

  const createPlan = async () => {
    setBusy(true);
    try {
      const created = await api<Plan>("/api/recruitment/plans", {
        method: "POST",
        body: JSON.stringify({ job_keyword: jobKeyword, mode: "passive" }),
      });
      setPlan(created);
      setStep(2);
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toCheck = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      await refreshChecks(plan);
      setStep(4);
    } finally {
      setBusy(false);
    }
  };

  const startPlan = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      const data = await api<{ plan: Plan; checks: CheckItem[] }>(
        `/api/recruitment/plans/${plan.id}/start`,
        { method: "POST" },
      );
      setPlan(data.plan);
      setChecks(data.checks || []);
      onToast("作业已启动，系统开始自动执行");
    } catch (error) {
      onToast((error as Error).message);
      await refreshChecks(plan);
    } finally {
      setBusy(false);
    }
  };

  const allPass = checks.length > 0 && checks.every((c) => c.ok);

  return (
    <div className="rec-view">
      <ol className="wizard-steps">
        {STEPS.map((s) => (
          <li key={s.key} className={step === s.key ? "is-active" : step > s.key ? "is-done" : ""}>
            <span className="wizard-step-num">{s.key}</span>
            <span>{s.label}</span>
          </li>
        ))}
      </ol>

      <div className="boss-panel">
        {step === 1 && (
          <>
            <div className="subsection-heading"><h3>选择在招职位</h3></div>
            <p className="boss-lead">选择 BOSS 直聘在招职位，发起本次招聘作业。</p>
            {loaded && positions.length === 0 && (
              <p className="boss-empty">未拉取到职位，请确认 boss-cli 已登录并同步职位。</p>
            )}
            <select
              className="rubric-select"
              value={jobKeyword}
              onChange={(e) => setJobKeyword(e.target.value)}
            >
              <option value="">选择职位…</option>
              {positions.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}{p.status ? `（${p.status}）` : ""}
                </option>
              ))}
            </select>
            <div className="boss-actions" style={{ marginTop: 10 }}>
              <Button variant="primary" busy={busy} disabled={!jobKeyword} onClick={() => void createPlan()}>
                下一步：招聘标准
              </Button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <div className="subsection-heading"><h3>招聘标准</h3></div>
            <p className="boss-lead">系统将根据岗位「{plan?.job_keyword}」的 JD 自动生成评分标准，用于后续简历筛选。</p>
            <p className="boss-lead">需要手动校准标准时，可在「评分标准」标签页完成。</p>
            <div className="boss-actions">
              <Button variant="secondary" onClick={() => setStep(1)}>上一步</Button>
              <Button variant="primary" onClick={() => setStep(3)}>下一步：执行方案</Button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <div className="subsection-heading"><h3>执行方案</h3></div>
            <p className="boss-lead">被动咨询：系统自动完成「拉候选人 → 打招呼 → 求简历 → 下载评分」。</p>
            <ul className="outreach-plan">
              <li><strong>打招呼</strong>：对推荐候选人自动点击 BOSS「打招呼」。</li>
              <li><strong>求简历</strong>：对方未发简历时自动发送默认话术。</li>
              <li><strong>下载评分</strong>：简历到手后自动下载并评分，S / A 级进入电话约谈。</li>
            </ul>
            <div className="boss-actions" style={{ marginTop: 10 }}>
              <Button variant="secondary" onClick={() => setStep(2)}>上一步</Button>
              <Button variant="primary" busy={busy} onClick={() => void toCheck()}>检查并开始执行</Button>
            </div>
          </>
        )}

        {step === 4 && (
          <>
            <div className="subsection-heading"><h3>执行前检查</h3></div>
            <p className="boss-lead">岗位「{plan?.job_keyword}」 · 被动咨询。检查通过后才会启动作业。</p>
            <ul className="wizard-checks">
              {checks.map((c) => (
                <li key={c.key}>
                  <span className="wizard-check-label">{c.label}</span>
                  <span className={`boss-outreach-status ${c.ok ? "is-sent" : "is-failed"}`}>
                    {c.ok ? "通过" : "未通过"}
                  </span>
                  {c.detail && <span className="wizard-check-detail">{c.detail}</span>}
                </li>
              ))}
            </ul>
            {checks.length === 0 && <p className="boss-empty">检查项加载中…</p>}
            <div className="boss-actions" style={{ marginTop: 10 }}>
              <Button variant="secondary" onClick={() => setStep(3)}>上一步</Button>
              <Button variant="primary" busy={busy} disabled={!allPass} onClick={() => void startPlan()}>
                开始执行
              </Button>
            </div>
            {checks.length > 0 && !allPass && (
              <p className="boss-guide is-attention">存在未通过项，请先处理后再开始执行。</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
