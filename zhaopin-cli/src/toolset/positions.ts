/** 本文件负责职位选择弹层的读取与岗位切换。 */

import type { Page } from 'puppeteer-core';
import {
  POSITION_ACTION_GAP_MS,
  POSITION_PANEL_SETTLE_MS,
  POSITION_SEARCH_SETTLE_MS,
  POSITION_SELECTED_SETTLE_MS,
  ensurePage,
  sleepRandom,
} from '../browser/index.js';
import {
  humanClick,
  humanType,
  normalizeText,
  readHandleText,
  waitForFirstSelector,
  type Handle,
} from '../common/page_helpers.js';
import { ENTRY_URL, SELECTORS } from '../common/selectors.js';

/** 打开职位选择弹层并可选地输入搜索关键词。 */
async function openPositionPanel(page: Page, keyword: string): Promise<void> {
  const openEntry = await waitForFirstSelector(page, SELECTORS.positionOpen);
  if (!openEntry) {
    throw new Error(
      '未找到职位选择入口。请先确认已登录智联招聘，且当前在推荐页（可用 zhaopin recommend 打开）。',
    );
  }
  await humanClick(page, openEntry);
  await sleepRandom(POSITION_PANEL_SETTLE_MS);

  const input = await waitForFirstSelector(page, SELECTORS.positionInput);
  if (!input) {
    throw new Error('职位选择弹层已打开，但没有找到搜索输入框。');
  }
  if (keyword) {
    await humanType(page, input, keyword);
    await sleepRandom(POSITION_SEARCH_SETTLE_MS);
  }
}

/** 读取当前弹层里的职位列表文本。 */
async function readPositionItems(page: Page): Promise<string[]> {
  const items = await page.$$(SELECTORS.positionItem[0]);
  const names: string[] = [];
  for (const item of items) {
    const title = await item.$(SELECTORS.positionTitle[0]);
    const text = normalizeText(
      title ? await readHandleText(title) : await readHandleText(item),
    );
    if (text && !names.includes(text)) {
      names.push(text);
    }
  }
  return names;
}

/** 关闭职位选择弹层，不改变当前岗位。 */
async function closePositionPanel(page: Page): Promise<void> {
  await page.keyboard.press('Escape');
  await sleepRandom(POSITION_ACTION_GAP_MS);
}

/** 确保当前页面在推荐页，必要时导航过去。 */
export async function ensureOnEntryPage(page: Page): Promise<void> {
  if (page.url().includes('zhaopin.com')) {
    return;
  }
  await page.goto(ENTRY_URL, { waitUntil: 'domcontentloaded' });
  await sleepRandom(POSITION_SELECTED_SETTLE_MS);
}

/** 列出职位选择弹层里可选的岗位名称，读取后自动关闭弹层。 */
export async function runListPositions(keyword?: string): Promise<string> {
  const page = await ensurePageForPositions();
  const search = (keyword ?? '').trim();
  await openPositionPanel(page, search);
  const names = await readPositionItems(page);
  await closePositionPanel(page);

  if (names.length === 0) {
    return search
      ? `没找到匹配「${search}」的岗位。`
      : '职位列表为空。请先在智联招聘网页上发布或上线职位。';
  }

  const lines = [
    search ? `职位搜索结果（关键词：${search}）：共 ${names.length} 个。` : `职位列表：共 ${names.length} 个。`,
    '',
    ...names.map((name, index) => `  ${index + 1}. ${name}`),
  ];
  return lines.join('\n');
}

/**
 * 通过职位选择弹层切换到目标岗位。
 *
 * 智联每次直接走弹层选择，不读取页面上不稳定的当前岗位文字；
 * 与 goodhr 的既有策略一致，选中搜索结果第一条后确认弹层已关闭。
 */
export async function selectPosition(page: Page, keyword: string): Promise<string> {
  const query = normalizeText(keyword);
  if (!query) {
    throw new Error('未提供岗位名称，无法切换岗位。');
  }

  await openPositionPanel(page, query);

  const items = await page.$$(SELECTORS.positionItem[0]);
  if (items.length === 0) {
    await closePositionPanel(page);
    throw new Error(`职位搜索没有结果：${query}`);
  }

  const first: Handle = items[0]!;
  const title = await first.$(SELECTORS.positionTitle[0]);
  const firstName = normalizeText(title ? await readHandleText(title) : await readHandleText(first));

  // 点击标题区域而不是整行，避免点到行内的查看图标或空白处。
  await humanClick(page, title ?? first);
  await sleepRandom(POSITION_SELECTED_SETTLE_MS);

  const panel = await waitForFirstSelector(page, SELECTORS.positionPanel, 3000);
  if (panel) {
    throw new Error('已点击岗位，但职位选择弹层没有关闭，岗位可能没有切换成功。');
  }

  return firstName || query;
}

/** 取得可操作的主标签页，并确保在智联域名内。 */
async function ensurePageForPositions(): Promise<Page> {
  const page = await ensurePage();
  await ensureOnEntryPage(page);
  return page;
}
