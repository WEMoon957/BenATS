/** 本文件负责 zhaopin-cli 的本地数据目录与浏览器会话常量。 */

import { existsSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

/** 应用主目录，业务缓存统一放在 .cache 下。 */
export const APP_HOME = join(homedir(), '.zhaopin-cli');

/** 浏览器缓存根目录。 */
export const CACHE_DIR = join(APP_HOME, '.cache');

/** 智联专用浏览器数据目录，登录态保存在这里，与 boss-cli 相互独立。 */
export const BROWSER_USER_DATA_DIR = join(CACHE_DIR, 'browser-data');

/** 候选人详情截图目录，`open` 命令抓详情时落盘，便于排查。 */
export const DETAIL_SCREENSHOTS_DIR = join(CACHE_DIR, 'detail-screenshots');

/**
 * 远程调试端口。zhaopin-cli 使用独立的数据目录，因此可以固定占用一个端口，
 * 让多个命令直接通过 `http://127.0.0.1:<port>/json/version` 复用同一只浏览器。
 * 默认 53471，与 boss-cli 的 53470 错开，避免两个 CLI 互相抢端口。
 */
export const REMOTE_DEBUGGING_PORT: number = (() => {
  const raw = process.env.ZHAOPIN_BROWSER_REMOTE_DEBUGGING_PORT?.trim();
  if (raw) {
    const parsed = Number.parseInt(raw, 10);
    if (Number.isFinite(parsed) && parsed > 0 && parsed <= 65535) return parsed;
  }
  return 53471;
})();

let appDataLayoutReady = false;

/** 确保应用数据目录存在（幂等）。 */
export function ensureAppDataLayout(): void {
  if (appDataLayoutReady) return;
  appDataLayoutReady = true;
  for (const dir of [CACHE_DIR, BROWSER_USER_DATA_DIR, DETAIL_SCREENSHOTS_DIR]) {
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }
  }
}
