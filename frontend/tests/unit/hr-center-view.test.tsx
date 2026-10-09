// =====================================================================
// 人事中台：飞书员工同步按钮的请求契约、结果提示与状态回显。
// =====================================================================

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HRCenterView } from "../../src/views/HRCenterView";
import { state } from "../../src/state";

const SYNC_PATH = "/api/hr/feishu-employees/sync";
const ATTENDANCE_SYNC_PATH = "/api/hr/feishu-sync";

let fetchMock: ReturnType<typeof vi.fn>;
let dashboard: Record<string, unknown>;

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
      latest_period: null,
      attendance_rate: 0,
      review_count: 0,
      pending_cross_day: 0,
      feishu: { enabled: true, running: true, last_error: "", last_batch: null },
    },
    recruitment: { candidates: 5, jobs: 1, stages: [] },
    feishu: { credentials_configured: true, employee_sync: null },
  };
}

beforeEach(() => {
  state.language = "zh-CN";
  dashboard = baseDashboard();
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === SYNC_PATH) {
      return jsonResponse({ at: "2026-10-08T03:00:00+00:00", total: 3, inserted: 1, updated: 2, skipped: 0 });
    }
    return jsonResponse(dashboard);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  state.language = "zh-CN";
});

async function renderView(onToast: (message: string) => void) {
  render(<HRCenterView onToast={onToast} />);
  await waitFor(() => expect(document.querySelector(".hr-grid")).not.toBeNull());
}

describe("人事中台飞书员工同步", () => {
  it("未同步过时显示按钮与未同步提示", async () => {
    await renderView(vi.fn());

    expect(screen.getByText("从飞书同步员工")).toBeDefined();
    expect(document.querySelector(".hr-sync .hr-period")?.textContent).toContain("尚未从飞书同步");
  });

  it("没有工号改用飞书用户 ID 建档时，同步行把它显示出来", async () => {
    dashboard = {
      ...baseDashboard(),
      feishu: {
        credentials_configured: true,
        employee_sync: {
          at: "2026-10-08T03:00:00+00:00",
          total: 25,
          inserted: 25,
          updated: 0,
          skipped: 0,
          fallback_user_id: 25,
        },
      },
    };
    await renderView(vi.fn());

    expect(document.querySelector(".hr-sync .hr-period")?.textContent).toContain(
      "其中 25 人无工号，用飞书用户 ID 建档"
    );
  });

  it("点击按钮发起 POST、刷新看板并提示同步结果", async () => {
    const onToast = vi.fn();
    await renderView(onToast);

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === SYNC_PATH) {
        const summary = {
          at: "2026-10-08T03:00:00+00:00",
          total: 3,
          inserted: 1,
          updated: 2,
          skipped: 0,
          fallback_user_id: 0,
        };
        dashboard = {
          ...baseDashboard(),
          feishu: { credentials_configured: true, employee_sync: summary },
        };
        return jsonResponse(summary);
      }
      return jsonResponse(dashboard);
    });

    fireEvent.click(screen.getByText("从飞书同步员工"));

    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith("同步完成：新增 1 · 更新 2 · 跳过 0")
    );
    expect(fetchMock).toHaveBeenCalledWith(SYNC_PATH, expect.objectContaining({ method: "POST" }));
    await waitFor(() =>
      expect(document.querySelector(".hr-sync .hr-period")?.textContent).toContain(
        "2026-10-08T03:00:00+00:00 · 新增 1 · 更新 2 · 跳过 0"
      )
    );
  });

  it("同步失败时透传后端 detail", async () => {
    const onToast = vi.fn();
    await renderView(onToast);

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === SYNC_PATH) {
        return jsonResponse({ detail: "飞书应用尚未开通通讯录权限，请开通后重试" }, 502);
      }
      return jsonResponse(dashboard);
    });

    fireEvent.click(screen.getByText("从飞书同步员工"));

    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith("飞书应用尚未开通通讯录权限，请开通后重试")
    );
  });
});

describe("人事中台飞书考勤同步", () => {
  it("显示同步按钮与已开启但未同步的状态", async () => {
    await renderView(vi.fn());

    expect(screen.getByText("立即从飞书同步")).toBeDefined();
    expect(screen.getByText("已开启 · 尚未同步")).toBeDefined();
  });

  it("点击后触发一轮同步并提示匹配人数", async () => {
    const onToast = vi.fn();
    await renderView(onToast);

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === ATTENDANCE_SYNC_PATH) {
        return jsonResponse({
          ok: true,
          batch_id: 1,
          period: "2026-10",
          employees: 2,
          matched_employees: 2,
          suspicion_count: 0,
        });
      }
      return jsonResponse(dashboard);
    });

    fireEvent.click(screen.getByText("立即从飞书同步"));

    await waitFor(() => expect(onToast).toHaveBeenCalledWith("同步完成：2026-10 匹配 2 人"));
    expect(fetchMock).toHaveBeenCalledWith(
      ATTENDANCE_SYNC_PATH,
      expect.objectContaining({ method: "POST" })
    );
  });

  it("同步未生效时提示后端 detail", async () => {
    const onToast = vi.fn();
    await renderView(onToast);

    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === ATTENDANCE_SYNC_PATH) {
        return jsonResponse({ ok: false, detail: "没有可同步的在职员工" });
      }
      return jsonResponse(dashboard);
    });

    fireEvent.click(screen.getByText("立即从飞书同步"));

    await waitFor(() => expect(onToast).toHaveBeenCalledWith("没有可同步的在职员工"));
  });

  it("同步失败原因来自服务端时展示错误文本", async () => {
    dashboard = {
      ...baseDashboard(),
      attendance: {
        ...(baseDashboard().attendance as Record<string, unknown>),
        feishu: { enabled: true, running: true, last_error: "打卡查询失败：超时", last_batch: null },
      },
    };
    await renderView(vi.fn());

    expect(screen.getByText("打卡查询失败：超时")).toBeDefined();
  });
});

