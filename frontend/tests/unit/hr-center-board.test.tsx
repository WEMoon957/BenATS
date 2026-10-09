// =====================================================================
// 人事中台看板改造：错误态/重试、常驻表单链接与复制降级、不可逆操作确认、
// 首屏待办聚合与列表筛选。既有 hr-center-view.test.tsx 的断言保持不变。
// =====================================================================

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HRCenterView } from "../../src/views/HRCenterView";
import { state } from "../../src/state";

const DASHBOARD_PATH = "/api/hr/dashboard";
const RESIGN_LIST_PATH = "/api/hr/resignations";
const ROSTER_PATH = "/api/attendance/employees?active=true";
const OFFBOARD_PATH = "/api/hr/lifecycle/run-offboards";

let fetchMock: ReturnType<typeof vi.fn>;
let dashboard: Record<string, unknown>;
let requests: unknown[];
let roster: unknown[];
let dashboardStatus = 200;
let resignStatus = 200;
let rosterStatus = 200;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function baseDashboard(): Record<string, unknown> {
  return {
    employees: { total: 3, active: 2, departments: [{ department: "技术部", count: 2 }] },
    attendance: {
      latest_period: "2026-10",
      attendance_rate: 98,
      review_count: 1,
      pending_cross_day: 0,
      feishu: { enabled: true, running: true, last_error: "", last_batch: null },
    },
    recruitment: { candidates: 5, jobs: 1, stages: [] },
    feishu: { credentials_configured: true, employee_sync: null },
    lifecycle: {
      active: 2,
      total: 3,
      current: { onboard: 1, offboard: 2, turnover_rate: 50, headcount: 2 },
      months: [
        { month: "2026-09", onboard: 1, offboard: 0, headcount: 3, turnover_rate: 0 },
        { month: "2026-10", onboard: 1, offboard: 2, headcount: 2, turnover_rate: 50 },
      ],
      reasons: [{ category: "个人发展", count: 2 }],
      departments: [{ department: "技术部", active: 2, onboard: 1, offboard: 2 }],
      pending: { sent: 1, submitted: 1, confirmed: 2, completed: 0 },
      recent_events: [],
      ops: {
        public_base_url: "",
        reason_categories: ["个人发展"],
        events: {
          configured: false,
          enabled: false,
          running: false,
          connected: false,
          last_error: "",
          last_event: null,
          stats: {},
        },
        offboard: { running: false, last_error: "", last_result: null },
      },
    },
  };
}

beforeEach(() => {
  state.language = "zh-CN";
  dashboard = baseDashboard();
  requests = [];
  roster = [{ id: 1, name: "张三", employee_no: "E001", department: "技术部" }];
  dashboardStatus = 200;
  resignStatus = 200;
  rosterStatus = 200;

  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (path === DASHBOARD_PATH) return jsonResponse(dashboard, dashboardStatus);
    if (path === RESIGN_LIST_PATH && method === "POST") {
      return jsonResponse({ request: { id: 1, status: "sent" }, delivery: { delivered: true } });
    }
    if (path === RESIGN_LIST_PATH) return jsonResponse({ requests }, resignStatus);
    if (path === ROSTER_PATH) return jsonResponse({ employees: roster }, rosterStatus);
    if (path === OFFBOARD_PATH) return jsonResponse({ processed: 2 });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  state.language = "zh-CN";
});

async function renderReady(onToast: (message: string) => void = vi.fn()) {
  render(<HRCenterView onToast={onToast} />);
  await waitFor(() => expect(document.querySelector(".hr-grid")).not.toBeNull());
}

/** 读取指定 KPI 卡片内的数值，避免依赖整页文本匹配 */
function kpiValue(label: string): string | null {
  const labelEl = [...document.querySelectorAll(".att-kpi-label")].find(
    (el) => el.textContent === label
  );
  return labelEl?.parentElement?.querySelector(".att-kpi-value")?.textContent ?? null;
}

function postCalls(path: string) {
  return fetchMock.mock.calls.filter(
    ([input, init]) =>
      String(input) === path && ((init as RequestInit | undefined)?.method ?? "GET").toUpperCase() === "POST"
  );
}

describe("人事中台：dashboard 错误态", () => {
  it("dashboard 500 时展示错误态与重试按钮，不显示加载中", async () => {
    dashboardStatus = 500;
    render(<HRCenterView onToast={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("重试")).toBeDefined());
    expect(screen.getByText(/看板加载失败/)).toBeDefined();
    expect(screen.queryByText("加载中…")).toBeNull();
    expect(document.querySelector(".hr-grid")).toBeNull();
  });
});

describe("人事中台：resignations 错误态", () => {
  it("申请列表 500 时展示错误态与重试，不显示「暂无离职申请」", async () => {
    resignStatus = 500;
    await renderReady();

    await waitFor(() => expect(screen.getByText(/离职申请加载失败/)).toBeDefined());
    expect(screen.queryByText("暂无离职申请")).toBeNull();
    expect(screen.getByText("重试")).toBeDefined();
  });
});

describe("人事中台：在职员工加载失败", () => {
  it("区分「加载失败」与「真的没有员工」", async () => {
    rosterStatus = 500;
    await renderReady();

    await waitFor(() => expect(screen.getByText(/在职员工加载失败/)).toBeDefined());
    expect(screen.queryByText("当前没有可发起离职的在职员工")).toBeNull();
  });
});

