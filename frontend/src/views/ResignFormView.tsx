// 离职填报表单（员工侧公开页）：按 /resign/<token> 路径渲染，无需登录。
// 提交后进入 HR 待确认队列；表单一旦提交就不再接受重复填写。

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { t } from "../i18n";
import { Button } from "../ui/Button";

interface ResignFormData {
  employee_name: string;
  employee_no: string;
  department: string;
  position: string;
  status: string;
  last_working_day: string | null;
  reason_category: string;
  reason_detail: string;
  handover_to: string;
  handover_note: string;
  contact_after: string;
}

/** 从 /resign/<token> 路径解析表单令牌；非该路径返回空串。 */
export function resignTokenFromPath(pathname: string): string {
  const match = /^\/resign\/([^/?#]+)/.exec(pathname);
  return match ? decodeURIComponent(match[1]) : "";
}

export function ResignFormView({ token }: { token: string }) {
  const [data, setData] = useState<ResignFormData | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [done, setDone] = useState(false);
  const [form, setForm] = useState({
    last_working_day: "",
    reason_category: "",
    reason_detail: "",
    handover_to: "",
    handover_note: "",
    contact_after: "",
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await api<{ request: ResignFormData; reason_categories: string[] }>(
        `/api/resignation/${encodeURIComponent(token)}`
      );
      setData(payload.request);
      setCategories(payload.reason_categories || []);
      setForm((prev) => ({ ...prev, reason_category: payload.request.reason_category || "" }));
    } catch (error) {
      setLoadError((error as Error).message || t("resignInvalid"));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = (key: keyof typeof form, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form.last_working_day) {
      setSubmitError(t("resignLastDay"));
      return;
    }
    setBusy(true);
    setSubmitError("");
    try {
      await api(`/api/resignation/${encodeURIComponent(token)}`, {
        method: "POST",
        body: JSON.stringify(form),
      });
      setDone(true);
    } catch (error) {
      setSubmitError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <main className="resign-page">
        <p className="att-empty">{t("resignLoading")}</p>
      </main>
    );
  }

  if (loadError || !data) {
    return (
      <main className="resign-page">
        <div className="resign-card">
          <h1>{t("resignPageTitle")}</h1>
          <p className="resign-error" role="alert">
            {loadError || t("resignInvalid")}
          </p>
        </div>
      </main>
    );
  }

  if (done) {
    return (
      <main className="resign-page">
        <div className="resign-card">
          <h1>{t("resignDone")}</h1>
          <p className="resign-lead">{t("resignDoneLead")}</p>
        </div>
      </main>
    );
  }

  if (data.status !== "sent") {
    return (
      <main className="resign-page">
        <div className="resign-card">
          <h1>{t("resignPageTitle")}</h1>
          <p className="resign-lead">{t("resignAlreadySubmitted")}</p>
        </div>
      </main>
    );
  }

  return (
    <main className="resign-page">
      <form className="resign-card" onSubmit={(event) => void submit(event)}>
        <h1>{t("resignPageTitle")}</h1>
        <p className="resign-lead">{t("resignLead")}</p>

        <dl className="resign-employee">
          <div>
            <dt>{t("resignEmployee")}</dt>
            <dd>{data.employee_name}</dd>
          </div>
          <div>
            <dt>{t("resignEmployeeNo")}</dt>
            <dd>{data.employee_no || "-"}</dd>
          </div>
          <div>
            <dt>{t("resignDepartment")}</dt>
            <dd>{data.department || "-"}</dd>
          </div>
          <div>
            <dt>{t("resignPosition")}</dt>
            <dd>{data.position || "-"}</dd>
          </div>
        </dl>

        <label className="field">
          <span>
            {t("resignLastDay")} *
          </span>
          <input
            type="date"
            value={form.last_working_day}
            onChange={(event) => update("last_working_day", event.target.value)}
            required
          />
        </label>

        <label className="field">
          <span>
            {t("resignReasonCategory")} · {t("resignOptional")}
          </span>
          <select
            value={form.reason_category}
            onChange={(event) => update("reason_category", event.target.value)}
          >
            <option value="">{t("resignPickReason")}</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>
            {t("resignReasonDetail")} · {t("resignOptional")}
          </span>
          <textarea
            rows={3}
            value={form.reason_detail}
            onChange={(event) => update("reason_detail", event.target.value)}
          />
        </label>

        <label className="field">
          <span>
            {t("resignHandoverTo")} · {t("resignOptional")}
          </span>
          <input
            value={form.handover_to}
            onChange={(event) => update("handover_to", event.target.value)}
          />
        </label>

        <label className="field">
          <span>
            {t("resignHandoverNote")} · {t("resignOptional")}
          </span>
          <textarea
            rows={3}
            value={form.handover_note}
            onChange={(event) => update("handover_note", event.target.value)}
          />
        </label>

        <label className="field">
          <span>
            {t("resignContactAfter")} · {t("resignOptional")}
          </span>
          <input
            value={form.contact_after}
            onChange={(event) => update("contact_after", event.target.value)}
          />
        </label>

        {submitError && (
          <p className="resign-error" role="alert">
            {submitError}
          </p>
        )}

        <p className="resign-hint">{t("resignRequiredHint")}</p>
        <Button variant="primary" type="submit" busy={busy}>
          {busy ? t("resignSubmitting") : t("resignSubmit")}
        </Button>
      </form>
    </main>
  );
}
