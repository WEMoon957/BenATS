import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import type { Page } from 'puppeteer-core';
import { ONLINE_RESUME_IFRAME_WAIT_MAX_MS, snapshotBossPageViewport } from '../browser/index.js';
import {
  closeBossPaywallPopupIfPresent,
  describeBossPaywallPopupIfPresent,
  waitForCResumeIframeOrPaywall,
} from '../common/boss_paywall_popup.js';
import {
  captureCResumeIframeToFile,
  closeCResumePanel,
  safeResumeScreenshotFileBase,
  waitForVisibleCResumeIframeReady,
} from '../common/c_resume_capture.js';
import { ensureAppDataLayout, RESUME_DOWNLOADS_DIR, RESUME_SCREENSHOTS_DIR } from '../config.js';
import { runChatActionOnCurrentConversation } from './action.js';
import { runOpenCandidateChat } from './chat.js';

const DOWNLOAD_WAIT_MS = 30_000;
const DOWNLOAD_POLL_MS = 500;

type AttachmentState = {
  isAttachment: boolean;
  pendingAccept: boolean;
  hasDownloadTrigger: boolean;
  hasPreviewTrigger: boolean;
  title: string;
  dump: string;
};

/**
 * 检测当前聊天流中的「附件简历」消息卡片：是否待接收（有可点「同意」）、
 * 是否存在下载入口（`a[download]`）或预览入口（「点击预览附件简历」），并 dump 卡片内可交互元素用于排查。
 */
const INSPECT_ATTACHMENT_SCRIPT = `(() => {
  const norm = (v) => (v ?? "").replace(/\\s+/g, "").trim();
  function isDisabled(el) {
    if (!(el instanceof HTMLElement)) return true;
    const cls = el.className ?? "";
    if (/disabled|forbid|ban/i.test(cls)) return true;
    return el.getAttribute("disabled") !== null;
  }
  const items = Array.from(document.querySelectorAll(".chat-message-list .message-item"));
  for (let i = items.length - 1; i >= 0; i--) {
    const friend = items[i].querySelector(".item-friend");
    if (!friend) continue;
    const title = norm(friend.querySelector(".message-card-top-title")?.textContent);
    const hasIcon = !!friend.querySelector(".resume-icon");
    if (!hasIcon && !title.includes("附件简历")) continue;
    const agreeBtn = Array.from(friend.querySelectorAll(".message-card-buttons .card-btn"))
      .find((btn) => norm(btn.textContent).includes("同意"));
    const pendingAccept = !!agreeBtn && !isDisabled(agreeBtn);
    const hasDownloadTrigger = !!friend.querySelector("a[download]");
    const hasPreviewTrigger = Array.from(friend.querySelectorAll(".message-card-buttons .card-btn"))
      .some((btn) => norm(btn.textContent).includes("预览"));
    const dump = Array.from(friend.querySelectorAll("a, button, .card-btn, [download]"))
      .map((el) => {
        const tag = el.tagName.toLowerCase();
        const cls = typeof el.className === "string" ? el.className : "";
        const href = el.getAttribute("href") || "";
        const dl = el.hasAttribute("download") ? "download" : "";
        const text = norm(el.textContent).slice(0, 40);
        return tag + "|" + cls + "|href=" + href + "|" + dl + "|text=" + text;
      })
      .slice(0, 20)
      .join("\\n");
    return { isAttachment: true, pendingAccept, hasDownloadTrigger, hasPreviewTrigger, title, dump };
  }
  return { isAttachment: false, pendingAccept: false, hasDownloadTrigger: false, hasPreviewTrigger: false, title: "", dump: "" };
})()`;

/** 点击附件消息卡片内带 `download` 属性的下载链接，触发浏览器下载。 */
const CLICK_DOWNLOAD_TRIGGER_SCRIPT = `(() => {
  const norm = (v) => (v ?? "").replace(/\\s+/g, "").trim();
  const items = Array.from(document.querySelectorAll(".chat-message-list .message-item"));
  for (let i = items.length - 1; i >= 0; i--) {
    const friend = items[i].querySelector(".item-friend");
    if (!friend) continue;
    const title = norm(friend.querySelector(".message-card-top-title")?.textContent);
    const hasIcon = !!friend.querySelector(".resume-icon");
    if (!hasIcon && !title.includes("附件简历")) continue;
    const link = friend.querySelector("a[download]");
    if (!link) return false;
    link.scrollIntoView({ block: "center", inline: "nearest" });
    link.click();
    return true;
  }
  return false;
})()`;

