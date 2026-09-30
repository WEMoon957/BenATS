/** 本文件负责打开智联招聘登录页，交由用户手动完成登录。 */

import { detachBrowserSession, ensurePage } from '../browser/index.js';
import { LOGIN_URL } from '../common/selectors.js';

/**
 * 打开智联招聘登录页。
 *
 * 登录必须由用户本人完成（扫码或账号密码），CLI 不代填任何凭据。
 * 打开后立即断开 CDP 连接但不关闭窗口，用户可以在浏览器里从容登录，
 * 登录态保存在本机专用目录，后续命令自动复用。
 */
export async function runLogin(): Promise<string> {
  const page = await ensurePage();
  await page.goto(LOGIN_URL, { waitUntil: 'domcontentloaded' });
  await detachBrowserSession();
  return [
    `已打开智联招聘登录页：${LOGIN_URL}`,
    '',
    '请在浏览器窗口里完成登录（扫码或账号密码）。',
    '登录态保存在本机，登录一次后后续命令会直接复用。',
  ].join('\n');
}
