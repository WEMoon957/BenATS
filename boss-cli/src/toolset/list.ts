import type { Page } from 'puppeteer-core';
import {
  LIST_FILTER_GAP_MS,
  LIST_MIN_BEFORE_EMPTY_OK_MS,
  LIST_POLL_MS,
  sleepRandom,
} from '../browser/index.js';
import { BOSS_CHAT_INDEX_URL, isBossChatIndexUrl } from '../common/auth.js';
import { ensurePage } from '../common/ensure_page.js';
import { withBossSessionPage } from '../common/boss_session_page.js';

type CandidateItem = {
  name: string;
  job: string;
  time: string;
  message: string;
  unreadCount: number;
};

/** 滚动收集会话时最多执行多少轮，避免列表异常时无限滚动。会话列表可达上千条，轮数要够。 */
export const LIST_SCROLL_MAX_ROUNDS = 200;

/** 读取当前 DOM 里已渲染的会话条目，以及列表滚动容器的位置信息。 */
const READ_LIST_STATE_SCRIPT = `(() => {
  const norm = (v) => (v ?? "").replace(/\\s+/g, " ").trim();
  const items = Array.from(document.querySelectorAll(".geek-item")).map((el) => {
    const name = norm(el.querySelector(".geek-name")?.textContent);
    const job = norm(el.querySelector(".source-job")?.textContent);
    const time = norm(el.querySelector(".time")?.textContent);
    const message = norm(el.querySelector(".push-text")?.textContent);
    const badge = el.querySelector(".badge-count");
    let unreadCount = 0;
    if (badge) {
      const digits = norm(badge.textContent).replace(/\\D/g, "");
      if (digits) unreadCount = parseInt(digits, 10) || 0;
    }
    return { name, job, time, message, unreadCount };
  });
  let node = document.querySelector(".geek-item");
  let scroller = null;
  while (node) {
    if (node.scrollHeight > node.clientHeight) { scroller = node; break; }
    node = node.parentElement;
  }
  if (!scroller) return { items, atEnd: true };
  return {
    items,
    atEnd: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2,
  };
})()`;

/** 把列表滚动容器回到顶部，确保从第一条开始收集。 */
export const SCROLL_LIST_TO_TOP_SCRIPT = `(() => {
  let node = document.querySelector(".geek-item");
  let scroller = null;
  while (node) {
    if (node.scrollHeight > node.clientHeight) { scroller = node; break; }
    node = node.parentElement;
  }
  if (!scroller) return false;
  scroller.scrollTop = 0;
  return true;
})()`;

/** 把列表滚动容器向下滚动约一屏（会话列表是虚拟列表，未滚到的条目不在 DOM 里）。 */
export const SCROLL_LIST_DOWN_SCRIPT = `(() => {
  let node = document.querySelector(".geek-item");
  let scroller = null;
  while (node) {
    if (node.scrollHeight > node.clientHeight) { scroller = node; break; }
    node = node.parentElement;
  }
  if (!scroller) return false;
  const step = Math.max(240, Math.round(scroller.clientHeight * 0.85));
  scroller.scrollTop = Math.min(scroller.scrollTop + step, scroller.scrollHeight);
  return true;
})()`;

/**
 * 滚动列表并收集全部会话：先回到顶部，再逐屏读取并下滚，最后按姓名合并去重。
 * 虚拟列表只保留可视区附近的节点，因此必须边滚边收，不能只读一次。
 */
async function collectAllCandidates(page: Page): Promise<CandidateItem[]> {
  await page.evaluate(SCROLL_LIST_TO_TOP_SCRIPT);
  await sleepRandom(LIST_POLL_MS.min, LIST_POLL_MS.max);

  const collected = new Map<string, CandidateItem>();
  let previousCount = -1;
  for (let round = 0; round < LIST_SCROLL_MAX_ROUNDS; round++) {
    const state = (await page.evaluate(READ_LIST_STATE_SCRIPT)) as {
      items: CandidateItem[];
      atEnd: boolean;
    };
    for (const item of state.items) {
      if (item.name && !collected.has(item.name)) {
        collected.set(item.name, item);
      }
    }
    if (state.atEnd && collected.size === previousCount) {
      break;
    }
    previousCount = collected.size;
    if (state.atEnd) {
      await sleepRandom(LIST_POLL_MS.min, LIST_POLL_MS.max);
      continue;
    }
    await page.evaluate(SCROLL_LIST_DOWN_SCRIPT);
    await sleepRandom(LIST_POLL_MS.min, LIST_POLL_MS.max);
  }
  return [...collected.values()];
}

