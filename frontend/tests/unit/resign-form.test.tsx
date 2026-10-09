// =====================================================================
// 离职填报表单（员工公开页）：加载、提交与路径解析。
// =====================================================================

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResignFormView, resignTokenFromPath } from "../../src/views/ResignFormView";
import { state } from "../../src/state";

const TOKEN = "tok-123";
const FORM_PATH = `/api/resignation/${TOKEN}`;

let fetchMock: ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function formPayload(overrides: Record<string, unknown> = {}) {
  return {
    request: {
      employee_name: "张三",
      employee_no: "E001",
      department: "技术部",
      position: "后端工程师",
      status: "sent",
      last_working_day: null,
      reason_category: "",
      reason_detail: "",
      handover_to: "",
      handover_note: "",
      contact_after: "",
      ...overrides,
    },
    reason_categories: ["个人发展", "薪酬福利"],
  };
}

beforeEach(() => {
  state.language = "zh-CN";
  fetchMock = vi.fn(async () => jsonResponse(formPayload()));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  state.language = "zh-CN";
});

describe("离职填报表单", () => {
  it("解析 /resign/<token> 路径", () => {
    expect(resignTokenFromPath("/resign/abc")).toBe("abc");
    expect(resignTokenFromPath("/resign/abc/")).toBe("abc");
    expect(resignTokenFromPath("/")).toBe("");
    expect(resignTokenFromPath("/hrcenter")).toBe("");
  });

  it("加载员工信息与离职原因选项", async () => {
    render(<ResignFormView token={TOKEN} />);

    await waitFor(() => expect(screen.getByText("张三")).toBeDefined());
    expect(screen.getByText("E001")).toBeDefined();
    expect(fetchMock).toHaveBeenCalledWith(
      FORM_PATH,
      expect.objectContaining({ headers: expect.any(Headers) })
    );
    expect(screen.getByRole("option", { name: "个人发展" })).toBeDefined();
  });

  it("提交时把表单字段发给后端并显示成功态", async () => {
    render(<ResignFormView token={TOKEN} />);
    await waitFor(() => expect(screen.getByText("张三")).toBeDefined());

    fireEvent.change(document.querySelector('input[type="date"]')!, {
      target: { value: "2026-10-20" },
    });
    fetchMock.mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return jsonResponse({ ok: true, status: "submitted" });
      return jsonResponse(formPayload());
    });

    fireEvent.click(screen.getByText("提交"));

    await waitFor(() => expect(screen.getByText("提交成功")).toBeDefined());
    const postCall = fetchMock.mock.calls.find((call) => (call[1] as RequestInit)?.method === "POST");
    expect(postCall).toBeTruthy();
    expect(JSON.parse(String((postCall![1] as RequestInit).body))).toMatchObject({
      last_working_day: "2026-10-20",
    });
  });

  it("已提交的表单显示不可重复填写", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(formPayload({ status: "submitted" })));
    render(<ResignFormView token={TOKEN} />);

    await waitFor(() => expect(screen.getByText("该表单已提交，无需重复填写。")).toBeDefined());
  });

  it("链接无效时展示错误", async () => {
    fetchMock.mockImplementation(async () => jsonResponse({ detail: "表单链接无效或已失效" }, 404));
    render(<ResignFormView token={TOKEN} />);

    await waitFor(() => expect(screen.getByText("表单链接无效或已失效")).toBeDefined());
  });
});
