/** 本文件负责解析命令行参数并分发到各命令实现。 */

import { runDownloadAllResumes, runDownloadResume } from '../toolset/download-resume.js';
import { runGreet, runGreetMany } from '../toolset/greet.js';
import { runHome } from '../toolset/home.js';
import { runLogin } from '../toolset/login.js';
import { runOpenDetail } from '../toolset/open.js';
import { runListPositions } from '../toolset/positions.js';
import { runRecommend } from '../toolset/recommend.js';
import { runRequest, runRequestMany, type RequestKind } from '../toolset/request.js';

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
    '  zhaopin greet <姓名> [姓名...]    对候选人打招呼，支持一次传多人批量执行',
    '  zhaopin request <姓名> [姓名...] [动作...]  打开聊天框索要信息并支持批量，',
    '                                  动作可取 resume / phone / wechat，可组合，默认 resume',
    '  zhaopin download <姓名> [姓名...]  下载指定候选人发来的附件简历，支持批量',
    '  zhaopin download-all            扫描全部会话，把对方发来的附件简历统一下载到本地',
    '',
    '  姓名之间用空格、逗号或顿号分隔，例如：',
    '  zhaopin greet 张三 李四 王五',
    '  zhaopin request 张三、李四 resume',
    '',
    '  批量执行时一个人失败不会中断整批，结尾会汇总成功与失败数量；',
    '  有失败时进程以非零状态退出。建议单批不超过 5～10 人，降低平台风控风险。',
    '',
    '首次使用请先执行 zhaopin login 完成登录，登录态会保存在本机。',
  ].join('\n');
}

/** 把一段参数按空格、逗号或顿号拆成姓名列表。 */
function parseNames(argument: string): string[] {
  return argument
    .split(/[、，,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 打印批量结果；有人失败时把进程退出码置为 1。 */
function reportBatch(result: { text: string; failed: number }): void {
  console.log(result.text);
  if (result.failed > 0) {
    process.exitCode = 1;
  }
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

    case 'greet': {
      const names = parseNames(argument);
      if (names.length === 0) {
        throw new Error('请提供候选人姓名，例如：zhaopin greet 张三 李四');
      }
      if (names.length === 1) {
        console.log(await runGreet(names[0]));
        return;
      }
      reportBatch(await runGreetMany(names));
      return;
    }

    case 'request': {
      const kindSet: ReadonlySet<string> = new Set(['resume', 'phone', 'wechat']);
      const names: string[] = [];
      const kinds: string[] = [];
      for (const token of rest) {
        if (kindSet.has(token)) {
          kinds.push(token);
        } else {
          names.push(...parseNames(token));
        }
      }
      if (names.length === 0) {
        throw new Error(
          '请提供候选人姓名，例如：zhaopin request 张三 李四 resume',
        );
      }
      const resolvedKinds = (kinds.length > 0 ? kinds : ['resume']) as RequestKind[];
      if (names.length === 1) {
        console.log(await runRequest(names[0], resolvedKinds));
        return;
      }
      reportBatch(await runRequestMany(names, resolvedKinds));
      return;
    }

    case 'download': {
      const names = parseNames(argument);
      if (names.length === 0) {
        throw new Error('请提供候选人姓名，例如：zhaopin download 张三 李四');
      }
      reportBatch(await runDownloadResume(names));
      return;
    }

    case 'download-all':
      reportBatch(await runDownloadAllResumes());
      return;

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
