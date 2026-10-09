// 人事中台视图：员工、考勤、招聘摘要与人员流动看板（只读），以及离职流程管理。
// 数据来自 /api/hr/dashboard 与 /api/hr/resignations；顶栏「新建」工具条的第五个工具入口。

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

interface AttendanceBatch {
  period: string;
  status: string;
  matched_rows: number;
  completed_at: string | null;
}

interface AttendanceFeishu {
  enabled: boolean;
  running: boolean;
  last_error: string;
  last_batch: AttendanceBatch | null;
}

interface AttendanceSummary {
  latest_period: string | null;
  attendance_rate: number;
  review_count: number;
  pending_cross_day: number;
  feishu: AttendanceFeishu;
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
  /** 没有工号、靠飞书用户 ID 建档的人数，这些人不会被飞书考勤按工号取到打卡 */
  fallback_user_id: number;
}

interface FeishuSummary {
  credentials_configured: boolean;
  employee_sync: EmployeeSync | null;
}

interface LifecycleMonth {
  month: string;
  onboard: number;
  offboard: number;
  headcount: number;
  turnover_rate: number;
}

interface LifecycleDepartment {
  department: string;
  active: number;
  onboard: number;
  offboard: number;
}

interface LifecycleEvent {
  id: number;
  employee_name: string;
  department: string;
  event_type: string;
  event_label: string;
  effective_date: string;
  source: string;
  reason: string;
}

interface LifecycleOps {
  public_base_url: string;
  reason_categories: string[];
  events: {
    configured: boolean;
    enabled: boolean;
    running: boolean;
    connected: boolean;
    last_error: string;
    last_event: { at: string; action: string; name: string } | null;
    stats: Record<string, number>;
  };
  offboard: { running: boolean; last_error: string; last_result: { processed: number } | null };
}

interface LifecycleSummary {
  active: number;
  total: number;
  current: { onboard: number; offboard: number; turnover_rate: number; headcount: number };
  months: LifecycleMonth[];
  reasons: { category: string; count: number }[];
  departments: LifecycleDepartment[];
  pending: { sent: number; submitted: number; confirmed: number; completed: number };
  recent_events: LifecycleEvent[];
  ops?: LifecycleOps;
}

interface HrDashboard {
  employees: EmployeesSummary;
  attendance: AttendanceSummary;
  recruitment: RecruitmentSummary;
  feishu: FeishuSummary;
  lifecycle?: LifecycleSummary;
}

interface ResignationRequest {
  id: number;
  employee_name: string;
  department: string;
  status: string;
  status_label: string;
  last_working_day: string | null;
  reason_category: string;
  deliver_status: string;
  deliver_error: string;
  /** 员工填报表单的令牌，用于拼出可转发/可复制的常驻链接 */
  token?: string;
}

interface EmployeeOption {
  id: number;
  name: string;
  employee_no: string;
  department: string;
}

interface DeliveryResult {
  delivered: boolean;
  manual: boolean;
  link: string;
  detail: string;
}

type LoadState = "loading" | "error" | "ready";

const EMPTY_LIFECYCLE: LifecycleSummary = {
  active: 0,
  total: 0,
  current: { onboard: 0, offboard: 0, turnover_rate: 0, headcount: 0 },
  months: [],
  reasons: [],
  departments: [],
  pending: { sent: 0, submitted: 0, confirmed: 0, completed: 0 },
  recent_events: [],
};

/** 离职列表状态筛选：all / todo 为聚合口径，其余与后端状态一一对应 */
const STATUS_FILTERS: { value: string; labelKey: string }[] = [
  { value: "all", labelKey: "hrResignFilterAll" },
  { value: "todo", labelKey: "hrResignFilterTodo" },
  { value: "sent", labelKey: "hrResignFilterSent" },
  { value: "submitted", labelKey: "hrResignFilterSubmitted" },
  { value: "confirmed", labelKey: "hrResignFilterConfirmed" },
  { value: "completed", labelKey: "hrResignFilterCompleted" },
  { value: "rejected", labelKey: "hrResignFilterRejected" },
];

