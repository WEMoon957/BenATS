/** 本文件负责提供随机延时与人类化等待，降低机械操作特征。 */

/** 等待指定毫秒；可传入 AbortSignal 提前结束。 */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('Aborted'));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new Error('Aborted'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** 返回闭区间 [min, max] 内的随机整数。 */
export function randomIntInclusive(min: number, max: number): number {
  if (max <= min) return min;
  return min + Math.floor(Math.random() * (max - min + 1));
}

/** 在区间内随机等待，用于动作之间的自然停顿。 */
export function sleepRandom(
  range: { min: number; max: number },
  signal?: AbortSignal,
): Promise<void> {
  return sleep(randomIntInclusive(range.min, range.max), signal);
}

/** 职位选择弹层的动作间隔：点入口、输入关键词、点结果之间的停顿。 */
export const POSITION_ACTION_GAP_MS = { min: 320, max: 820 } as const;

/** 打开职位弹层后等待列表渲染。 */
export const POSITION_PANEL_SETTLE_MS = { min: 520, max: 1100 } as const;

/** 输入搜索关键词后等待前端过滤。 */
export const POSITION_SEARCH_SETTLE_MS = { min: 700, max: 1500 } as const;

/** 选中岗位后等待推荐列表刷新。 */
export const POSITION_SELECTED_SETTLE_MS = { min: 800, max: 1800 } as const;

/** 每轮滚轮加载之间的间隔。 */
export const LIST_SCROLL_GAP_MS = { min: 260, max: 700 } as const;

/** 打开候选人详情后等待正文渲染。 */
export const DETAIL_SETTLE_MS = { min: 900, max: 2200 } as const;

/** 点击打招呼后等待按钮状态变化与招呼语弹框出现。 */
export const GREET_SETTLE_MS = { min: 700, max: 1600 } as const;
