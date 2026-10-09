// 全局会话：登录 token 的存取，与考勤模块共用同一账号体系。
export const SESSION_TOKEN_KEY = "talentHub.attendanceToken";

export function getSessionToken(): string {
  if (typeof localStorage === "undefined") return "";
  return localStorage.getItem(SESSION_TOKEN_KEY) || "";
}

export function setSessionToken(token: string): void {
  localStorage.setItem(SESSION_TOKEN_KEY, token);
}

export function clearSessionToken(): void {
  localStorage.removeItem(SESSION_TOKEN_KEY);
}
