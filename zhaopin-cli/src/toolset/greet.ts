/** 本文件负责对候选人执行打招呼。 */

import { GREET_SETTLE_MS, ensurePage, sleepRandom } from '../browser/index.js';
import { humanClick, readHandleText, type Handle } from '../common/page_helpers.js';
import {
  CONTINUE_BUTTON_TEXT,
  GREET_BUTTON_TEXT,
  GREET_DIALOG_TITLE,
  GREET_SEND_TEXT,
  SELECTORS,
} from '../common/selectors.js';
import { findCandidateCard } from './candidate_locator.js';
import { ensureOnEntryPage } from './positions.js';

/** 卡片上打招呼按钮的三种状态。 */
type GreetButtonState =
  | { kind: 'ready'; button: Handle }
  | { kind: 'already' }
  | { kind: 'missing' };

/**
 * 在卡片上找到文字为「打招呼」的按钮。
 *
 * 同一区域还有同款式的打电话按钮，因此必须逐个核对按钮文字。
 */
async function resolveGreetButton(card: Handle): Promise<GreetButtonState> {
  const buttons = await card.$$(SELECTORS.greetButton.join(', '));
  for (const button of buttons) {
    const text = await readHandleText(button);
    if (text.includes(GREET_BUTTON_TEXT)) {
      return { kind: 'ready', button };
    }
    if (text.includes(CONTINUE_BUTTON_TEXT)) {
      return { kind: 'already' };
    }
  }
  return { kind: 'missing' };
}

/** 首次打招呼会弹出招呼语选择框，出现时点一次发送。 */
async function confirmGreetingDialog(page: Awaited<ReturnType<typeof ensurePage>>): Promise<boolean> {
  const dialog = await page
    .waitForSelector(`::-p-text(${GREET_DIALOG_TITLE})`, { timeout: 2500 })
    .catch(() => null);
  if (!dialog) {
    return false;
  }

  const sendButton = await page
    .waitForSelector(`::-p-text(${GREET_SEND_TEXT})`, { timeout: 3000 })
    .catch(() => null);
  if (!sendButton) {
    throw new Error('弹出了招呼语选择框，但没有找到发送按钮，请手动在浏览器里确认。');
  }

  await humanClick(page, sendButton);
  await sleepRandom(GREET_SETTLE_MS);
  return true;
}

/** 对指定候选人执行打招呼，返回结果说明。 */
export async function runGreet(name: string): Promise<string> {
  const page = await ensurePage();
  await ensureOnEntryPage(page);

  const card = await findCandidateCard(page, name);
  const state = await resolveGreetButton(card);

  if (state.kind === 'already') {
    return `候选人「${name}」已经打过招呼了，不用重复操作。`;
  }
  if (state.kind === 'missing') {
    throw new Error(
      `候选人「${name}」的卡片上没有找到打招呼按钮，可能已经聊过或该平台限制了主动触达。`,
    );
  }

  await humanClick(page, state.button);
  await sleepRandom(GREET_SETTLE_MS);

  const confirmed = await confirmGreetingDialog(page);

  return confirmed
    ? `已对候选人「${name}」打招呼，并在招呼语弹框里点了发送。`
    : `已对候选人「${name}」打招呼。`;
}

/** 批量打招呼时两个人之间的等待区间，降低平台风控风险。 */
const PERSON_GAP_MS = { min: 1500, max: 3000 } as const;

/**
 * 对多位候选人依次打招呼，返回汇总文本。
 *
 * 单个人失败不会中断整批；文本末尾附成功与失败数量。
 */
export async function runGreetMany(
  names: string[],
): Promise<{ text: string; failed: number }> {
  const lines: string[] = [];
  let failed = 0;

  for (const [index, name] of names.entries()) {
    if (index > 0) {
      await sleepRandom(PERSON_GAP_MS);
    }
    try {
      lines.push(await runGreet(name));
    } catch (error) {
      failed += 1;
      const message = error instanceof Error ? error.message : String(error);
      lines.push(`候选人「${name}」处理失败：${message}`);
    }
  }

  lines.push(
    `批量打招呼结束：共 ${names.length} 人，成功 ${names.length - failed}，失败 ${failed}。`,
  );
  return { text: lines.join('\n'), failed };
}