/** 点击附件消息卡片内的「点击预览附件简历」按钮，打开 c-resume 预览弹层。 */
const CLICK_PREVIEW_TRIGGER_SCRIPT = `(() => {
  const norm = (v) => (v ?? "").replace(/\\s+/g, "").trim();
  const items = Array.from(document.querySelectorAll(".chat-message-list .message-item"));
  for (let i = items.length - 1; i >= 0; i--) {
    const friend = items[i].querySelector(".item-friend");
    if (!friend) continue;
    const title = norm(friend.querySelector(".message-card-top-title")?.textContent);
    const hasIcon = !!friend.querySelector(".resume-icon");
    if (!hasIcon && !title.includes("附件简历")) continue;
    const btn = Array.from(friend.querySelectorAll(".message-card-buttons .card-btn"))
      .find((el) => norm(el.textContent).includes("预览"));
    if (!btn) return false;
    btn.scrollIntoView({ block: "center", inline: "nearest" });
    btn.click();
    return true;
  }
  return false;
})()`;

async function snapshotDownloadDir(): Promise<Set<string>> {
  return new Set(await readdir(RESUME_DOWNLOADS_DIR));
}

/**
 * 等待下载目录出现「本次触发产生的新文件」。Chrome 下载进行中会先写 `.crdownload`
 * 临时后缀，完成后重命名为最终文件，因此跳过 `.crdownload` 视为仍在进行。
 */
async function waitForDownloadedFile(
  downloadDir: string,
  before: Set<string>,
  timeoutMs: number,
): Promise<string> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const entries = await readdir(downloadDir);
    const fresh = entries.find((name) => !before.has(name) && !name.endsWith('.crdownload'));
    if (fresh) {
      return join(downloadDir, fresh);
    }
    await new Promise((resolve) => setTimeout(resolve, DOWNLOAD_POLL_MS));
  }
  throw new Error(`等待附件简历下载落盘超时（${timeoutMs / 1000}s）。`);
}

/**
 * 点击「点击预览附件简历」，对 c-resume iframe 整框截图，返回 PNG 路径。
 * 附件简历被 BOSS 改为「点击预览」后无 `a[download]` 入口，截图交由调用方 OCR。
 */
async function captureAttachmentResumePreview(page: Page, candidateName: string): Promise<string> {
  ensureAppDataLayout();
  const savedViewport = await snapshotBossPageViewport(page);

  const clicked = (await page.evaluate(CLICK_PREVIEW_TRIGGER_SCRIPT)) as boolean;
  if (!clicked) {
    throw new Error('未找到「点击预览附件简历」入口。');
  }

  const outcome = await waitForCResumeIframeOrPaywall(page, ONLINE_RESUME_IFRAME_WAIT_MAX_MS);
  if (outcome !== 'iframe') {
    const paywall = await describeBossPaywallPopupIfPresent(page);
    await closeBossPaywallPopupIfPresent(page);
    if (paywall) {
      throw new Error(paywall);
    }
    throw new Error('点击预览后未出现在线简历 iframe（c-resume）。');
  }

  const ready = await waitForVisibleCResumeIframeReady(page);
  if (!ready) {
    await closeCResumePanel(page);
    throw new Error('在线简历 iframe 已出现，但内容未在预期时间内渲染完成。');
  }

  const fileName = `attachment-resume-${safeResumeScreenshotFileBase(candidateName)}-${Date.now()}.png`;
  const absPath = join(RESUME_SCREENSHOTS_DIR, fileName);

  const ok = await captureCResumeIframeToFile(page, savedViewport, absPath);
  await closeCResumePanel(page);
  if (!ok) {
    throw new Error('附件简历截图失败。');
  }
  return absPath;
}

/**
 * 打开指定候选人聊天，接收（如仍待处理）并下载其附件简历文件。
 * 有 `a[download]` 直接下载原始文件；否则若为「点击预览附件简历」，截图预览供调用方 OCR。
 */
export async function runDownloadResume(page: Page, candidateName: string): Promise<string> {
  await runOpenCandidateChat(page, candidateName, true);

  let state = (await page.evaluate(INSPECT_ATTACHMENT_SCRIPT)) as AttachmentState;
  if (!state.isAttachment) {
    throw new Error('当前会话未检测到对方发来的附件简历消息（.resume-icon / 附件简历卡片）。');
  }
  if (state.pendingAccept) {
    await runChatActionOnCurrentConversation(page, { action: 'agree-resume' });
    state = (await page.evaluate(INSPECT_ATTACHMENT_SCRIPT)) as AttachmentState;
  }
  if (!state.hasDownloadTrigger) {
    if (state.hasPreviewTrigger) {
      const pngPath = await captureAttachmentResumePreview(page, candidateName);
      return `附件简历已下载：${pngPath}`;
    }
    throw new Error(
      `已收到附件简历，但未在附件卡片内找到下载入口（a[download]）。\n附件卡片可交互元素：\n${state.dump || '（空，请打开候选人聊天确认附件已接收）'}`,
    );
  }

  const before = await snapshotDownloadDir();
  const clicked = (await page.evaluate(CLICK_DOWNLOAD_TRIGGER_SCRIPT)) as boolean;
  if (!clicked) {
    throw new Error('下载入口已定位，但点击触发下载失败。');
  }
  const savedPath = await waitForDownloadedFile(RESUME_DOWNLOADS_DIR, before, DOWNLOAD_WAIT_MS);
  return `附件简历已下载：${savedPath}`;
}