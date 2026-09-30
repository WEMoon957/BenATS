/** 本文件负责读取智联推荐页的候选人列表，并支持真实滚轮加载更多。 */

import type { Page } from 'puppeteer-core';
import {
  LIST_SCROLL_GAP_MS,
  POSITION_SELECTED_SETTLE_MS,
  ensurePage,
  sleepRandom,
} from '../browser/index.js';
import {
  readChildText,
  readHandleText,
  waitForFirstSelector,
  wheelScroll,
} from '../common/page_helpers.js';
import { GREET_BUTTON_TEXT, MAX_ITEMS, SCROLL_DISTANCE, SELECTORS } from '../common/selectors.js';
import { ensureOnEntryPage, selectPosition } from './positions.js';

/** 一位候选人在列表卡片上的可见信息。 */
export type RecommendCandidate = {
  /** 候选人姓名。 */
  name: string;
  /** 基本信息（年龄、经验、学历等）。 */
  basicInfo: string;
  /** 工作与教育经历摘要。 */
  education: string;
  /** 卡片描述区域文本。 */
  description: string;
  /** 卡片上是否还有可用的「打招呼」按钮。 */
  canGreet: boolean;
};

/** 读取当前已经渲染出来的候选人卡片，按姓名去重。 */
async function readVisibleCandidates(page: Page): Promise<RecommendCandidate[]> {
  const cards = await page.$$(SELECTORS.candidateCard[0]);
  const candidates: RecommendCandidate[] = [];

  for (const card of cards) {
    const name = await readChildText(card, SELECTORS.fieldName);
    if (!name || candidates.some((item) => item.name === name)) {
      continue;
    }

    const greetButton = await card.$(SELECTORS.greetButton.join(', '));
    const greetText = greetButton ? await readHandleText(greetButton) : '';

    candidates.push({
      name,
      basicInfo: await readChildText(card, SELECTORS.fieldBasicInfo),
      education: await readChildText(card, SELECTORS.fieldEducation),
      description: await readChildText(card, SELECTORS.fieldDescription),
      canGreet: greetText.includes(GREET_BUTTON_TEXT),
    });
  }

  return candidates;
}

/**
 * 通过真实滚轮逐步加载候选人，直到达到数量上限或列表不再增长。
 *
 * 智联推荐页是无限滚动列表，每滚动一次会追加新卡片；
 * 连续两轮数量没有变化时判定已经到底，避免无意义地一直滚下去。
 */
async function loadCandidates(page: Page, limit: number): Promise<RecommendCandidate[]> {
  let collected = await readVisibleCandidates(page);
  let stagnantRounds = 0;
  const maxRounds = Math.ceil(limit / 5) + 5;

  for (let round = 0; round < maxRounds && collected.length < limit; round += 1) {
    const container = await waitForFirstSelector(page, SELECTORS.candidateList, 3000);
    if (!container) break;

    await wheelScroll(page, container, SCROLL_DISTANCE);
    await sleepRandom(LIST_SCROLL_GAP_MS);

    const next = await readVisibleCandidates(page);
    if (next.length <= collected.length) {
      stagnantRounds += 1;
      if (stagnantRounds >= 2) break;
    } else {
      stagnantRounds = 0;
    }
    collected = next;
  }

  return collected.slice(0, limit);
}

/** 把候选人列表渲染成便于阅读与解析的纯文本。 */
export function renderCandidates(candidates: RecommendCandidate[], positionName: string): string {
  const header = positionName ? `当前岗位：${positionName}` : '当前岗位：未切换';
  if (candidates.length === 0) {
    return [header, '', '推荐列表为空。请确认已完成登录，且当前岗位下有推荐候选人。'].join('\n');
  }

  const lines = [header, '', `推荐候选人：共 ${candidates.length} 人。`, ''];
  candidates.forEach((candidate, index) => {
    const fields = [
      candidate.basicInfo ? `信息:${candidate.basicInfo}` : '',
      candidate.canGreet ? '可打招呼' : '已打过招呼',
    ]
      .filter(Boolean)
      .join('｜');
    lines.push(`  ${index + 1}. ${candidate.name}｜${fields}`);
    const detail = candidate.education || candidate.description;
    if (detail) {
      lines.push(`     经历: ${detail}`);
    }
  });
  return lines.join('\n');
}

/**
 * 读取推荐候选人列表。
 *
 * 传入 jobKeyword 时会先通过职位弹层切换到该岗位，再读取候选人。
 */
export async function runRecommend(jobKeyword?: string, limit = MAX_ITEMS): Promise<string> {
  const page = await ensurePage();
  await ensureOnEntryPage(page);

  let positionName = '';
  const keyword = (jobKeyword ?? '').trim();
  if (keyword) {
    positionName = await selectPosition(page, keyword);
    await sleepRandom(POSITION_SELECTED_SETTLE_MS);
  }

  const candidates = await loadCandidates(page, Math.min(limit, MAX_ITEMS));
  return renderCandidates(candidates, positionName);
}