const RESIGN_LIST_PATH = "/api/hr/resignations";
const ROSTER_PATH = "/api/attendance/employees?active=true";

function lifecycleDashboard(): Record<string, unknown> {
  return {
    ...baseDashboard(),
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
      pending: { sent: 1, submitted: 1, confirmed: 0, completed: 0 },
      recent_events: [
        {
          id: 1,
          employee_name: "张三",
          department: "技术部",
          event_type: "offboard",
          event_label: "离职",
          effective_date: "2026-10-20",
          source: "resignation",
          reason: "个人发展",
        },
      ],
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

function mockLifecycleFetch(overrides: Record<string, () => Response> = {}) {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (overrides[path]) return overrides[path]();
    if (path === RESIGN_LIST_PATH) {
      return jsonResponse({
        requests: [
          {
            id: 7,
            employee_name: "张三",
            department: "技术部",
            status: "submitted",
            status_label: "待 HR 确认",
            last_working_day: "2026-10-20",
            reason_category: "个人发展",
            deliver_status: "manual",
            deliver_error: "",
          },
        ],
      });
    }
    if (path === ROSTER_PATH) {
      return jsonResponse({ employees: [{ id: 1, name: "张三", employee_no: "E001", department: "技术部" }] });
    }
    return jsonResponse(dashboard);
  });
}

describe("人事中台人员流动看板", () => {
  it("展示本月入职离职、离职率与部门流动", async () => {
    dashboard = lifecycleDashboard();
    mockLifecycleFetch();

    await renderView(vi.fn());

    expect(screen.getByText("人员流动")).toBeDefined();
    expect(screen.getByText("本月入职")).toBeDefined();
    expect(screen.getByText("50%")).toBeDefined();
    expect(screen.getByText("部门人员流动")).toBeDefined();
  });

  it("确认离职申请后提示将在最后工作日自动离职", async () => {
    const onToast = vi.fn();
    dashboard = lifecycleDashboard();
    mockLifecycleFetch({
      "/api/hr/resignations/7/confirm": () =>
        jsonResponse({ request: { id: 7, status: "confirmed" } }),
    });

    await renderView(onToast);
    fireEvent.click(screen.getByText("确认离职"));

    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith("已确认，将在最后工作日自动离职")
    );
  });

  it("发起离职后把未能自动送达的链接提示给 HR", async () => {
    const onToast = vi.fn();
    dashboard = lifecycleDashboard();
    mockLifecycleFetch({
      "/api/hr/resignations": () =>
        jsonResponse({
          request: { id: 9, status: "sent" },
          delivery: { delivered: false, manual: true, link: "http://x/resign/tk", detail: "未配置对外访问地址" },
        }),
    });

    await renderView(onToast);
    fireEvent.change(document.querySelector(".hr-resign-start select")!, { target: { value: "1" } });
    fireEvent.click(screen.getByText("发起离职"));

    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith(
        "离职流程已发起，但表单未送达。请手工转发链接：http://x/resign/tk"
      )
    );
  });

  it("未自动送达的申请在列表里带标注，便于 HR 手工转发", async () => {
    dashboard = lifecycleDashboard();
    mockLifecycleFetch();

    await renderView(vi.fn());

    expect(screen.getByText("未自动送达")).toBeDefined();
  });

  it("长连接已连接时显示已连接状态", async () => {
    dashboard = lifecycleDashboard();
    const board = dashboard.lifecycle as Record<string, unknown>;
    (board.ops as Record<string, unknown>).events = {
      configured: true,
      enabled: true,
      running: true,
      connected: true,
      last_error: "",
      last_event: { at: "2026-10-09T03:00:00+00:00", action: "offboard", name: "李四" },
      stats: {},
    };
    mockLifecycleFetch();

    await renderView(vi.fn());

    expect(screen.getByText("已连接")).toBeDefined();
    expect(screen.getByText("李四")).toBeDefined();
  });

  it("长连接失败时把后端错误原文展示出来", async () => {
    dashboard = lifecycleDashboard();
    const board = dashboard.lifecycle as Record<string, unknown>;
    (board.ops as Record<string, unknown>).events = {
      configured: true,
      enabled: true,
      running: true,
      connected: false,
      last_error: "事件长连接未建立：请在飞书开放平台把「事件订阅」方式设为「长连接」",
      last_event: null,
      stats: {},
    };
    mockLifecycleFetch();

    await renderView(vi.fn());

    expect(screen.getByText("连接中…")).toBeDefined();
    expect(
      screen.getByText("事件长连接未建立：请在飞书开放平台把「事件订阅」方式设为「长连接」")
    ).toBeDefined();
  });
});
