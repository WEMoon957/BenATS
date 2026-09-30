/** 本文件负责维护与浏览器的会话，供各命令复用同一个标签页。 */

import type { Browser, Page } from 'puppeteer-core';
import { clearSpawnedChromeProcessRef, connectBrowser } from './cdp_browser.js';

/** 智联招聘域名，用于在多个标签中优先选中招聘页面。 */
const ZHAOPIN_HOST = 'zhaopin.com';

let browserRef: Browser | null = null;
let pageRef: Page | null = null;
let connectPromise: Promise<void> | null = null;

/** 浏览器连接断开时清空本地引用，下次自动重连。 */
function attachDisconnectedHandler(browser: Browser): void {
  browser.once('disconnected', () => {
    if (browserRef === browser) {
      browserRef = null;
      pageRef = null;
      console.error('[zhaopin-cli] 与浏览器断开连接（窗口关闭或进程退出）；下次使用命令时会自动重连。');
    }
  });
}

/**
 * 读取浏览器当前标签页。
 *
 * 刚建立 CDP 连接时页面目标可能尚未初始化完毕，此时直接取页面会抛
 * 「Requesting main frame too early」；这里做短暂重试，等目标就绪。
 */
async function listPages(browser: Browser, attempts = 8): Promise<Page[]> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return (await browser.pages()).filter((page) => !page.isClosed());
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  }
  throw lastError instanceof Error
    ? lastError
    : new Error('无法读取浏览器标签页，请确认浏览器窗口正常打开。');
}

/** 选一个主标签：优先智联页面，其次任意非空白页，都没有则新建。 */
async function pickOrCreatePage(browser: Browser): Promise<Page> {
  const pages = await listPages(browser);
  if (pages.length === 0) {
    return browser.newPage();
  }

  const urls = pages.map((page) => {
    try {
      return page.url();
    } catch {
      return '';
    }
  });

  const zhaopinPage = pages.find((_, index) => {
    const url = urls[index] ?? '';
    return url.length > 0 && url !== 'about:blank' && url.includes(ZHAOPIN_HOST);
  });
  if (zhaopinPage) return zhaopinPage;

  const nonBlank = pages.find((_, index) => {
    const url = urls[index] ?? '';
    return url.length > 0 && url !== 'about:blank';
  });
  if (nonBlank) return nonBlank;

  return pages[0]!;
}

/** 建立或复用浏览器会话，返回可用的主标签页。 */
export async function ensurePage(): Promise<Page> {
  if (browserRef?.connected && pageRef && !pageRef.isClosed()) {
    return pageRef;
  }

  if (connectPromise) {
    await connectPromise;
    if (pageRef && !pageRef.isClosed()) return pageRef;
  }

  connectPromise = (async () => {
    const previous = browserRef;
    if (previous) {
      try {
        previous.removeAllListeners('disconnected');
        await previous.close();
      } catch {
        /* 已断开时忽略 */
      }
      browserRef = null;
      pageRef = null;
    }
    const browser = await connectBrowser();
    browserRef = browser;
    attachDisconnectedHandler(browser);
    pageRef = await pickOrCreatePage(browser);
  })();

  try {
    await connectPromise;
  } finally {
    connectPromise = null;
  }

  if (!pageRef) {
    throw new Error('浏览器已连接，但无法获取可用标签页。');
  }
  return pageRef;
}

/** 把某个标签页设为主操作页。 */
export function setSessionPage(page: Page): void {
  if (!browserRef?.connected || page.isClosed()) return;
  pageRef = page;
}

/**
 * 只断开与浏览器的 CDP 连接，不关闭浏览器窗口。
 * 用于 `login` 这类需要用户继续在浏览器里操作的场景。
 */
export async function detachBrowserSession(): Promise<void> {
  const browser = browserRef;
  if (!browser) return;
  try {
    browser.removeAllListeners('disconnected');
    await (browser as unknown as { disconnect?: () => Promise<void> }).disconnect?.();
  } catch {
    /* 断开失败时仍保留窗口，仅清理引用 */
  }
  try {
    const proc = browser.process();
    proc?.unref();
  } catch {
    /* connect 模式下没有子进程句柄 */
  }
  clearSpawnedChromeProcessRef();
  browserRef = null;
  pageRef = null;
}