const TODO_STATUSES = ["sent", "submitted", "confirmed"];

/** 最近动态前端限条：后端已按时间倒序，这里只取首屏够用的条数 */
const RECENT_EVENT_LIMIT = 8;

export type NavigateTool = "attendance" | "recruitment";

export interface HRCenterViewProps {
  onToast: (message: string) => void;
  /** 从卡片跳转到考勤 / 招聘工作台；未传时降级为纯文本提示 */
  onNavigate?: (tool: NavigateTool) => void;
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

/** NavHint：有 onNavigate 时渲染为可点按钮，否则退化为纯文本，保证容器视图不崩。 */
function NavHint({
  label,
  tool,
  onNavigate,
}: {
  label: string;
  tool: NavigateTool;
  onNavigate?: (tool: NavigateTool) => void;
}) {
  if (!onNavigate) return <p className="hr-nav-hint">{label}</p>;
  return (
    <button type="button" className="hr-nav-link" onClick={() => onNavigate(tool)}>
      {label}
    </button>
  );
}

export function HRCenterView({ onToast, onNavigate }: HRCenterViewProps) {
  const [data, setData] = useState<HrDashboard | null>(null);
  const [dashboardState, setDashboardState] = useState<LoadState>("loading");
  const [dashboardError, setDashboardError] = useState("");
  const [requests, setRequests] = useState<ResignationRequest[]>([]);
  const [requestsState, setRequestsState] = useState<LoadState>("loading");
  const [requestsError, setRequestsError] = useState("");
  const [employees, setEmployees] = useState<EmployeeOption[]>([]);
  const [rosterState, setRosterState] = useState<LoadState>("loading");
  const [rosterError, setRosterError] = useState("");
  const [selectedEmployee, setSelectedEmployee] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [employeeSyncError, setEmployeeSyncError] = useState("");
  const [syncingAttendance, setSyncingAttendance] = useState(false);
  const [busy, setBusy] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [failedOnly, setFailedOnly] = useState(false);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const loadDashboard = useCallback(async () => {
    setDashboardState("loading");
    try {
      const dashboard = await api<HrDashboard>("/api/hr/dashboard");
      setData(dashboard);
      const ops = dashboard.lifecycle?.ops;
      if (ops) setBaseUrl(ops.public_base_url ?? "");
      setDashboardError("");
      setDashboardState("ready");
    } catch (error) {
      setDashboardError((error as Error).message);
      setDashboardState("error");
    }
  }, []);

  const loadResignations = useCallback(async () => {
    setRequestsState("loading");
    try {
      const resignations = await api<{ requests?: ResignationRequest[] }>("/api/hr/resignations");
      setRequests(Array.isArray(resignations.requests) ? resignations.requests : []);
      setRequestsError("");
      setRequestsState("ready");
    } catch (error) {
      setRequestsError((error as Error).message);
      setRequestsState("error");
    }
  }, []);

  const loadRoster = useCallback(async () => {
    setRosterState("loading");
    try {
      const roster = await api<{ employees?: EmployeeOption[] }>("/api/attendance/employees?active=true");
      setEmployees(Array.isArray(roster.employees) ? roster.employees : []);
      setRosterError("");
      setRosterState("ready");
    } catch (error) {
      setRosterError((error as Error).message);
      setRosterState("error");
    }
  }, []);

  const load = useCallback(async () => {
    await Promise.all([loadDashboard(), loadResignations(), loadRoster()]);
  }, [loadDashboard, loadResignations, loadRoster]);

  /** 按飞书通讯录刷新员工档案：同步成功后重新拉看板，让员工与部门分布即时反映结果。 */
  const syncEmployees = useCallback(async () => {
    setSyncing(true);
    setEmployeeSyncError("");
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
      setEmployeeSyncError((error as Error).message);
    } finally {
      setSyncing(false);
    }
  }, [load, onToast]);

