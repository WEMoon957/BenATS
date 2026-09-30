/** 本文件负责解析命令行参数并分发到各命令实现。 */

import { runGreet } from '../toolset/greet.js';
import { runHome } from '../toolset/home.js';
import { runLogin } from '../toolset/login.js';
import { runOpenDetail } from '../toolset/open.js';
import { runListPositions } from '../toolset/positions.js';
import { runRecommend } from '../toolset/recommend.js';
import { runRequest, type RequestKind } from '../toolset/request.js';

/** 返回用法说明文本。 */
function usageText(): string {
  return [
    '智联招聘自动化 CLI',
    '',
    '用法：',
    '  zhaopin login                   打开登录页，由你手动完成登录',
    '  zhaopin home                    直接跳到企业端（候选人推荐页）',
    '  zhaopin positions [关键词]       列出职位选择弹层里的可选岗位',
    '  zhaopin recommend [岗位关键词]    读取推荐候选人，可先切换到指定岗位',
    '  zhaopin open <姓名>              打开候选人详情并输出正文',
    '  zhaopin greet <姓名>             对候选人打招呼',
    '  zhaopin request <姓名> [动作...]  打开聊天框索要信息，动作可取 resume / phone / wechat，可组合，默认 resume',
    '',
    '首次使用请先执行 zhaopin login 完成登录，登录态会保存在本机。',
  ].join('\n');
}

/** 解析并执行一条命令。 */
export async function runCli(argv: string[]): Promise<void> {
  const [command, ...rest] = argv;
  const argument = rest.join(' ').trim();

  switch (command) {
    case 'login':
      console.log(await runLogin());
      return;

    case 'home':
      console.log(await runHome());
      return;

    case 'positions':
      console.log(await runListPositions(argument || undefined));
      return;

    case 'recommend':
      console.log(await runRecommend(argument || undefined));
      return;

    case 'open':
      if (!argument) {
        throw new Error('请提供候选人姓名，例如：zhaopin open 张三');
      }
      console.log(await runOpenDetail(argument));
      return;

    case 'greet':
      if (!argument) {
        throw new Error('请提供候选人姓名，例如：zhaopin greet 张三');
      }
      console.log(await runGreet(argument));
      return;

    case 'request': {
      const [name, ...kindArgs] = rest;
      if (!name) {
        throw new Error('请提供候选人姓名，例如：zhaopin request 张三 resume phone');
      }
      const kinds = kindArgs.filter(
        (item): item is RequestKind =>
          item === 'resume' || item === 'phone' || item === 'wechat',
      );
      console.log(await runRequest(name, kinds.length > 0 ? kinds : ['resume']));
      return;
    }

    case undefined:
    case '':
    case 'help':
    case '-h':
    case '--help':
      console.log(usageText());
      return;

    default:
      console.log(`未知命令：${command}`);
      console.log();
      console.log(usageText());
      process.exitCode = 1;
  }
}
