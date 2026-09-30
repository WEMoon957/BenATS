/** 本文件负责按姓名定位候选人卡片，供详情与打招呼复用。 */

import type { ElementHandle, Page } from 'puppeteer-core';
import type { Handle } from '../common/page_helpers.js';
import { readChildText } from '../common/page_helpers.js';
import { SELECTORS } from '../common/selectors.js';

/** 在推荐列表里按姓名查找候选人卡片；支持精确匹配与包含匹配。 */
export async function findCandidateCard(page: Page, name: string): Promise<Handle> {
  const target = name.trim();
  if (!target) {
    throw new Error('未提供候选人姓名。');
  }

  const cards = await page.$$(SELECTORS.candidateCard[0]);
  if (cards.length === 0) {
    throw new Error(
      '推荐列表里没有候选人卡片。请先执行 zhaopin recommend 打开推荐页并加载候选人。',
    );
  }

  let fallback: ElementHandle<Element> | null = null;
  for (const card of cards) {
    const cardName = await readChildText(card, SELECTORS.fieldName);
    if (!cardName) continue;
    if (cardName === target) {
      return card;
    }
    if (!fallback && cardName.includes(target)) {
      fallback = card;
    }
  }

  if (fallback) {
    return fallback;
  }

  const available = [];
  for (const card of cards) {
    const cardName = await readChildText(card, SELECTORS.fieldName);
    if (cardName) available.push(cardName);
  }
  throw new Error(
    `没有找到候选人「${target}」。当前列表里有：${available.slice(0, 20).join('、') || '（空）'}`,
  );
}
