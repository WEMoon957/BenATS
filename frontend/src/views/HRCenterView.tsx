// 人事中台视图：员工、考勤与招聘三类摘要的一览看板（只读）。
// 数据来自 /api/hr/dashboard，顶栏「新建」工具条第五个工具入口。

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { t } from "../i18n";
import { registerView } from "../router";
import { Button } from "../ui/Button";

interface DepartmentCount {
  department: string;
  count: number;
}

interface EmployeesSummary {
  total: number;
  active: number;
  departments: DepartmentCount[];
}

interface AttendanceSummary {
  latest_period: string | null;
  attendance_rate: number;
  review_count: number;
  pending_cross_day: number;
}

interface StageCount {
  stage: string;
  label: string;
  count: number;
}

interface RecruitmentSummary {
  candidates: number;
  jobs: number;
  stages: StageCount[];
}

interface EmployeeSync {
  at: string;
  total: number;
  inserted: number;
  updated: number;
  skipped: number;
}

interface FeishuSummary {
  credentials_configured: boolean;
  employee_sync: EmployeeSync | null;
}

interface HrDashboard {
  employees: EmployeesSummary;
  attendance: AttendanceSummary;
  recruitment: RecruitmentSummary;
  feishu: FeishuSummary;
}

export interface HRCenterViewProps {
  onToast: (message: string) => void;
}

/** CountBars 用相对宽度条形展示标签 + 数量，复用考勤的条形样式。 */
function CountBars({ items }: { items: { label: string; count: number }[] }) {
  if (items.length === 0) return <p className="att-empty">{t("hrNoData")}</p>;
  const max = Math.max(...items.map((item) => item.count), 1);
  return (
    <ul className="att-bars">
      {items.map((item) => (
        <li key={item.label} className="att-bar-row">
          <span className="att-bar-name">{item.label}</span>
          <div className="att-bar-track">
            <div className="att-bar-fill" style={{ width: `${(item.count / max) * 100}%` }} />
          </div>
          <span className="att-bar-value">{item.count}</span>
        </li>
      ))}
    </ul>
  );
}

export function HRCenterView({ onToast }: HRCenterViewProps) {
  const [data, setData] = useState<HrDashboard | null>(null);
  const [syncing, setSyncing] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api<HrDashboard>("/api/hr/dashboard"));
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [onToast]);

  /** 按飞书通讯录刷新员工档案：同步成功后重新拉看板，让员工与部门分布即时反映结果。 */
  const syncEmployees = useCallback(async () => {
    setSyncing(true);
    try {
      const result = await api<EmployeeSync>("/api/hr/feishu-employees/sync", { method: "POST" });
      await load();
      onToast(
        t("hrFeishuSyncDone", {
          inserted: result.inserted,
          updated: result.updated,
          skipped: result.skipped,
        })
      );
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setSyncing(false);
    }
  }, [load, onToast]);

  useEffect(() => {
    registerView("hrcenter", { enter: () => void load() });
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!data) {
    return (
      <section id="hrCenterView" className="hr-center-view">
        <p className="att-empty">{t("hrLoading")}</p>
      </section>
    );
  }

  const employees = data.employees ?? { total: 0, active: 0, departments: [] };
  const attendance = data.attendance ?? {
    latest_period: null,
    attendance_rate: 0,
    review_count: 0,
    pending_cross_day: 0,
  };
  const recruitment = data.recruitment ?? { candidates: 0, jobs: 0, stages: [] };
  const feishu = data.feishu ?? { credentials_configured: false, employee_sync: null };
  const lastSync = feishu.employee_sync;

  return (
    <section id="hrCenterView" className="hr-center-view">
      <div className="hr-grid">
        <div className="boss-panel hr-panel">
          <div className="subsection-heading">
            <h3>{t("hrSectionEmployees")}</h3>
          </div>
          <div className="att-kpis">
            <div className="att-kpi">
              <span className="att-kpi-value">{employees.active}</span>
              <span className="att-kpi-label">{t("hrActiveEmployees")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{employees.total}</span>
              <span className="att-kpi-label">{t("hrTotalEmployees")}</span>
            </div>
          </div>
          <div className="hr-sync">
            <Button variant="secondary" busy={syncing} onClick={() => void syncEmployees()}>
              {syncing ? t("hrFeishuSyncing") : t("hrFeishuSyncEmployees")}
            </Button>
            <p className="hr-period">
              {t("hrFeishuLastSync")}：
              {lastSync
                ? t("hrFeishuSyncSummary", {
                    at: lastSync.at,
                    inserted: lastSync.inserted,
                    updated: lastSync.updated,
                    skipped: lastSync.skipped,
                  })
                : t("hrFeishuNeverSynced")}
            </p>
          </div>
          <div className="subsection-heading">
            <h3>{t("hrDepartments")}</h3>
          </div>
          <CountBars items={employees.departments.map((d) => ({ label: d.department, count: d.count }))} />
        </div>

        <div className="boss-panel hr-panel">
          <div className="subsection-heading">
            <h3>{t("hrSectionAttendance")}</h3>
          </div>
          <div className="att-kpis">
            <div className="att-kpi">
              <span className="att-kpi-value">{attendance.attendance_rate}%</span>
              <span className="att-kpi-label">{t("hrAttendanceRate")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{attendance.review_count}</span>
              <span className="att-kpi-label">{t("hrReviewCount")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{attendance.pending_cross_day}</span>
              <span className="att-kpi-label">{t("hrCrossDay")}</span>
            </div>
          </div>
          <p className="hr-period">
            {t("hrPeriod")}：{attendance.latest_period || t("hrNoPeriod")}
          </p>
        </div>

        <div className="boss-panel hr-panel">
          <div className="subsection-heading">
            <h3>{t("hrSectionRecruitment")}</h3>
          </div>
          <div className="att-kpis">
            <div className="att-kpi">
              <span className="att-kpi-value">{recruitment.candidates}</span>
              <span className="att-kpi-label">{t("hrCandidates")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{recruitment.jobs}</span>
              <span className="att-kpi-label">{t("hrJobs")}</span>
            </div>
          </div>
          <div className="subsection-heading">
            <h3>{t("hrStages")}</h3>
          </div>
          <CountBars items={recruitment.stages.map((s) => ({ label: s.label, count: s.count }))} />
        </div>
      </div>
    </section>
  );
}