  /** 立即触发一轮飞书考勤同步：失败原因由后端 detail 返回，结果与状态回读看板。 */
  const syncAttendance = useCallback(async () => {
    setSyncingAttendance(true);
    try {
      const result = await api<{ ok: boolean; detail?: string; period?: string; matched_employees?: number }>(
        "/api/hr/feishu-sync",
        { method: "POST" }
      );
      await load();
      onToast(
        result.ok
          ? t("hrAttendanceSyncDone", { period: result.period ?? "", matched: result.matched_employees ?? 0 })
          : (result.detail ?? t("hrAttendanceSyncFailed"))
      );
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setSyncingAttendance(false);
    }
  }, [load, onToast]);

  /** 复制表单链接：优先用剪贴板 API，缺失或被拒时降级为提示手动复制（不抛错）。 */
  const copyLink = useCallback(
    async (link: string, id: number) => {
      const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
      if (clipboard && typeof clipboard.writeText === "function") {
        try {
          await clipboard.writeText(link);
          setCopiedId(id);
          onToast(t("hrResignCopied"));
          return;
        } catch {
          // 权限被拒或非安全上下文：继续走手动复制降级
        }
      }
      onToast(t("hrResignCopyManual"));
    },
    [onToast]
  );

  /** 发起离职流程：先确认，再建申请并把表单链接通过飞书发给员工；送达失败时把链接交给 HR 转发。 */
  const startResignation = useCallback(async () => {
    if (!selectedEmployee) return;
    const employee = employees.find((item) => String(item.id) === selectedEmployee);
    // 仅当用户明确取消（confirm 返回 false）时中止；未实现 confirm 的环境（返回 undefined）不阻断
    if (window.confirm(t("hrResignStartConfirm", { name: employee?.name ?? "" })) === false) return;
    setBusy("create");
    try {
      const result = await api<{ request: ResignationRequest; delivery: DeliveryResult }>(
        "/api/hr/resignations",
        { method: "POST", body: JSON.stringify({ employee_id: Number(selectedEmployee) }) }
      );
      setSelectedEmployee("");
      await load();
      const delivery = result.delivery;
      onToast(
        delivery?.delivered
          ? t("hrResignDelivered")
          : delivery?.link
            ? t("hrResignLinkManual", { link: delivery.link })
            : delivery?.detail || t("hrResignCreated")
      );
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy("");
    }
  }, [employees, load, onToast, selectedEmployee]);

  const actOnRequest = useCallback(
    async (id: number, action: "confirm" | "reject" | "resend") => {
      setBusy(`${action}-${id}`);
      try {
        const body = action === "confirm" ? JSON.stringify({}) : JSON.stringify({ note: "" });
        const result = await api<{ delivery?: DeliveryResult }>(`/api/hr/resignations/${id}/${action}`, {
          method: "POST",
          body,
        });
        await load();
        if (action === "confirm") onToast(t("hrResignConfirmed"));
        else if (action === "reject") onToast(t("hrResignRejected"));
        else
          onToast(
            result.delivery?.delivered
              ? t("hrResignDelivered")
              : result.delivery?.link
                ? t("hrResignLinkManual", { link: result.delivery.link })
                : result.delivery?.detail || t("hrResignCreated")
          );
      } catch (error) {
        onToast((error as Error).message);
      } finally {
        setBusy("");
      }
    },
    [load, onToast]
  );

  const saveLifecycleConfig = useCallback(async () => {
    setBusy("config");
    try {
      await api("/api/hr/lifecycle/config", {
        method: "PUT",
        body: JSON.stringify({ public_base_url: baseUrl }),
      });
      await load();
      onToast(t("hrLifecycleSaved"));
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy("");
    }
  }, [baseUrl, load, onToast]);

