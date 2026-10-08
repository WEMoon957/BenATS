// =====================================================================
// 人事中台：飞书员工同步按钮的请求契约、结果提示与状态回显。
// =====================================================================

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HRCenterView } from "../../src/views/HRCenterView";
import { state } from "../../src/state";

const SYNC_PATH = "/api/hr/feishu-employees/sync";

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
    attendance: { latest_period: null, attendance_rate: 0, review_count: 0, pending_cross_day: 0 },
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
