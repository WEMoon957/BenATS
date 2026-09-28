// 考勤管理视图：账号登录 + 看板/人员/导入/结果/跨日审核/设置 六个子视图。
// 考勤模块有独立账号体系，会话 token 存 localStorage，请求携带 X-Attendance-Token。

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api/client";
import { onChange, t } from "../i18n";
import { registerView } from "../router";
import { Button } from "../ui/Button";

const ATT_TOKEN_KEY = "talentHub.attendanceToken";

export interface AttendanceViewProps {
  onToast: (message: string) => void;
}

type AttView = "dashboard" | "employees" | "imports" | "results" | "suspicions" | "settings";

interface Account {
  id: number;
  username: string;
  role: string;
  department: string;
  is_active: boolean;
}

interface Policy {
  id: number;
  code: string;
  name: string;
  mode: string;
  cross_day_cutoff_minutes: number;
  active: boolean;
}

interface Employee {
  id: number;
  employee_no: string;
  name: string;
  aliases: string[];
  department: string;
  position: string;
  join_date: string | null;
  employment_status: string;
  active: boolean;
  attendance_policy_id: number | null;
  tags: { id: number; name: string; color: string }[];
  phone: string;
}

interface ImportBatch {
  id: number;
  original_filename: string;
  year: number;
  month: number;
  default_expected_days: number;
  status: string;
  total_rows: number;
  matched_rows: number;
  unmatched_rows: number;
  suspicion_count: number;
  error_message: string;
  created_at: string;
}

interface ResultRow {
  id: number;
  batch_id: number;
  employee_id: number;
  emp_no: string;
  emp_name: string;
  emp_department: string;
  punch_days: number;
  due_days: number;
  rest_days: number;
  actual_days: number;
  adjustment_days: number;
  late_count: number;
  status: string;
  rule_trace: Record<string, unknown>;
}

interface Suspicion {
  id: number;
  emp_name: string;
  emp_no: string;
  previous_date: string;
  work_date: string;
  punch_text: string;
  reason: string;
  status: string;
}

interface Dashboard {
  batch: ImportBatch | null;
  batches: ImportBatch[];
  available_periods: { value: string; label: string; batch_id: number }[];
  selected_department: string;
  available_departments: string[];
  kpis: { employees: number; attendance_rate: number; review_count: number; pending_cross_day: number; unmatched_rows: number };
  summary: { total_rows: number; matched_rows: number; unmatched_rows: number; suspicion_count: number };
  daily: { date: string; count: number; rate: number }[];
  departments: { department: string; employees: number; attendance_rate: number; review_count: number }[];
}

const ROLE_LABELS: Record<string, string> = {
  admin: "系统管理员",
  hr: "HR",
  supervisor: "部门主管",
  viewer: "只读",
};
const WRITE_ROLES = new Set(["admin", "hr"]);

const MODE_LABELS: Record<string, string> = {
  standard: "标准考勤",
  flexible: "弹性工作",
  exempt: "免考勤",
  part_time: "兼职",
  shift: "轮班",
};

const EMPLOYMENT_LABELS: Record<string, string> = {
  probation: "试用期",
  regular: "已转正",
  founder: "创始人",
  part_time: "兼职",
  left: "已离职",
};

function attHeaders(): Record<string, string> {
  const token = localStorage.getItem(ATT_TOKEN_KEY) || "";
  return token ? { "X-Attendance-Token": token } : {};
}

async function attApi<T = unknown>(path: string, options: Parameters<typeof api>[1] = {}): Promise<T> {
  return api<T>(path, { ...options, headers: attHeaders() });
}

