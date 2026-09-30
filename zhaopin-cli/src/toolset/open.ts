/** 本文件负责打开候选人详情并读取正文文本。 */

import { DETAIL_SETTLE_MS, ensurePage, sleepRandom } from '../browser/index.js';
import { humanClick, readHandleText, waitForFirstSelector } from '../common/page_helpers.js';
import { SELECTORS } from '../common/selectors.js';
import { findCandidateCard } from './candidate_locator.js';
import { ensureOnEntryPage } from './positions.js';

/** 关闭详情面板时的等待区间。 */
const DETAIL_CLOSE_GAP_MS = { min: 300, max: 700 } as const;

/**
 * 打开指定候选人的详情面板，读取正文后关闭，返回正文文本。
 *
 * 详情入口优先点姓名区域；没有打开时改点卡片中间的经历区域，
 * 两处都远离右侧的打电话与打招呼按钮，避免误触。
 */
export async function runOpenDetail(name: string): Promise<string> {
  const page = await ensurePage();
  await ensureOnEntryPage(page);

  const card = await findCandidateCard(page, name);

  const primaryTarget = await card.$(SELECTORS.detailOpen[0]);
  const fallbackTarget = await card.$(SELECTORS.detailOpenFallback[0]);
  const openTarget = primaryTarget ?? fallbackTarget;
  if (!openTarget) {
    throw new Error(`候选人「${name}」的卡片上没有找到可点击的详情入口。`);
  }

  await humanClick(page, openTarget);
  await sleepRandom(DETAIL_SETTLE_MS);

  let body = await waitForFirstSelector(page, SELECTORS.detailBody, 6000);

  // 姓名区域点击无效时，改用经历区域重试一次。
  if (!body && primaryTarget && fallbackTarget) {
    await humanClick(page, fallbackTarget);
    await sleepRandom(DETAIL_SETTLE_MS);
    body = await waitForFirstSelector(page, SELECTORS.detailBody, 6000);
  }

  if (!body) {
    throw new Error(
      `点击之后没有打开「${name}」的详情。请确认当前停留在推荐列表页，且列表已经加载出来。`,
    );
  }

  const text = await readHandleText(body);
  await page.keyboard.press('Escape');
  await sleepRandom(DETAIL_CLOSE_GAP_MS);

  if (!text) {
    throw new Error(`已经打开「${name}」的详情，但没有读到正文内容。`);
  }
  return text;
}