  const runOffboards = useCallback(
    async (count: number) => {
      // 不可逆操作：先按预计影响人数确认；仅当用户明确取消时中止
      if (window.confirm(t("hrLifecycleRunOffboardsConfirm", { count })) === false) return;
      setBusy("offboard");
      try {
        const result = await api<{ processed?: number }>("/api/hr/lifecycle/run-offboards", { method: "POST" });
        await load();
        onToast(t("hrLifecycleOffboardDone", { count: result.processed ?? 0 }));
      } catch (error) {
        onToast((error as Error).message);
      } finally {
        setBusy("");
      }
    },
    [load, onToast]
  );

  useEffect(() => {
    registerView("hrcenter", { enter: () => void load() });
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!data && dashboardState === "loading") {
    return (
      <section id="hrCenterView" className="hr-center-view">
        <p className="att-empty">{t("hrLoading")}</p>
      </section>
    );
  }

  if (!data) {
    return (
      <section id="hrCenterView" className="hr-center-view">
        <div className="boss-panel hr-panel hr-error-panel" role="alert">
          <p className="hr-error">
            {t("hrLoadError")}：{dashboardError}
          </p>
          <Button variant="secondary" onClick={() => void loadDashboard()}>
            {t("hrRetry")}
          </Button>
        </div>
      </section>
    );
  }

  const employeesSummary = data.employees ?? { total: 0, active: 0, departments: [] };
  const attendance = data.attendance ?? {
    latest_period: null,
    attendance_rate: 0,
    review_count: 0,
    pending_cross_day: 0,
    feishu: { enabled: false, running: false, last_error: "", last_batch: null },
  };
  const attendanceFeishu = attendance.feishu ?? {
    enabled: false,
    running: false,
    last_error: "",
    last_batch: null,
  };
  const attendanceState = !attendanceFeishu.enabled
    ? t("hrAttendanceSyncOff")
    : attendanceFeishu.last_batch
      ? t("hrAttendanceSyncBatch", {
          period: attendanceFeishu.last_batch.period,
          matched: attendanceFeishu.last_batch.matched_rows,
        })
      : t("hrAttendanceSyncNever");
  const recruitment = data.recruitment ?? { candidates: 0, jobs: 0, stages: [] };
  const feishu = data.feishu ?? { credentials_configured: false, employee_sync: null };
  const lastSync = feishu.employee_sync;

  const lifecycle = data.lifecycle ?? EMPTY_LIFECYCLE;
  const ops = lifecycle.ops;
  const flowMax = Math.max(
    1,
    ...lifecycle.months.flatMap((month) => [month.onboard, month.offboard])
  );
  const eventsStatus = !ops?.events?.configured
    ? t("hrLifecycleEventsNotConfigured")
    : ops.events.connected
      ? t("hrLifecycleEventsRunning")
      : ops.events.running
        ? t("hrLifecycleEventsConnecting")
        : t("hrLifecycleEventsIdle");

  // 首屏聚合口径：本月离职人数用 current.offboard；待办 = sent + submitted + confirmed；送达失败按列表实时统计
  const pendingTotal = lifecycle.pending.sent + lifecycle.pending.submitted + lifecycle.pending.confirmed;
  const deliverFailedCount = requests.filter((item) => item.deliver_status === "failed").length;
  const recentEvents = lifecycle.recent_events.slice(0, RECENT_EVENT_LIMIT);

  // 表单链接宿主：优先看板配置的对外地址，留空则回退当前站点 origin
  const formBase = (
    baseUrl.trim() || (typeof window !== "undefined" ? window.location.origin : "")
  ).replace(/\/+$/, "");
  const formLink = (token: string) => `${formBase}/resign/${token}`;

  const visibleRequests = requests.filter((item) => {
    if (failedOnly && item.deliver_status !== "failed") return false;
    if (statusFilter === "all") return true;
    if (statusFilter === "todo") return TODO_STATUSES.includes(item.status);
    return item.status === statusFilter;
  });

  const showEmptyRequests = statusFilter === "all" && !failedOnly;

  return (
    <section id="hrCenterView" className="hr-center-view">
      {dashboardState === "error" && (
        <div className="boss-panel hr-panel hr-error-panel" role="alert">
          <p className="hr-error">
            {t("hrLoadError")}：{dashboardError}
          </p>
          <Button variant="secondary" onClick={() => void loadDashboard()}>
            {t("hrRetry")}
          </Button>
        </div>
      )}
      <div className="boss-panel hr-panel hr-todo-bar">
        <div className="subsection-heading">
          <h3>{t("hrSectionTodo")}</h3>
        </div>
        <div className="att-kpis">
          <div className="att-kpi">
            <span className="att-kpi-value">{lifecycle.current.offboard}</span>
            <span className="att-kpi-label">{t("hrLifecycleOffboard")}</span>
          </div>
          <div className="att-kpi">
            <span className="att-kpi-value">{pendingTotal}</span>
            <span className="att-kpi-label">{t("hrLifecycleTodoTotal")}</span>
            <button
              type="button"
              className="hr-kpi-link"
              onClick={() => {
                setStatusFilter("todo");
                setFailedOnly(false);
              }}
            >
              {t("hrResignFilterTodo")}
            </button>
          </div>
          <div className="att-kpi">
            <span className="att-kpi-value">{requestsState === "ready" ? deliverFailedCount : "-"}</span>
            <span className="att-kpi-label">{t("hrLifecycleDeliverFailedCount")}</span>
            <button
              type="button"
              className="hr-kpi-link"
              onClick={() => {
                setFailedOnly(true);
                setStatusFilter("all");
              }}
            >
              {t("hrResignFilterFailed")}
            </button>
          </div>
        </div>
      </div>
      <div className="hr-grid">
        <div className="boss-panel hr-panel">
          <div className="subsection-heading">
            <h3>{t("hrSectionEmployees")}</h3>
          </div>
          <div className="att-kpis">
            <div className="att-kpi">
              <span className="att-kpi-value">{employeesSummary.active}</span>
              <span className="att-kpi-label">{t("hrActiveEmployees")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{employeesSummary.total}</span>
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
                  }) +
                  (lastSync.fallback_user_id
                    ? t("hrFeishuSyncFallback", { count: lastSync.fallback_user_id })
                    : "")
                : t("hrFeishuNeverSynced")}
            </p>
            {employeeSyncError && (
              <p className="hr-error" role="alert">
                {employeeSyncError}
              </p>
            )}
          </div>
          <div className="subsection-heading">
            <h3>{t("hrDepartments")}</h3>
          </div>
          <div className="hr-scroll hr-scroll-bars">
            <CountBars items={employeesSummary.departments.map((d) => ({ label: d.department, count: d.count }))} />
          </div>
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
          <div className="hr-sync">
            <Button variant="secondary" busy={syncingAttendance} onClick={() => void syncAttendance()}>
              {syncingAttendance ? t("hrAttendanceSyncing") : t("hrAttendanceSyncNow")}
            </Button>
            <p className="hr-period">{attendanceFeishu.last_error || attendanceState}</p>
          </div>
          {(attendance.review_count > 0 || attendance.pending_cross_day > 0) && (
            <NavHint label={t("hrNavAttendance")} tool="attendance" onNavigate={onNavigate} />
          )}
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
          <div className="hr-scroll hr-scroll-bars">
            <CountBars items={recruitment.stages.map((s) => ({ label: s.label, count: s.count }))} />
          </div>
          <NavHint label={t("hrNavRecruitment")} tool="recruitment" onNavigate={onNavigate} />
        </div>