// ---- 每日出勤率折线图（纯 SVG）----
function DailyChart({ daily }: { daily: Dashboard["daily"] }) {
  const width = 720;
  const height = 180;
  const padX = 28;
  const padY = 20;
  if (daily.length === 0) {
    return <p className="att-empty">{t("attNoData")}</p>;
  }
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const maxRate = 100;
  const points = daily.map((item, index) => {
    const x = padX + (daily.length === 1 ? innerW / 2 : (index / (daily.length - 1)) * innerW);
    const y = padY + innerH - (item.rate / maxRate) * innerH;
    return { x, y, item };
  });
  const polyline = points.map((p) => `${p.x},${p.y}`).join(" ");
  return (
    <svg className="att-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("attDailyRate")}>
      <line x1={padX} y1={padY + innerH} x2={width - padX} y2={padY + innerH} className="att-chart-axis" />
      <line x1={padX} y1={padY} x2={padX} y2={padY + innerH} className="att-chart-axis" />
      <text x={width - padX} y={padY + innerH + 14} className="att-chart-label" textAnchor="end">
        {points.length > 0 ? points[points.length - 1].item.date.slice(5) : ""}
      </text>
      <text x={padX} y={padY + innerH + 14} className="att-chart-label">
        {points.length > 0 ? points[0].item.date.slice(5) : ""}
      </text>
      <polyline points={polyline} className="att-chart-line" />
      {points.map((p) => (
        <circle key={p.item.date} cx={p.x} cy={p.y} r={2.6} className="att-chart-dot">
          <title>{`${p.item.date} · ${p.item.count} 人 · ${p.item.rate}%`}</title>
        </circle>
      ))}
    </svg>
  );
}

// ---- 部门对比条形图 ----
function DepartmentBars({ departments }: { departments: Dashboard["departments"] }) {
  if (departments.length === 0) return <p className="att-empty">{t("attNoData")}</p>;
  return (
    <ul className="att-bars">
      {departments.map((dept) => (
        <li key={dept.department} className="att-bar-row">
          <span className="att-bar-name">{dept.department}</span>
          <div className="att-bar-track">
            <div className="att-bar-fill" style={{ width: `${dept.attendance_rate}%` }} />
          </div>
          <span className="att-bar-value">{dept.attendance_rate}%</span>
        </li>
      ))}
    </ul>
  );
}

