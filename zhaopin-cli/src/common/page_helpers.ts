/**
 * 本文件提供页面定位与操作辅助。
 *
 * 操作原则：点击、滚动、输入全部使用 puppeteer 原生能力（真实鼠标与键盘事件），
 * 不通过注入脚本驱动页面；仅在读取文本与属性时做最小化的信息提取。
 */

import type { ElementHandle, Page } from 'puppeteer-core';
import { randomIntInclusive, sleep } from '../browser/timing.js';

/** 元素句柄的通用类型别名。 */
export type Handle = ElementHandle<Element>;

/** 归一化文本：合并空白并去掉首尾空格。 */
export function normalizeText(value: string | null | undefined): string {
  return (value ?? '').replace(/\s+/g, ' ').trim();
}

/** 在候选选择器列表里等待第一个出现的元素，超时返回 null。 */
export async function waitForFirstSelector(
  page: Page,
  selectors: readonly string[],
  timeoutMs = 5000,
): Promise<Handle | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const remaining = Math.max(deadline - Date.now(), 200);
    for (const selector of selectors) {
      try {
        const handle = await page.waitForSelector(selector, {
          timeout: Math.min(remaining, 800),
          visible: true,
        });
        if (handle) return handle;
      } catch {
        /* 当前选择器未命中，继续尝试下一个 */
      }
    }
    await sleep(randomIntInclusive(120, 260));
  }
  return null;
}

/** 读取元素自身的文本内容。 */
export async function readHandleText(handle: Handle | null): Promise<string> {
  if (!handle) return '';
  const text = await handle.evaluate((node) => node.textContent ?? '');
  return normalizeText(text);
}

/** 在容器内按选择器读取第一个子元素的文本，缺失时回退到容器自身文本。 */
export async function readChildText(
  container: Handle,
  selectors: readonly string[],
): Promise<string> {
  for (const selector of selectors) {
    const child = await container.$(selector);
    if (child) {
      const text = await readHandleText(child);
      if (text) return text;
    }
  }
  return '';
}

/** 读取页面内匹配选择器的全部元素文本，按出现顺序返回去重后的非空结果。 */
export async function readSelectorTexts(
  page: Page,
  selectors: readonly string[],
  options: { limit?: number; dedupe?: boolean } = {},
): Promise<string[]> {
  const { limit = 200, dedupe = true } = options;
  let raw: string[] = [];
  for (const selector of selectors) {
    raw = await page.$$eval(selector, (nodes) =>
      nodes.map((node) => node.textContent ?? ''),
    );
    if (raw.length > 0) break;
  }
  const texts = raw.map((value) => normalizeText(value)).filter(Boolean).slice(0, limit);
  return dedupe ? [...new Set(texts)] : texts;
}

/**
 * 用真实鼠标点击元素：先滚动到可视区，再把指针移到元素中心按下抬起。
 * 不走脚本点击，避免绕过页面自身的坐标与事件校验。
 */
export async function humanClick(page: Page, handle: Handle): Promise<void> {
  await handle.scrollIntoView().catch(() => undefined);
  const box = await handle.boundingBox();
  if (!box) {
    throw new Error('元素不在可视区内，无法点击。');
  }
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y, { steps: randomIntInclusive(6, 14) });
  await sleep(randomIntInclusive(60, 180));
  await page.mouse.down();
  await sleep(randomIntInclusive(40, 120));
  await page.mouse.up();
}

/** 用真实滚轮在指定容器上滚动一段距离。 */
export async function wheelScroll(page: Page, container: Handle, deltaY: number): Promise<void> {
  await container.scrollIntoView().catch(() => undefined);
  const box = await container.boundingBox();
  if (box) {
    await page.mouse.move(
      box.x + box.width / 2,
      box.y + Math.min(box.height / 2, 200),
      { steps: randomIntInclusive(4, 10) },
    );
  }
  await page.mouse.wheel({ deltaY });
}

/** 逐字符输入文本，字符之间保留随机间隔，贴近真人输入节奏。 */
export async function humanType(
  page: Page,
  handle: Handle,
  text: string,
): Promise<void> {
  await humanClick(page, handle);
  await sleep(randomIntInclusive(120, 320));
  for (const char of Array.from(text)) {
    await page.keyboard.type(char, { delay: 0 });
    await sleep(randomIntInclusive(38, 120));
  }
}
