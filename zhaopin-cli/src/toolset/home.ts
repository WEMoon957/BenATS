/** 本文件负责直接把浏览器带到智联招聘企业端入口页。 */

import { detachBrowserSession, ensurePage } from '../browser/index.js';
import { ENTRY_URL } from '../common/selectors.js';

/**
 * 直接跳到智联招聘企业端入口页（候选人推荐列表）。
 *
 * 用于跳过手动点击导航，一条命令落到企业端；
 * 打开后立即断开 CDP 连接但不关闭窗口，登录态供后续命令复用。
 * 若还没登录，平台会把页面弹到登录页，此时返回提示而不误报成功。
 */
export async function runHome(): Promise<string> {
  const page = await ensurePage();
  await page.goto(ENTRY_URL, { waitUntil: 'domcontentloaded' });
  const landedUrl = page.url();
  await detachBrowserSession();

  if (!landedUrl.startsWith('https://rd6.zhaopin.com')) {
    return [
      `已尝试打开智联招聘企业端，但页面停在了：${landedUrl}`,
      '',
      '看起来还没登录。请先执行 zhaopin login 完成登录，再运行本命令。',
    ].join('\n');
  }

  return `已跳到智联招聘企业端：${ENTRY_URL}`;
}
