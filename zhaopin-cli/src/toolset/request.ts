/** 本文件负责打开候选人聊天框，索要附件简历、电话或微信，并可追加一条消息。 */

import type { Page } from 'puppeteer-core';
import { ensurePage, sleepRandom } from '../browser/index.js';
import {
  humanClick,
  humanType,
  readHandleText,
  waitForFirstSelector,
  type Handle,
} from '../common/page_helpers.js';
import {
  CHAT_CLOSE_SELECTOR,
  CHAT_FOOTER_SELECTOR,
  CHAT_INPUT_SELECTOR,
  CHAT_OPEN_SELECTORS,
  CHAT_PHONE_CONFIRM_SELECTOR,
  CHAT_PHONE_SELECTOR,
  CHAT_RESUME_SCOPE_SELECTOR,
  CHAT_WECHAT_SELECTOR,
  CONTINUE_BUTTON_TEXT,
  GREET_BUTTON_TEXT,
  REQUEST_RESUME_TEXT,
} from '../common/selectors.js';
import { findCandidateCard } from './candidate_locator.js';
import { ensureOnEntryPage } from './positions.js';

/** 索要动作的类型。 */
export type RequestKind = 'resume' | 'phone' | 'wechat';

/** 点击「继续沟通」后等待聊天框渲染。 */
const CHAT_OPEN_SETTLE_MS = { min: 900, max: 1800 } as const;

/** 单个索要动作之间的等待区间。 */
const ACTION_GAP_MS = { min: 260, max: 620 } as const;

/** 打开候选人聊天框：点击卡片上的「继续沟通」按钮。 */
async function openChat(page: Page, card: Handle, name: string): Promise<void> {
  const buttons = await card.$$(CHAT_OPEN_SELECTORS.join(', '));
  let continueButton: Handle | null = null;
  let greetStillThere = false;

  for (const button of buttons) {
    const text = await readHandleText(button);
    if (text.includes(CONTINUE_BUTTON_TEXT)) {
      continueButton = button;
      break;
    }
    if (text.includes(GREET_BUTTON_TEXT)) {
      greetStillThere = true;
    }
  }

  if (!continueButton) {
    throw new Error(
      greetStillThere
        ? `候选人「${name}」还没打过招呼，请先执行 greet 再索要信息。`
        : `候选人「${name}」的卡片上没有找到「继续沟通」按钮，无法打开聊天框。`,
    );
  }

  await humanClick(page, continueButton);
  await sleepRandom(CHAT_OPEN_SETTLE_MS);

  const footer = await waitForFirstSelector(page, [CHAT_FOOTER_SELECTOR], 6000);
  if (!footer) {
    throw new Error(`点击「继续沟通」后聊天框没有打开，无法对「${name}」执行索要操作。`);
  }
}

/** 在聊天框底部范围内按精确文字点击按钮。 */
async function clickByChatText(page: Page, scopeSelector: string, text: string): Promise<void> {
  const scope = await page.$(scopeSelector);
  if (!scope) {
    throw new Error(`聊天框里没有找到「${text}」所在的操作区。`);
  }
  const candidates = await scope.$$('a, button, span, div');
  for (const candidate of candidates) {
    if ((await readHandleText(candidate)) === text) {
      await humanClick(page, candidate);
      return;
    }
  }
  throw new Error(`聊天框里没有找到文字为「${text}」的按钮。`);
}

/** 执行一次索要动作，返回结果说明。 */
async function performRequest(page: Page, kind: RequestKind): Promise<string> {
  if (kind === 'resume') {
    await clickByChatText(page, CHAT_RESUME_SCOPE_SELECTOR, REQUEST_RESUME_TEXT);
    return '已点击「要附件简历」';
  }

  if (kind === 'wechat') {
    const button = await waitForFirstSelector(page, [CHAT_WECHAT_SELECTOR], 5000);
    if (!button) {
      throw new Error('聊天框里没有找到索要微信的按钮。');
    }
    await humanClick(page, button);
    return '已索要微信';
  }

  const button = await waitForFirstSelector(page, [CHAT_PHONE_SELECTOR], 5000);
  if (!button) {
    throw new Error('聊天框里没有找到索要电话的按钮。');
  }
  await humanClick(page, button);
  await sleepRandom(ACTION_GAP_MS);

  // 索要电话会弹出一个方式选择层，出现时点「向对方索要」确认一次。
  const confirm = await waitForFirstSelector(page, [CHAT_PHONE_CONFIRM_SELECTOR], 1500);
  if (confirm) {
    await humanClick(page, confirm);
    return '已索要电话（含二次确认）';
  }
  return '已索要电话';
}

/** 关闭聊天框；关闭失败不影响已完成的索要动作。 */
async function closeChat(page: Page): Promise<void> {
  const closeButton = await page.$(CHAT_CLOSE_SELECTOR);
  if (!closeButton) return;
  await humanClick(page, closeButton).catch(() => undefined);
  await sleepRandom(ACTION_GAP_MS);
}

/**
 * 对指定候选人执行索要操作。
 *
 * kinds 为要执行的动作列表，按传入顺序依次执行；
 * message 非空时会在索要完成后于聊天框内追加一条消息并发送。
 */
export async function runRequest(
  name: string,
  kinds: RequestKind[],
  message = '',
): Promise<string> {
  const page = await ensurePage();
  await ensureOnEntryPage(page);

  const card = await findCandidateCard(page, name);
  await openChat(page, card, name);

  const done: string[] = [];
  for (const kind of kinds) {
    done.push(await performRequest(page, kind));
    await sleepRandom(ACTION_GAP_MS);
  }

  if (message.trim()) {
    const input = await waitForFirstSelector(page, [CHAT_INPUT_SELECTOR], 5000);
    if (!input) {
      throw new Error('聊天框里没有找到输入框，补充消息没有发送。');
    }
    await humanType(page, input, message.trim());
    await sleepRandom(ACTION_GAP_MS);
    await page.keyboard.press('Enter');
    done.push('已发送补充消息');
  }

  await closeChat(page);

  return [`候选人「${name}」处理完成：`, ...done.map((item) => `  - ${item}`)].join('\n');
}