async function waitForCandidateListSettled(
  page: Page,
  opts: { timeoutMs: number; pollMsMin: number; pollMsMax: number; minMsBeforeEmptyOk: number },
): Promise<void> {
  const start = Date.now();
  let prev = -1;
  let stable = 0;
  while (Date.now() - start < opts.timeoutMs) {
    const n = (await page.evaluate(
      `(() => document.querySelectorAll(".geek-item").length)()`,
    )) as number;
    const elapsed = Date.now() - start;
    if (n === prev) {
      stable++;
    } else {
      prev = n;
      stable = 1;
    }
    if (stable >= 2) {
      if (n > 0) {
        return;
      }
      if (n === 0 && elapsed >= opts.minMsBeforeEmptyOk) {
        return;
      }
    }
    await sleepRandom(opts.pollMsMin, opts.pollMsMax);
  }
}

async function clickChatFilterTab(page: Page, label: string): Promise<void> {
  const labelLiteral = JSON.stringify(label);
  await page.evaluate(`(() => {
    const targetText = ${labelLiteral};
    const container = document.querySelector(".chat-message-filter-left");
    if (!container) {
      throw new Error("未找到聊天筛选容器：.chat-message-filter-left");
    }
    const spans = Array.from(container.querySelectorAll("span"));
    const norm = (v) => (v ?? "").replace(/\\s+/g, "");
    const target = spans.find((el) => norm(el.textContent).includes(targetText));
    if (!target) {
      const labels = spans.map((el) => norm(el.textContent)).filter(Boolean).join(",");
      throw new Error("未找到聊天筛选项：" + targetText + "；当前筛选项：" + (labels || "空"));
    }
    target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    target.click();
  })()`);
}

async function waitForChatFilterTabSelected(page: Page, label: string): Promise<void> {
  const labelLiteral = JSON.stringify(label);
  await page.waitForFunction(
    `(() => {
      const targetText = ${labelLiteral};
      const container = document.querySelector(".chat-message-filter-left");
      if (!container) return false;
      const norm = (v) => (v ?? "").replace(/\\s+/g, "");
      const tabs = Array.from(container.querySelectorAll("span"));
      const tab = tabs.find((el) => norm(el.textContent).includes(targetText));
      if (!tab) return false;
      const cls = String(tab.className || "");
      const selectedByClass = /active|selected|current|checked/.test(cls);
      const selectedByAria = tab.getAttribute("aria-selected") === "true";
      const selectedByAncestor = !!tab.closest(".active, .selected, .current, .checked");
      return selectedByClass || selectedByAria || selectedByAncestor;
    })()`,
    { timeout: 8_000 },
  );
}

export async function ensureChatListReady(
  page: Page,
  filter: 'all' | 'unread' = 'all',
): Promise<void> {
  await ensurePage(page, {
    name: '沟通列表页',
    targetUrl: BOSS_CHAT_INDEX_URL,
    matches: isBossChatIndexUrl,
  });

  await page.waitForFunction(
    `(() => {
      const filter = document.querySelector(".chat-message-filter-left");
      if (!filter) return false;
      const tabs = Array.from(filter.querySelectorAll("span"));
      return tabs.length >= 2;
    })()`,
    { timeout: 15_000 },
  );

  const filterLabel = filter === 'unread' ? '未读' : '全部';
  await clickChatFilterTab(page, filterLabel);
  await sleepRandom(LIST_FILTER_GAP_MS.min, LIST_FILTER_GAP_MS.max);
  await waitForChatFilterTabSelected(page, filterLabel);
  await waitForCandidateListSettled(page, {
    timeoutMs: 18_000,
    pollMsMin: LIST_POLL_MS.min,
    pollMsMax: LIST_POLL_MS.max,
    minMsBeforeEmptyOk: LIST_MIN_BEFORE_EMPTY_OK_MS,
  });
}

export async function runGetCandidateList(
  opts: { unreadOnly?: boolean } = {},
): Promise<string> {
  const unreadOnly = opts.unreadOnly === true;

  try {
    return await withBossSessionPage(async (page) => {
      await ensureChatListReady(page, unreadOnly ? 'unread' : 'all');

      const candidates = await collectAllCandidates(page);
      const withUnread = candidates.filter((it) => it.unreadCount > 0).length;
      const visible = unreadOnly ? candidates : candidates;
      const lines = visible.map((it, idx) => {
        const base = `${idx + 1}. ${it.name}${it.job ? `｜${it.job}` : ''}`;
        const meta = [
          it.unreadCount > 0 ? `未读:${it.unreadCount}` : '',
          it.time ? `时间:${it.time}` : '',
          it.message ? `消息:${it.message}` : '',
        ]
          .filter(Boolean)
          .join('｜');
        return meta ? `${base}｜${meta}` : base;
      });
      const previewText =
        lines.length > 0 ? `候选人明细：\n${lines.join('\n')}` : '候选人明细：暂无。';

      return [
        unreadOnly
          ? `未读筛选：共 ${visible.length} 人（已切换页面「未读」筛选）。`
          : `沟通列表共 ${candidates.length} 人，其中 ${withUnread} 人有未读消息。`,
        previewText,
      ]
        .filter(Boolean)
        .join('\n');
    });
  } catch (e) {
    if (e instanceof Error) {
      throw e;
    }
    throw new Error(`获取候选人列表失败：${String(e)}`);
  }
}
