// 应用登录门：未登录时渲染登录表单，登录后渲染主应用。
// 会话 token 与考勤模块共用，登录一次即可访问全部工具。

import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { clearSessionToken, getSessionToken, setSessionToken } from "../auth";
import { t } from "../i18n";
import { App } from "../App";
import { Button } from "../ui/Button";

export function Root() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    let active = true;
    (async () => {
      if (!getSessionToken()) {
        if (active) setReady(true);
        return;
      }
      try {
        await api("/api/attendance/me");
        if (active) setAuthed(true);
      } catch {
        clearSessionToken();
      } finally {
        if (active) setReady(true);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  if (!ready) return null;
  if (!authed) return <LoginPanel onLogin={() => setAuthed(true)} />;
  return <App />;
}

function LoginPanel({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ token: string }>("/api/attendance/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setSessionToken(result.token);
      onLogin();
    } catch (err) {
      setError((err as Error).message);
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
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="field">
            <span>{t("attPassword")}</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && (
            <p role="alert" style={{ color: "var(--red)", fontSize: 12, margin: "0 0 8px" }}>
              {error}
            </p>
          )}
          <Button variant="primary" type="submit" busy={busy}>
            {t("attLogin")}
          </Button>
        </form>
      </div>
    </div>
  );
}