export function AttendanceView({ onToast }: AttendanceViewProps) {
  const [account, setAccount] = useState<Account | null>(null);
  const [view, setView] = useState<AttView>("dashboard");
  const [booting, setBooting] = useState(true);
  const [, rerender] = useState(0);

  const refreshAccount = useCallback(async () => {
    try {
      const me = await attApi<Account>("/api/attendance/me");
      setAccount(me);
    } catch {
      localStorage.removeItem(ATT_TOKEN_KEY);
      setAccount(null);
    }
  }, []);

  useEffect(() => {
    registerView("attendance", { enter: () => void refreshAccount() });
  }, [refreshAccount]);

  useEffect(() => {
    let active = true;
    onChange(() => {
      if (active) rerender((n) => n + 1);
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      if (!localStorage.getItem(ATT_TOKEN_KEY)) {
        setAccount(null);
        setBooting(false);
        return;
      }
      try {
        const me = await attApi<Account>("/api/attendance/me");
        if (active) setAccount(me);
      } catch {
        localStorage.removeItem(ATT_TOKEN_KEY);
        if (active) setAccount(null);
      } finally {
        if (active) setBooting(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  if (booting) {
    return <section id="attendanceView" className="att-view" />;
  }

  if (!account) {
    return (
      <section id="attendanceView" className="att-view">
        <LoginPanel onLogin={setAccount} onToast={onToast} />
      </section>
    );
  }

  const canWrite = WRITE_ROLES.has(account.role);
  const navItems: { key: AttView; label: string }[] = [
    { key: "dashboard", label: t("attNavDashboard") },
    { key: "employees", label: t("attNavEmployees") },
    { key: "imports", label: t("attNavImports") },
    { key: "results", label: t("attNavResults") },
    { key: "suspicions", label: t("attNavSuspicions") },
    { key: "settings", label: t("attNavSettings") },
  ];

  return (
    <section id="attendanceView" className="att-view">
      <div className="att-header">
        <div className="att-user">
          <span className="att-user-name">{account.username}</span>
          <span className="att-user-role">{ROLE_LABELS[account.role] || account.role}</span>
        </div>
        <div className="att-header-actions">
          <ChangePasswordButton onToast={onToast} />
          <Button
            variant="secondary"
            onClick={() => {
              void attApi("/api/attendance/logout", { method: "POST" }).finally(() => {
                localStorage.removeItem(ATT_TOKEN_KEY);
                setAccount(null);
              });
            }}
          >
            {t("attLogout")}
          </Button>
        </div>
      </div>
      <div className="att-layout">
        <nav className="att-nav" aria-label={t("attNavLabel")}>
          {navItems.map((item) => (
            <button
              key={item.key}
              type="button"
              className={view === item.key ? "att-nav-item active" : "att-nav-item"}
              onClick={() => setView(item.key)}
            >
              {item.label}
              {item.key === "suspicions" && <SuspicionBadge />}
            </button>
          ))}
        </nav>
        <div className="att-content">
          {view === "dashboard" && <DashboardPanel canWrite={canWrite} onToast={onToast} />}
          {view === "employees" && <EmployeesPanel canWrite={canWrite} onToast={onToast} />}
          {view === "imports" && <ImportsPanel canWrite={canWrite} onToast={onToast} />}
          {view === "results" && <ResultsPanel canWrite={canWrite} onToast={onToast} />}
          {view === "suspicions" && <SuspicionsPanel canWrite={canWrite} onToast={onToast} />}
          {view === "settings" && <SettingsPanel canWrite={canWrite} onToast={onToast} />}
        </div>
      </div>
    </section>
  );
}

// ---- 登录 ----

function LoginPanel({ onLogin, onToast }: { onLogin: (a: Account) => void; onToast: (m: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await api<{ token: string; account: Account }>("/api/attendance/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      localStorage.setItem(ATT_TOKEN_KEY, result.token);
      onLogin(result.account);
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="att-login">
      <div className="att-login-card">
        <h2>{t("attLoginTitle")}</h2>
        <p className="att-login-lead">{t("attLoginLead")}</p>
        <form onSubmit={(e) => void submit(e)} className="att-login-form">
          <label className="field">
            <span>{t("attUsername")}</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </label>
          <label className="field">
            <span>{t("attPassword")}</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>
          <Button variant="primary" type="submit" busy={busy}>
            {t("attLogin")}
          </Button>
        </form>
      </div>
    </div>
  );
}

function ChangePasswordButton({ onToast }: { onToast: (m: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        {t("attChangePassword")}
      </Button>
      {open && <ChangePasswordDialog onClose={() => setOpen(false)} onToast={onToast} />}
    </>
  );
}

function ChangePasswordDialog({ onClose, onToast }: { onClose: () => void; onToast: (m: string) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await attApi("/api/attendance/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      onToast(t("attPasswordChanged"));
      onClose();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="att-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="att-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>{t("attChangePassword")}</h3>
        <label className="field">
          <span>{t("attCurrentPassword")}</span>
          <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("attNewPassword")}</span>
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <div className="att-modal-actions">
          <Button variant="secondary" onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button variant="primary" busy={busy} disabled={next.length < 6} onClick={() => void submit()}>
            {t("save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---- 看板 ----

function SuspicionBadge() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    attApi<{ suspicions: Suspicion[] }>("/api/attendance/suspicions?status=pending")
      .then((r) => setCount(r.suspicions.length))
      .catch(() => setCount(0));
  }, []);
  if (count === 0) return null;
  return <span className="att-badge">{count}</span>;
}

function DashboardPanel({ canWrite, onToast }: { canWrite: boolean; onToast: (m: string) => void }) {
  const [data, setData] = useState<Dashboard | null>(null);
  const [frm, setFrm] = useState("");
  const [to, setTo] = useState("");
  const [department, setDepartment] = useState("");

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (frm) params.set("from", frm);
      if (to) params.set("to", to);
      if (department) params.set("department", department);
      const q = params.toString();
      setData(await attApi<Dashboard>(`/api/attendance/dashboard${q ? `?${q}` : ""}`));
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [frm, to, department, onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!data) return <p className="att-empty">{t("attLoading")}</p>;
  const k = data.kpis;

  return (
    <div>
      <div className="att-filter-row">
        <input type="month" value={frm} onChange={(e) => setFrm(e.target.value)} aria-label={t("attFrom")} />
        <span className="att-filter-sep">–</span>
        <input type="month" value={to} onChange={(e) => setTo(e.target.value)} aria-label={t("attTo")} />
        <select value={department} onChange={(e) => setDepartment(e.target.value)} aria-label={t("attDepartment")}>
          <option value="">{t("attAllDepartments")}</option>
          {data.available_departments.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
      </div>
      <div className="att-kpis">
        <div className="att-kpi">
          <span className="att-kpi-value">{k.employees}</span>
          <span className="att-kpi-label">{t("attKpiEmployees")}</span>
        </div>
        <div className="att-kpi">
          <span className="att-kpi-value">{k.attendance_rate}%</span>
          <span className="att-kpi-label">{t("attKpiRate")}</span>
        </div>
        <div className="att-kpi">
          <span className="att-kpi-value">{k.review_count}</span>
          <span className="att-kpi-label">{t("attKpiReview")}</span>
        </div>
        <div className="att-kpi">
          <span className="att-kpi-value">{k.pending_cross_day}</span>
          <span className="att-kpi-label">{t("attKpiPending")}</span>
        </div>
      </div>
      <div className="boss-panel">
        <div className="subsection-heading">
          <h3>{t("attDailyRate")}</h3>
        </div>
        <DailyChart daily={data.daily} />
      </div>
      <div className="boss-panel">
        <div className="subsection-heading">
          <h3>{t("attDepartmentCompare")}</h3>
        </div>
        <DepartmentBars departments={data.departments} />
      </div>
      <div className="boss-panel">
        <div className="subsection-heading">
          <h3>{t("attSummary")}</h3>
        </div>
        <ul className="att-summary">
          <li>{t("attTotalRows")}: {data.summary.total_rows}</li>
          <li>{t("attMatchedRows")}: {data.summary.matched_rows}</li>
          <li>{t("attUnmatchedRows")}: {data.summary.unmatched_rows}</li>
          <li>{t("attSuspicionCount")}: {data.summary.suspicion_count}</li>
        </ul>
      </div>
    </div>
  );
}

// ---- 人员 ----

function EmployeesPanel({ canWrite, onToast }: { canWrite: boolean; onToast: (m: string) => void }) {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Employee | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      const r = await attApi<{ employees: Employee[] }>(`/api/attendance/employees${params.toString() ? `?${params}` : ""}`);
      setEmployees(r.employees);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [q, onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (employee: Employee) => {
    if (!window.confirm(t("attDeleteEmployeeConfirm", { name: employee.name }))) return;
    try {
      await attApi(`/api/attendance/employees/${employee.id}`, { method: "DELETE" });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    }
  };

  return (
    <div>
      <div className="att-filter-row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("attSearchPlaceholder")} />
        {canWrite && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            {t("attAddEmployee")}
          </Button>
        )}
      </div>
      <table className="att-table">
        <thead>
          <tr>
            <th>{t("attColNo")}</th>
            <th>{t("attColName")}</th>
            <th>{t("attColDepartment")}</th>
            <th>{t("attColPosition")}</th>
            <th>{t("attColStatus")}</th>
            {canWrite && <th />}
          </tr>
        </thead>
        <tbody>
          {employees.map((e) => (
            <tr key={e.id}>
              <td>{e.employee_no}</td>
              <td>{e.name}</td>
              <td>{e.department || "-"}</td>
              <td>{e.position || "-"}</td>
              <td>{EMPLOYMENT_LABELS[e.employment_status] || e.employment_status}</td>
              {canWrite && (
                <td className="att-row-actions">
                  <Button variant="secondary" onClick={() => setEditing(e)}>{t("edit")}</Button>
                  <Button variant="danger" onClick={() => void remove(e)}>{t("delete")}</Button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {(creating || editing) && (
        <EmployeeDialog
          employee={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={() => {
            setCreating(false);
            setEditing(null);
            void load();
          }}
          onToast={onToast}
        />
      )}
    </div>
  );
}

function EmployeeDialog({
  employee,
  onClose,
  onSaved,
  onToast,
}: {
  employee: Employee | null;
  onClose: () => void;
  onSaved: () => void;
  onToast: (m: string) => void;
}) {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [tags, setTags] = useState<{ id: number; name: string }[]>([]);
  const [form, setForm] = useState({
    employee_no: employee?.employee_no ?? "",
    name: employee?.name ?? "",
    department: employee?.department ?? "",
    position: employee?.position ?? "",
    employment_status: employee?.employment_status ?? "regular",
    active: employee?.active ?? true,
    attendance_policy_id: employee?.attendance_policy_id ?? null as number | null,
    aliases: employee?.aliases?.join("、") ?? "",
    phone: employee?.phone ?? "",
    tag_ids: employee?.tags?.map((tg) => tg.id) ?? [] as number[],
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void Promise.all([
      attApi<{ policies: Policy[] }>("/api/attendance/policies"),
      attApi<{ tags: { id: number; name: string }[] }>("/api/attendance/tags"),
    ]).then(([p, tg]) => {
      setPolicies(p.policies);
      setTags(tg.tags);
    }).catch((e) => onToast((e as Error).message));
  }, [onToast]);

  const set = (key: string, value: unknown) => setForm((f) => ({ ...f, [key]: value }));

  const save = async () => {
    setBusy(true);
    try {
      const body = {
        employee_no: form.employee_no,
        name: form.name,
        department: form.department,
        position: form.position,
        employment_status: form.employment_status,
        active: form.active,
        attendance_policy_id: form.attendance_policy_id,
        aliases: form.aliases.split(/[、,，]/).map((s) => s.trim()).filter(Boolean),
        phone: form.phone,
        tag_ids: form.tag_ids,
      };
      if (employee) {
        await attApi(`/api/attendance/employees/${employee.id}`, { method: "PUT", body: JSON.stringify(body) });
      } else {
        await attApi("/api/attendance/employees", { method: "POST", body: JSON.stringify(body) });
      }
      onSaved();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="att-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="att-modal att-modal-wide" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>{employee ? t("attEditEmployee") : t("attAddEmployee")}</h3>
        <div className="att-form-grid">
          <label className="field"><span>{t("attColNo")}</span><input value={form.employee_no} onChange={(e) => set("employee_no", e.target.value)} /></label>
          <label className="field"><span>{t("attColName")}</span><input value={form.name} onChange={(e) => set("name", e.target.value)} /></label>
          <label className="field"><span>{t("attColDepartment")}</span><input value={form.department} onChange={(e) => set("department", e.target.value)} /></label>
          <label className="field"><span>{t("attColPosition")}</span><input value={form.position} onChange={(e) => set("position", e.target.value)} /></label>
          <label className="field"><span>{t("attColStatus")}</span>
            <select value={form.employment_status} onChange={(e) => set("employment_status", e.target.value)}>
              {Object.entries(EMPLOYMENT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="field"><span>{t("attPolicy")}</span>
            <select value={form.attendance_policy_id ?? ""} onChange={(e) => set("attendance_policy_id", e.target.value ? Number(e.target.value) : null)}>
              <option value="">{t("attNoPolicy")}</option>
              {policies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="field"><span>{t("attAliases")}</span><input value={form.aliases} onChange={(e) => set("aliases", e.target.value)} placeholder={t("attAliasesPlaceholder")} /></label>
          <label className="field"><span>{t("attPhone")}</span><input value={form.phone} onChange={(e) => set("phone", e.target.value)} /></label>
          <label className="field"><span>{t("attTags")}</span>
            <select multiple value={form.tag_ids.map(String)} onChange={(e) => set("tag_ids", Array.from(e.target.selectedOptions, (o) => Number(o.value)))}>
              {tags.map((tg) => <option key={tg.id} value={tg.id}>{tg.name}</option>)}
            </select>
          </label>
          <label className="field att-field-inline"><span>{t("attActive")}</span>
            <input type="checkbox" checked={form.active} onChange={(e) => set("active", e.target.checked)} />
          </label>
        </div>
        <div className="att-modal-actions">
          <Button variant="secondary" onClick={onClose}>{t("cancel")}</Button>
          <Button variant="primary" busy={busy} disabled={!form.employee_no || !form.name} onClick={() => void save()}>{t("save")}</Button>
        </div>
      </div>
    </div>
  );
}

// ---- 导入 ----

function ImportsPanel({ canWrite, onToast }: { canWrite: boolean; onToast: (m: string) => void }) {
  const [imports, setImports] = useState<ImportBatch[]>([]);
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [month, setMonth] = useState(String(new Date().getMonth() + 1));
  const [expectedDays, setExpectedDays] = useState("25");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await attApi<{ imports: ImportBatch[] }>("/api/attendance/imports");
      setImports(r.imports);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const url = `/api/attendance/imports?year=${year}&month=${month}&default_expected_days=${expectedDays}`;
      await attApi(url, { method: "POST", body: formData });
      setFile(null);
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const download = (batch: ImportBatch) => {
    const token = localStorage.getItem(ATT_TOKEN_KEY) || "";
    const headers = new Headers({ "X-App-Token": (document.querySelector('meta[name="app-token"]') as HTMLMetaElement)?.content ?? "" });
    headers.set("X-Attendance-Token", token);
    void fetch(`/api/attendance/imports/${batch.id}/export`, { headers }).then((res) => {
      if (!res.ok) return;
      return res.blob();
    }).then((blob) => {
      if (!blob) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${batch.year}.${batch.month}月考勤汇总.xlsx`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  };

  return (
    <div>
      {canWrite && (
        <form className="boss-panel att-import-form" onSubmit={(e) => void upload(e)}>
          <div className="subsection-heading"><h3>{t("attImportTitle")}</h3></div>
          <div className="att-form-grid">
            <label className="field"><span>{t("attYear")}</span><input type="number" value={year} onChange={(e) => setYear(e.target.value)} /></label>
            <label className="field"><span>{t("attMonth")}</span><input type="number" min={1} max={12} value={month} onChange={(e) => setMonth(e.target.value)} /></label>
            <label className="field"><span>{t("attExpectedDays")}</span><input type="number" step="0.5" value={expectedDays} onChange={(e) => setExpectedDays(e.target.value)} /></label>
            <label className="field"><span>{t("attFile")}</span><input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          </div>
          <Button variant="primary" type="submit" busy={busy} disabled={!file}>{t("attUpload")}</Button>
        </form>
      )}
      <table className="att-table">
        <thead>
          <tr>
            <th>{t("attColPeriod")}</th>
            <th>{t("attColFile")}</th>
            <th>{t("attColStatus")}</th>
            <th>{t("attColMatched")}</th>
            <th>{t("attColSuspicion")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {imports.map((b) => (
            <tr key={b.id}>
              <td>{b.year}-{String(b.month).padStart(2, "0")}</td>
              <td>{b.original_filename}</td>
              <td>{b.status === "completed" ? t("attStatusCompleted") : b.status === "failed" ? t("attStatusFailed") : t("attStatusProcessing")}</td>
              <td>{b.matched_rows}/{b.total_rows}</td>
              <td>{b.suspicion_count}</td>
              <td>
                {b.status === "completed" && <Button variant="secondary" onClick={() => download(b)}>{t("attExport")}</Button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- 结果 ----

function ResultsPanel({ canWrite, onToast }: { canWrite: boolean; onToast: (m: string) => void }) {
  const [results, setResults] = useState<ResultRow[]>([]);
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [batchId, setBatchId] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState<ResultRow | null>(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (batchId) params.set("batch", batchId);
      if (status) params.set("status", status);
      const r = await attApi<{ results: ResultRow[] }>(`/api/attendance/results${params.toString() ? `?${params}` : ""}`);
      setResults(r.results);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [batchId, status, onToast]);

  useEffect(() => {
    void attApi<{ imports: ImportBatch[] }>("/api/attendance/imports").then((r) => setBatches(r.imports)).catch(() => {});
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const approve = async (row: ResultRow) => {
    try {
      await attApi(`/api/attendance/results/${row.id}/approve`, { method: "POST" });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    }
  };

  return (
    <div>
      <div className="att-filter-row">
        <select value={batchId} onChange={(e) => setBatchId(e.target.value)} aria-label={t("attBatch")}>
          <option value="">{t("attAllBatches")}</option>
          {batches.map((b) => <option key={b.id} value={b.id}>{b.year}-{String(b.month).padStart(2, "0")}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("attColStatus")}>
          <option value="">{t("attAllStatuses")}</option>
          <option value="review">{t("attStatusReview")}</option>
          <option value="normal">{t("attStatusNormal")}</option>
          <option value="approved">{t("attStatusApproved")}</option>
        </select>
      </div>
      <table className="att-table">
        <thead>
          <tr>
            <th>{t("attColNo")}</th>
            <th>{t("attColName")}</th>
            <th>{t("attColDepartment")}</th>
            <th>{t("attPunchDays")}</th>
            <th>{t("attDueDays")}</th>
            <th>{t("attActualDays")}</th>
            <th>{t("attColStatus")}</th>
            {canWrite && <th />}
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.id}>
              <td>{r.emp_no}</td>
              <td>{r.emp_name}</td>
              <td>{r.emp_department || "-"}</td>
              <td>{r.punch_days}</td>
              <td>{r.due_days}</td>
              <td>{r.actual_days}</td>
              <td>{r.status === "approved" ? t("attStatusApproved") : r.status === "normal" ? t("attStatusNormal") : t("attStatusReview")}</td>
              {canWrite && (
                <td className="att-row-actions">
                  <Button variant="secondary" onClick={() => setEditing(r)}>{t("attAdjust")}</Button>
                  {r.status !== "approved" && <Button variant="primary" onClick={() => void approve(r)}>{t("attApprove")}</Button>}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <ResultDialog result={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void load(); }} onToast={onToast} />
      )}
    </div>
  );
}

function ResultDialog({ result, onClose, onSaved, onToast }: { result: ResultRow; onClose: () => void; onSaved: () => void; onToast: (m: string) => void }) {
  const [adjustmentDays, setAdjustmentDays] = useState(String(result.adjustment_days ?? 0));
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await attApi(`/api/attendance/results/${result.id}`, {
        method: "PATCH",
        body: JSON.stringify({ adjustment_days: Number(adjustmentDays || 0) }),
      });
      onSaved();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="att-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="att-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>{t("attAdjust")} · {result.emp_name}</h3>
        <p className="att-modal-hint">{t("attAdjustHint", { actual: result.actual_days, due: result.due_days, punch: result.punch_days })}</p>
        <label className="field">
          <span>{t("attAdjustmentDays")}</span>
          <input type="number" step="0.5" value={adjustmentDays} onChange={(e) => setAdjustmentDays(e.target.value)} />
        </label>
        <div className="att-modal-actions">
          <Button variant="secondary" onClick={onClose}>{t("cancel")}</Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>{t("save")}</Button>
        </div>
      </div>
    </div>
  );
}

// ---- 跨日审核 ----

function SuspicionsPanel({ canWrite, onToast }: { canWrite: boolean; onToast: (m: string) => void }) {
  const [items, setItems] = useState<Suspicion[]>([]);
  const [status, setStatus] = useState("pending");
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await attApi<{ suspicions: Suspicion[] }>(`/api/attendance/suspicions${status ? `?status=${status}` : ""}`);
      setItems(r.suspicions);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [status, onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const resolve = async (item: Suspicion, resolution: string) => {
    setBusyId(item.id);
    try {
      await attApi(`/api/attendance/suspicions/${item.id}/resolve`, { method: "POST", body: JSON.stringify({ resolution }) });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <div className="att-filter-row">
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("attColStatus")}>
          <option value="pending">{t("attStatusReview")}</option>
          <option value="assign_previous">{t("attAssignPrevious")}</option>
          <option value="keep_current">{t("attKeepCurrent")}</option>
          <option value="">{t("attAllStatuses")}</option>
        </select>
      </div>
      <ul className="att-suspicion-list">
        {items.map((item) => (
          <li key={item.id} className="att-suspicion-item">
            <div className="att-suspicion-main">
              <strong>{item.emp_name}（{item.emp_no}）</strong>
              <span className="att-suspicion-meta">{item.work_date} {item.punch_text}</span>
              <span className="att-suspicion-reason">{item.reason}</span>
            </div>
            <div className="att-suspicion-actions">
              {item.status === "pending" && canWrite ? (
                <>
                  <Button variant="primary" busy={busyId === item.id} onClick={() => void resolve(item, "assign_previous")}>{t("attAssignPrevious")}</Button>
                  <Button variant="secondary" disabled={busyId === item.id} onClick={() => void resolve(item, "keep_current")}>{t("attKeepCurrent")}</Button>
                </>
              ) : (
                <span className="att-suspicion-status">{item.status === "assign_previous" ? t("attAssignPrevious") : t("attKeepCurrent")}</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---- 飞书自动同步配置 ----

interface FeishuConfig {
  enabled: boolean;
  app_id: string;
  app_secret_configured: boolean;
  app_secret_tail: string;
  sync_running: boolean;
  last_error: string;
}

function FeishuSyncPanel({ canWrite, onToast }: { canWrite: boolean; onToast: (m: string) => void }) {
  const [config, setConfig] = useState<FeishuConfig | null>(null);
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const cfg = await attApi<FeishuConfig>("/api/attendance/feishu-config");
      setConfig(cfg);
      setAppId(cfg.app_id || "");
      setEnabled(Boolean(cfg.enabled));
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setBusy(true);
    try {
      await attApi("/api/attendance/feishu-config", {
        method: "PUT",
        body: JSON.stringify({ app_id: appId, app_secret: appSecret, enabled }),
      });
      setAppSecret("");
      onToast(t("save") + " ✓");
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const sync = async () => {
    setBusy(true);
    try {
      const r = await attApi<{ ok: boolean; detail?: string; suspicion_count?: number }>("/api/attendance/feishu/sync", { method: "POST" });
      if (r.ok) onToast(t("attFeishuSyncDone", { count: r.suspicion_count ?? 0 }));
      else onToast(r.detail || t("attFeishuSyncFailed"));
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="boss-panel">
      <div className="subsection-heading">
        <h3>{t("attFeishuSync")}</h3>
        <span>{config?.sync_running ? t("attFeishuRunning") : ""}</span>
      </div>
      <p className="boss-lead">{t("attFeishuSyncLead")}</p>
      {config?.last_error && <p className="att-feishu-error">{t("attFeishuLastError")}: {config.last_error}</p>}
      {canWrite ? (
        <>
          <div className="att-form-grid">
            <label className="field">
              <span>{t("attFeishuAppId")}</span>
              <input value={appId} onChange={(e) => setAppId(e.target.value)} placeholder="cli_..." />
            </label>
            <label className="field">
              <span>{t("attFeishuAppSecret")}</span>
              <input
                type="password"
                value={appSecret}
                onChange={(e) => setAppSecret(e.target.value)}
                placeholder={config?.app_secret_configured ? t("attFeishuSecretConfigured", { tail: config.app_secret_tail }) : t("attFeishuSecretEmpty")}
              />
            </label>
          </div>
          <div className="att-filter-row">
            <label className="att-field-inline">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              <span>{t("attFeishuEnabled")}</span>
            </label>
            <Button variant="primary" busy={busy} onClick={() => void save()}>{t("save")}</Button>
            <Button variant="secondary" disabled={busy || !config?.enabled} onClick={() => void sync()}>{t("attFeishuSyncNow")}</Button>
          </div>
        </>
      ) : (
        <p className="att-empty">{t("attFeishuReadonly")}</p>
      )}
    </div>
  );
}

// ---- 设置（策略 + 标签）----

function SettingsPanel({ canWrite, onToast }: { canWrite: boolean; onToast: (m: string) => void }) {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [tags, setTags] = useState<{ id: number; name: string; color: string }[]>([]);

  const load = useCallback(async () => {
    try {
      const [p, tg] = await Promise.all([
        attApi<{ policies: Policy[] }>("/api/attendance/policies"),
        attApi<{ tags: { id: number; name: string; color: string }[] }>("/api/attendance/tags"),
      ]);
      setPolicies(p.policies);
      setTags(tg.tags);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div className="boss-panel">
        <div className="subsection-heading"><h3>{t("attPolicies")}</h3></div>
        <table className="att-table">
          <thead>
            <tr><th>{t("attColName")}</th><th>{t("attPolicyMode")}</th><th>{t("attCrossDayCutoff")}</th><th>{t("attActive")}</th></tr>
          </thead>
          <tbody>
            {policies.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td>{MODE_LABELS[p.mode] || p.mode}</td>
                <td>{p.cross_day_cutoff_minutes} 分钟</td>
                <td>{p.active ? t("attYes") : t("attNo")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {policies.length === 0 && <p className="att-empty">{t("attNoData")}</p>}
      </div>
      <div className="boss-panel">
        <div className="subsection-heading"><h3>{t("attTags")}</h3></div>
        <div className="att-tag-list">
          {tags.map((tg) => (
            <span key={tg.id} className="att-tag" style={{ background: tg.color }}>
              {tg.name}
            </span>
          ))}
          {tags.length === 0 && <p className="att-empty">{t("attNoData")}</p>}
        </div>
      </div>
      <FeishuSyncPanel canWrite={canWrite} onToast={onToast} />
    </div>
  );
}