describe("人事中台：常驻表单链接", () => {
  beforeEach(() => {
    requests = [
      {
        id: 7,
        employee_name: "张三",
        department: "技术部",
        status: "sent",
        status_label: "待填写",
        last_working_day: "2026-10-20",
        reason_category: "个人发展",
        deliver_status: "manual",
        deliver_error: "",
        token: "tk1",
      },
    ];
  });

  it("行内展示可复制的表单链接，无 clipboard 时走降级且不抛异常", async () => {
    const onToast = vi.fn();
    await renderReady(onToast);

    const link = `${window.location.origin}/resign/tk1`;
    expect(screen.getByText(link)).toBeDefined();

    expect(() => fireEvent.click(screen.getByText("复制链接"))).not.toThrow();
    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith("浏览器未授予剪贴板权限，请手动复制链接")
    );
  });

  it("存在剪贴板 API 时写入链接并提示已复制", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const onToast = vi.fn();
    await renderReady(onToast);

    fireEvent.click(screen.getByText("复制链接"));

    await waitFor(() => expect(onToast).toHaveBeenCalledWith("已复制"));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/resign/tk1`);
  });
});

describe("人事中台：不可逆操作确认", () => {
  it("发起离职需确认，取消则不发请求", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderReady();

    fireEvent.change(document.querySelector(".hr-resign-start select")!, { target: { value: "1" } });
    fireEvent.click(screen.getByText("发起离职"));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(String(confirmSpy.mock.calls[0][0])).toContain("张三");
    expect(postCalls(RESIGN_LIST_PATH)).toHaveLength(0);

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByText("发起离职"));
    await waitFor(() => expect(postCalls(RESIGN_LIST_PATH)).toHaveLength(1));
  });

  it("立即执行到期离职需确认影响人数，取消则不发请求", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderReady();

    fireEvent.click(screen.getByText("立即执行到期离职"));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    // lifecycle.pending.confirmed = 2
    expect(String(confirmSpy.mock.calls[0][0])).toContain("2");
    expect(postCalls(OFFBOARD_PATH)).toHaveLength(0);

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByText("立即执行到期离职"));
    await waitFor(() => expect(postCalls(OFFBOARD_PATH)).toHaveLength(1));
  });

  it("没有可执行的到期离职时禁用执行按钮且不弹确认框", async () => {
    dashboard = {
      ...baseDashboard(),
      lifecycle: {
        ...(baseDashboard().lifecycle as Record<string, unknown>),
        pending: { sent: 1, submitted: 1, confirmed: 0, completed: 0 },
      },
    };
    const confirmSpy = vi.spyOn(window, "confirm");
    await renderReady();

    const btn = screen.getByText("立即执行到期离职").closest("button") as HTMLButtonElement;
    expect(btn).toBeDisabled();

    fireEvent.click(btn);
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(postCalls(OFFBOARD_PATH)).toHaveLength(0);
  });
});

const SYNC_PATH = "/api/hr/feishu-employees/sync";

describe("人事中台：员工同步失败持久提示", () => {
  it("同步失败后页内持久展示后端 detail，恢复成功后清除", async () => {
    await renderReady();

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (path === SYNC_PATH && method === "POST") {
        return jsonResponse({ detail: "飞书应用尚未开通通讯录权限" }, 502);
      }
      if (path === DASHBOARD_PATH) return jsonResponse(dashboard, dashboardStatus);
      if (path === RESIGN_LIST_PATH) return jsonResponse({ requests }, resignStatus);
      if (path === ROSTER_PATH) return jsonResponse({ employees: roster }, rosterStatus);
      if (path === OFFBOARD_PATH && method === "POST") return jsonResponse({ processed: 2 });
      return jsonResponse({});
    });

    fireEvent.click(screen.getByText("从飞书同步员工"));

    await waitFor(() => expect(document.querySelector(".hr-sync .hr-error")).not.toBeNull());
    expect(document.querySelector(".hr-sync .hr-error")?.textContent).toContain(
      "飞书应用尚未开通通讯录权限"
    );

    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (path === SYNC_PATH && method === "POST") {
        return jsonResponse({
          at: "2026-10-09T03:00:00+00:00",
          total: 3,
          inserted: 1,
          updated: 2,
          skipped: 0,
          fallback_user_id: 0,
        });
      }
      if (path === DASHBOARD_PATH) return jsonResponse(dashboard, dashboardStatus);
      if (path === RESIGN_LIST_PATH) return jsonResponse({ requests }, resignStatus);
      if (path === ROSTER_PATH) return jsonResponse({ employees: roster }, rosterStatus);
      if (path === OFFBOARD_PATH && method === "POST") return jsonResponse({ processed: 2 });
      return jsonResponse({});
    });

    fireEvent.click(screen.getByText("从飞书同步员工"));

    await waitFor(() => expect(document.querySelector(".hr-sync .hr-error")).toBeNull());
  });
});

describe("人事中台：首屏待办聚合与筛选", () => {
  it("同屏展示本月离职人数、待办总数与送达失败数", async () => {
    requests = [
      {
        id: 1,
        employee_name: "张三",
        department: "技术部",
        status: "sent",
        status_label: "待填写",
        last_working_day: null,
        reason_category: "个人发展",
        deliver_status: "failed",
        deliver_error: "网络超时",
        token: "tk1",
      },
    ];
    await renderReady();

    expect(kpiValue("本月离职人数")).toBe("2");
    expect(kpiValue("离职待办总数")).toBe("4");
    expect(kpiValue("送达失败数")).toBe("1");
  });

  it("待办 KPI 可点击写入筛选", async () => {
    await renderReady();

    const todoButton = document.querySelector(".hr-kpi-link") as HTMLButtonElement;
    fireEvent.click(todoButton);

    const select = document.querySelector(".hr-resign-filters select") as HTMLSelectElement;
    expect(select.value).toBe("todo");
  });
});