        <div className="boss-panel hr-panel">
          <div className="subsection-heading">
            <h3>{t("hrSectionLifecycle")}</h3>
          </div>
          <div className="att-kpis">
            <div className="att-kpi">
              <span className="att-kpi-value">{lifecycle.current.onboard}</span>
              <span className="att-kpi-label">{t("hrLifecycleOnboard")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{lifecycle.current.turnover_rate}%</span>
              <span className="att-kpi-label">{t("hrLifecycleTurnover")}</span>
              <span className="hr-kpi-note">{t("hrLifecycleTurnoverBasis")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{lifecycle.current.headcount}</span>
              <span className="att-kpi-label">{t("hrLifecycleHeadcount")}</span>
            </div>
          </div>

          <div className="subsection-heading">
            <h3>{t("hrLifecycleTrend", { months: lifecycle.months.length })}</h3>
          </div>
          {lifecycle.months.length === 0 ? (
            <p className="att-empty">{t("hrNoData")}</p>
          ) : (
            <>
              <div className="hr-trend-legend">
                <span className="hr-trend-legend-item">
                  <span className="hr-trend-swatch is-in" aria-hidden="true" />
                  {t("hrLifecycleIn")}
                </span>
                <span className="hr-trend-legend-item">
                  <span className="hr-trend-swatch is-out" aria-hidden="true" />
                  {t("hrLifecycleOut")}
                </span>
              </div>
              <div className="hr-scroll hr-scroll-trend">
                <ul className="hr-trend">
                  {lifecycle.months.map((month) => (
                    <li key={month.month} className="hr-trend-row">
                      <span className="hr-trend-month">{month.month}</span>
                      <div className="hr-trend-bars" aria-hidden="true">
                        <span
                          className="hr-trend-in"
                          style={{ width: `${(month.onboard / flowMax) * 100}%` }}
                          title={`${t("hrLifecycleIn")} ${month.onboard}`}
                        />
                        <span
                          className="hr-trend-out"
                          style={{ width: `${(month.offboard / flowMax) * 100}%` }}
                          title={`${t("hrLifecycleOut")} ${month.offboard}`}
                        />
                      </div>
                      <span className="hr-trend-meta">
                        +{month.onboard} / −{month.offboard}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </>
          )}

          <div className="subsection-heading">
            <h3>{t("hrLifecyclePending")}</h3>
          </div>
          <div className="att-kpis">
            <div className="att-kpi">
              <span className="att-kpi-value">{lifecycle.pending.sent}</span>
              <span className="att-kpi-label">{t("hrLifecyclePendingSent")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{lifecycle.pending.submitted}</span>
              <span className="att-kpi-label">{t("hrLifecyclePendingSubmitted")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value">{lifecycle.pending.confirmed}</span>
              <span className="att-kpi-label">{t("hrLifecyclePendingConfirmed")}</span>
            </div>
          </div>

          <div className="subsection-heading">
            <h3>{t("hrLifecycleReasons")}</h3>
          </div>
          <div className="hr-scroll hr-scroll-bars">
            <CountBars items={lifecycle.reasons.map((r) => ({ label: r.category, count: r.count }))} />
          </div>

          <div className="subsection-heading">
            <h3>{t("hrLifecycleDeptFlow")}</h3>
          </div>
          {lifecycle.departments.length === 0 ? (
            <p className="att-empty">{t("hrNoData")}</p>
          ) : (
            <div className="hr-scroll hr-scroll-table">
              <table className="hr-table">
                <thead>
                  <tr>
                    <th>{t("hrLifecycleDept")}</th>
                    <th>{t("hrLifecycleActive")}</th>
                    <th>{t("hrLifecycleIn")}</th>
                    <th>{t("hrLifecycleOut")}</th>
                  </tr>
                </thead>
                <tbody>
                  {lifecycle.departments.map((dept) => (
                    <tr key={dept.department}>
                      <td>{dept.department}</td>
                      <td>{dept.active}</td>
                      <td>{dept.onboard}</td>
                      <td>{dept.offboard}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="subsection-heading">
            <h3>{t("hrLifecycleRecent")}</h3>
          </div>
          {recentEvents.length === 0 ? (
            <p className="att-empty">{t("hrNoData")}</p>
          ) : (
            <div className="hr-scroll hr-scroll-events">
              <ul className="hr-events">
                {recentEvents.map((event) => (
                  <li key={event.id} className={`hr-event is-${event.event_type}`}>
                    <span className="hr-event-date">{event.effective_date}</span>
                    <span className="hr-event-name">{event.employee_name}</span>
                    <span className="hr-event-tag">{event.event_label}</span>
                    <span className="hr-event-dept">{event.department}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="boss-panel hr-panel">
          <div className="subsection-heading">
            <h3>{t("hrSectionResignations")}</h3>
          </div>
          <div className="hr-resign-start">
            <select
              value={selectedEmployee}
              onChange={(event) => setSelectedEmployee(event.target.value)}
              aria-label={t("hrResignPickEmployee")}
            >
              <option value="">{t("hrResignPickEmployee")}</option>
              {employees.map((employee) => (
                <option key={employee.id} value={employee.id}>
                  {employee.name}
                  {employee.department ? ` · ${employee.department}` : ""}
                </option>
              ))}
            </select>
            <Button
              variant="primary"
              busy={busy === "create"}
              disabled={!selectedEmployee}
              onClick={() => void startResignation()}
            >
              {t("hrResignStart")}
            </Button>
          </div>
          {rosterState === "ready" && employees.length === 0 && (
            <p className="att-empty">{t("hrResignPendingEmpty")}</p>
          )}
          {rosterState === "error" && (
            <div className="hr-inline-error" role="alert">
              <p className="hr-error">
                {t("hrRosterLoadError")}：{rosterError}
              </p>
              <Button variant="secondary" onClick={() => void loadRoster()}>
                {t("hrRetry")}
              </Button>
            </div>
          )}

          <div className="hr-resign-filters">
            <label className="hr-resign-filter">
              <span>{t("hrResignFilterLabel")}</span>
              <select
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
                aria-label={t("hrResignFilterLabel")}
              >
                {STATUS_FILTERS.map((filter) => (
                  <option key={filter.value} value={filter.value}>
                    {t(filter.labelKey)}
                  </option>
                ))}
              </select>
            </label>
            <label className="hr-resign-filter hr-resign-filter-toggle">
              <input
                type="checkbox"
                checked={failedOnly}
                onChange={(event) => setFailedOnly(event.target.checked)}
              />
              <span>{t("hrResignFilterFailed")}</span>
            </label>
          </div>

          {requestsState === "error" ? (
            <div className="hr-inline-error" role="alert">
              <p className="hr-error">
                {t("hrResignLoadError")}：{requestsError}
              </p>
              <Button variant="secondary" onClick={() => void loadResignations()}>
                {t("hrRetry")}
              </Button>
            </div>
          ) : requestsState === "loading" ? (
            <p className="att-empty">{t("hrLoading")}</p>
          ) : visibleRequests.length === 0 ? (
            <p className="att-empty">{showEmptyRequests ? t("hrResignEmpty") : t("hrResignFilterEmpty")}</p>
          ) : (
            <div className="hr-scroll hr-resign-table-wrap">
              <table className="hr-table hr-resign-table">
                <thead>
                  <tr>
                    <th>{t("hrResignEmployee")}</th>
                    <th>{t("hrResignStatus")}</th>
                    <th>{t("hrResignReason")}</th>
                    <th>{t("hrResignLastDay")}</th>
                    <th>{t("hrResignLink")}</th>
                    <th>{t("hrResignOps")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRequests.map((request) => {
                    const link = request.token ? formLink(request.token) : "";
                    return (
                      <tr key={request.id}>
                        <td>
                          {request.employee_name}
                          <span className="hr-resign-dept">{request.department}</span>
                        </td>
                        <td>
                          <span className={`hr-badge is-${request.status}`}>{request.status_label}</span>
                          {request.deliver_status === "failed" && (
                            <span className="hr-badge is-deliver-failed">{t("hrResignDeliverFailed")}</span>
                          )}
                          {request.deliver_status === "manual" && (
                            <span className="hr-badge is-deliver-manual">{t("hrResignDeliverManual")}</span>
                          )}
                          {request.deliver_error && (
                            <details className="hr-resign-error">
                              <summary>{t("hrResignDeliverError")}</summary>
                              <span className="hr-resign-error-text">{request.deliver_error}</span>
                            </details>
                          )}
                        </td>
                        <td>{request.reason_category || "-"}</td>
                        <td>{request.last_working_day || "-"}</td>
                        <td className="hr-resign-link-cell">
                          {link ? (
                            <>
                              <a
                                className="hr-resign-link"
                                href={link}
                                target="_blank"
                                rel="noreferrer"
                                title={link}
                              >
                                {link}
                              </a>
                              <Button
                                variant="secondary"
                                className="hr-resign-copy"
                                onClick={() => void copyLink(link, request.id)}
                              >
                                {copiedId === request.id ? t("hrResignCopied") : t("hrResignCopyLink")}
                              </Button>
                            </>
                          ) : (
                            "-"
                          )}
                        </td>
                        <td className="hr-resign-actions">
                          {(request.status === "sent" || request.status === "submitted") && (
                            <Button
                              variant="secondary"
                              busy={busy === `confirm-${request.id}`}
                              onClick={() => void actOnRequest(request.id, "confirm")}
                            >
                              {t("hrResignConfirm")}
                            </Button>
                          )}
                          {request.status !== "completed" && request.status !== "rejected" && (
                            <>
                              <Button
                                variant="secondary"
                                busy={busy === `resend-${request.id}`}
                                onClick={() => void actOnRequest(request.id, "resend")}
                              >
                                {t("hrResignResend")}
                              </Button>
                              <Button
                                variant="danger"
                                busy={busy === `reject-${request.id}`}
                                onClick={() => void actOnRequest(request.id, "reject")}
                              >
                                {t("hrResignReject")}
                              </Button>
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="hr-resign-offboard">
            <Button
              variant="danger"
              busy={busy === "offboard"}
              disabled={lifecycle.pending.confirmed === 0}
              onClick={() => void runOffboards(lifecycle.pending.confirmed)}
            >
              {t("hrLifecycleRunOffboards")}
            </Button>
            <span className="hr-period">{t("hrLifecycleRunOffboardsHint")}</span>
          </div>
        </div>
      </div>
      <div className="hr-config-section">
        <div className="subsection-heading">
          <h3>{t("hrSectionConfig")}</h3>
        </div>
        <div className="boss-panel hr-panel">
          <div className="subsection-heading">
            <h3>{t("hrLifecycleEvents")}</h3>
          </div>
          <div className="att-kpis">
            <div className="att-kpi">
              <span className="att-kpi-value hr-status-value">{eventsStatus}</span>
              <span className="att-kpi-label">{t("hrLifecycleEventsStatus")}</span>
            </div>
            <div className="att-kpi">
              <span className="att-kpi-value hr-status-value">
                {ops?.events?.last_event ? ops.events.last_event.name || "-" : "-"}
              </span>
              <span className="att-kpi-label">{t("hrLifecycleLastEvent")}</span>
            </div>
          </div>
          <p className="hr-period">{t("hrLifecycleEventsHint")}</p>
          {ops?.events?.last_error && <p className="hr-error">{ops.events.last_error}</p>}
          {ops?.offboard?.last_error && <p className="hr-error">{ops.offboard.last_error}</p>}
          {ops?.events?.last_event && (
            <p className="hr-period">
              {ops.events.last_event.at} · {ops.events.last_event.name} · {ops.events.last_event.action}
            </p>
          )}
          <label className="field">
            <span>{t("hrLifecycleBaseUrl")}</span>
            <input
              value={baseUrl}
              placeholder={t("hrLifecycleBaseUrlPlaceholder")}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
          </label>
          <div className="hr-sync">
            <Button variant="secondary" busy={busy === "config"} onClick={() => void saveLifecycleConfig()}>
              {t("hrLifecycleSave")}
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
