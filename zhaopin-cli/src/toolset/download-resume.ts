/** 本文件负责把候选人发来的附件简历统一下载到本地 downloads 目录。 */

import { readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { Page } from 'puppeteer-core';
import { ensurePage, sleepRandom } from '../browser/index.js';
import {
  ATTACH_DOWNLOAD_URL_MARKER,
  ATTACH_RESUME_CARD_SELECTOR,
  CHAT_URL,
  SESSION_ITEM_SELECTOR,
  SESSION_LIST_SCROLL_SELECTOR,
  SESSION_NAME_SELECTOR,
} from '../common/selectors.js';
import { RESUME_DOWNLOADS_DIR } from '../config.js';

/** 点击附件卡片后等待下载直链标签页出现的上限。 */
const ATTACH_TARGET_WAIT_MS = 15000;

/** 打开会话后等聊天流渲染的时长区间。 */
const CHAT_OPEN_SETTLE_MS = { min: 2500, max: 4000 } as const;

/** 批量下载时两个人之间的等待区间，降低平台风控风险。 */
const PERSON_GAP_MS = { min: 2000, max: 4000 } as const;

/** 会话列表扫描的加载轮次与人数上限。 */
const SESSION_SCAN_MAX_ROUNDS = 80;
const SESSION_SCAN_MAX_NAMES = 300;

/** 下载结果：一段人话说明、落盘路径（未下载时为空）与结果类型。 */
export type DownloadResult = {
  text: string;
  path: string;
  outcome: 'downloaded' | 'skipped';
};

/** 清理文件名里的非法字符。 */
function safeFileBase(name: string): string {
  return name.replace(/[\\/:*?"<>|\s]+/g, '').slice(0, 40) || '候选人';
}

/** 当前时间戳，用于避免同名文件互相覆盖。 */
function timestamp(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  );
}

/** 确保当前在聊天页。 */
async function ensureChatPage(page: Page): Promise<void> {
  if (!page.url().startsWith(CHAT_URL)) {
    await page.goto(CHAT_URL, { waitUntil: 'domcontentloaded', timeout: 40000 });
    await sleepRandom({ min: 3000, max: 5000 });
  }
}

/** 在会话列表里按姓名点开会话；找不到返回 false。 */
async function openSession(page: Page, name: string): Promise<boolean> {
  return page.evaluate(
    (args) => {
      const { target, itemSelector, nameSelector } = args;
      for (const item of Array.from(
        document.querySelectorAll(itemSelector),
      ) as HTMLElement[]) {
        const title = item.querySelector(nameSelector)?.textContent?.trim() || '';
        if (title === target || title.includes(target)) {
          item.scrollIntoView({ block: 'center' });
          item.click();
          return true;
        }
      }
      return false;
    },
    {
      target: name,
      itemSelector: SESSION_ITEM_SELECTOR,
      nameSelector: SESSION_NAME_SELECTOR,
    },
  );
}

/** 判断当前会话里有没有对方发来的附件简历卡片。 */
async function hasAttachCard(page: Page): Promise<boolean> {
  return (await page.$(ATTACH_RESUME_CARD_SELECTOR)) !== null;
}

/** 从下载直链标签页里抓取文件字节与建议文件名。 */
async function fetchAttachmentBytes(attachPage: Page): Promise<{
  base64: string;
  disposition: string;
  type: string;
}> {
  return attachPage.evaluate(async () => {
    const response = await fetch(location.href, { credentials: 'include' });
    if (!response.ok) {
      throw new Error('HTTP ' + response.status);
    }
    const buffer = await response.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return {
      base64: btoa(binary),
      disposition: response.headers.get('content-disposition') || '',
      type: response.headers.get('content-type') || '',
    };
  });
}

/** 从 Content-Disposition / Content-Type 推断文件扩展名。 */
function pickExtension(disposition: string, type: string, url: string): string {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  const plain = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
  const rawName = encoded ? decodeURIComponent(encoded) : (plain || '');
  const fromName = /\.([A-Za-z0-9]{2,5})$/.exec(rawName.trim())?.[1];
  if (fromName) return fromName.toLowerCase();
  if (url.includes('.pdf') || type.includes('pdf')) return 'pdf';
  if (type.includes('msword')) return 'doc';
  if (type.includes('officedocument.wordprocessingml')) return 'docx';
  return 'pdf';
}

/**
 * 下载当前会话里对方发来的附件简历，返回落盘路径。
 *
 * 前置条件：已打开目标候选人的会话，且会话里存在附件简历卡片。
 * 直链响应有两种形态，两条路都要接住：
 * - PDF 等内联内容：新开标签页渲染，需要从标签页里抓字节自己写盘；
 * - doc 等附件内容：浏览器经 downloadBehavior 直接落盘到下载目录，无新标签页。
 */
async function downloadCurrentSessionResume(page: Page, name: string): Promise<string> {
  const browser = page.browser();
  const beforeUrls = new Set((await browser.pages()).map((item) => item.url()));
  const beforeFiles = new Set(await readdir(RESUME_DOWNLOADS_DIR));

  const clicked = await page.evaluate((selector) => {
    const card = document.querySelector(selector) as HTMLElement | null;
    if (!card) return false;
    card.scrollIntoView({ block: 'center' });
    card.click();
    return true;
  }, ATTACH_RESUME_CARD_SELECTOR);
  if (!clicked) {
    throw new Error('点击附件简历卡片失败。');
  }

  const deadline = Date.now() + ATTACH_TARGET_WAIT_MS;
  while (Date.now() < deadline) {
    // 形态一：浏览器已把文件直接落盘
    const entries = await readdir(RESUME_DOWNLOADS_DIR);
    const landed = entries.find(
      (fileName) => !beforeFiles.has(fileName) && !fileName.endsWith('.crdownload'),
    );
    if (landed) {
      return join(RESUME_DOWNLOADS_DIR, landed);
    }

    // 形态二：新开下载直链标签页，抓字节写盘
    const pages = await browser.pages();
    const attachPage =
      pages.find(
        (item) => !beforeUrls.has(item.url()) && item.url().includes(ATTACH_DOWNLOAD_URL_MARKER),
      ) || null;
    if (attachPage) {
      try {
        const bytes = await fetchAttachmentBytes(attachPage);
        const extension = pickExtension(bytes.disposition, bytes.type, attachPage.url());
        const fileName = `${safeFileBase(name)}-附件简历-${timestamp()}.${extension}`;
        const absPath = join(RESUME_DOWNLOADS_DIR, fileName);
        await writeFile(absPath, Buffer.from(bytes.base64, 'base64'));
        return absPath;
      } finally {
        await attachPage.close().catch(() => undefined);
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('点击附件简历后既没有文件落盘，也没有打开下载直链页面。');
}

/** 对一位候选人下载附件简历；没有附件简历或已下载过时返回带说明的结果。 */
async function downloadOne(page: Page, name: string): Promise<DownloadResult> {
  await ensureChatPage(page);

  // 同一人已下载过就不再重复拉取，方便 download-all 反复跑不堆文件。
  const base = safeFileBase(name);
  const existing = (await readdir(RESUME_DOWNLOADS_DIR)).find(
    (fileName) => fileName.startsWith(`${base}-附件简历-`) || fileName.startsWith(`${base}_`),
  );
  if (existing) {
    return {
      text: `候选人「${name}」的附件简历之前已下载过：${join(RESUME_DOWNLOADS_DIR, existing)}（跳过重复）`,
      path: '',
      outcome: 'skipped',
    };
  }

  const found = await openSession(page, name);
  if (!found) {
    throw new Error(`聊天列表里没有找到「${name}」。`);
  }
  await sleepRandom(CHAT_OPEN_SETTLE_MS);

  if (!(await hasAttachCard(page))) {
    return {
      text: `候选人「${name}」的会话里没有发现对方发来的附件简历。`,
      path: '',
      outcome: 'skipped',
    };
  }

  const path = await downloadCurrentSessionResume(page, name);
  return { text: `候选人「${name}」的附件简历已下载：${path}`, path, outcome: 'downloaded' };
}

/** 批量执行的汇总行。 */
function batchSummary(kind: string, total: number, downloaded: number, failed: number): string {
  return `批量${kind}结束：共 ${total} 人，下载 ${downloaded}，跳过 ${total - downloaded - failed}，失败 ${failed}。`;
}

/**
 * 对多位候选人依次下载附件简历，返回汇总文本。
 *
 * 单个人失败不会中断整批；文本末尾附下载数量与失败数量。
 */
export async function runDownloadResume(
  names: string[],
): Promise<{ text: string; failed: number }> {
  const page = await ensurePage();
  await ensureChatPage(page);

  const lines: string[] = [];
  let downloaded = 0;
  let failed = 0;

  for (const [index, name] of names.entries()) {
    if (index > 0) {
      await sleepRandom(PERSON_GAP_MS);
    }
    try {
      const result = await downloadOne(page, name);
      lines.push(result.text);
      if (result.outcome === 'downloaded') downloaded += 1;
    } catch (error) {
      failed += 1;
      const message = error instanceof Error ? error.message : String(error);
      lines.push(`候选人「${name}」下载失败：${message}`);
    }
  }

  lines.push(batchSummary('下载', names.length, downloaded, failed));
  return { text: lines.join('\n'), failed };
}

/** 滚动会话列表收集全部候选人姓名（虚拟列表需要逐步滚动加载）。 */
async function collectSessionNames(page: Page): Promise<string[]> {
  const seen = new Set<string>();
  let stableRounds = 0;

  for (let round = 0; round < SESSION_SCAN_MAX_ROUNDS; round += 1) {
    const names = await page.evaluate(
      (args) => {
        const { itemSelector, nameSelector } = args;
        return Array.from(document.querySelectorAll(`${itemSelector} ${nameSelector}`)).map(
          (el) => (el.textContent || '').trim(),
        );
      },
      { itemSelector: SESSION_ITEM_SELECTOR, nameSelector: SESSION_NAME_SELECTOR },
    );
    const beforeSize = seen.size;
    for (const name of names) {
      if (name) seen.add(name);
    }
    if (seen.size >= SESSION_SCAN_MAX_NAMES) break;
    stableRounds = seen.size === beforeSize ? stableRounds + 1 : 0;
    if (stableRounds >= 3) break;

    const scrolled = await page.evaluate((selector) => {
      const list =
        document.querySelector(selector) ||
        document.querySelector('.im-session-list');
      if (!list) return false;
      list.scrollTop += 900;
      return true;
    }, SESSION_LIST_SCROLL_SELECTOR);
    if (!scrolled) break;
    await sleepRandom({ min: 700, max: 1200 });
  }

  return Array.from(seen);
}

/**
 * 扫描聊天列表全部会话，把所有「对方发来的附件简历」统一下载到本地。
 *
 * 没发简历的会话只跳过不计为失败；一个人失败不中断整批。
 */
export async function runDownloadAllResumes(): Promise<{ text: string; failed: number }> {
  const page = await ensurePage();
  await ensureChatPage(page);

  const names = await collectSessionNames(page);
  if (names.length === 0) {
    return { text: '聊天列表里没有读到任何会话。', failed: 1 };
  }

  const lines: string[] = [`开始扫描 ${names.length} 个会话：`];
  const skipped: string[] = [];
  let downloaded = 0;
  let failed = 0;

  for (const [index, name] of names.entries()) {
    if (index > 0) {
      await sleepRandom(PERSON_GAP_MS);
    }
    try {
      const result = await downloadOne(page, name);
      lines.push(result.text);
      if (result.outcome === 'downloaded') {
        downloaded += 1;
      } else {
        skipped.push(name);
      }
    } catch (error) {
      failed += 1;
      const message = error instanceof Error ? error.message : String(error);
      lines.push(`候选人「${name}」下载失败：${message}`);
    }
  }

  lines.push(
    `统一下载结束：扫描 ${names.length} 人，下载 ${downloaded} 份，` +
      `跳过 ${skipped.length} 人，失败 ${failed}。` +
      (skipped.length > 0 ? `\n跳过（无附件简历或已下载过）：${skipped.join('、')}` : ''),
  );
  return { text: lines.join('\n'), failed };
}
