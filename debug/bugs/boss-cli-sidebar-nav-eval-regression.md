# boss-cli 侧边栏导航失效（puppeteer 字符串函数表达式不注入参数）

## 现象

`boss positions` 稳定失败：`通过侧边栏“职位管理”进入职位页失败，请确认已登录并可访问 /web/chat/job/list`。
登录态、浏览器会话、推荐/聊天列表均正常。

## 根因

puppeteer-core 24.x 对「字符串形式的函数表达式 + args 参数注入」不再生效：

- `page.evaluate("(({ a }) => a)", arg)` 返回函数对象本身（未调用，arg 未注入）；
- `page.waitForFunction("((p) => cond)", opts, arg)` 的 predicate 变成 truthy 函数对象，7ms 内“通过”。

因此 [boss_sidebar_nav.ts](../../boss-cli/src/common/boss_sidebar_nav.ts) 的点击从未执行、
URL 等待形同虚设。实测 puppeteer-core 24.29.1 与 24.43.1 行为一致，与版本无关。

受影响的三处（均为「字符串函数表达式 + 第二参数注入」）：

- `src/common/boss_sidebar_nav.ts` — 侧边栏点击与 URL 等待（positions / jd 等命令的公共路径）
- `src/toolset/action.ts` — 备注填写后的回读校验
- `src/common/c_resume_capture.ts` — c-resume iframe 滚动（参数未传也未执行）

仓库内其余字符串脚本均为 IIFE + 内嵌参数形式（`})+${JSON.stringify(x)})` 风格），不受影响。

## 修复

把三处改为「IIFE + 参数内嵌 JSON」风格：参数经 `JSON.stringify` 写入脚本字符串，
evaluate / waitForFunction 不再依赖外部注入。

## 验证

- `boss positions` → 读取 20 个职位（页面统计共 35），状态统计与明细正常。
- `boss jd 快餐店店员` → JD 完整抓取并落盘缓存。
- Talent Hub `GET /api/boss/positions` 端到端 → 20 个职位 JSON 正常返回。

## 备注

- 运行侧修复的部署方式：仓库 `boss-cli/` 构建产物整体覆盖全局安装包 `dist/`
  （npm 发布版 0.6.6 的 cli 路由不含 `download-resume`，仓库 vendored 版含）。
- `boss download-resume 范彩虹` 当前报「附件卡片无 `a[download]` 入口」：
  dump 显示卡片仅提供「点击预览附件简历」，属平台侧对新附件简历交互的差异，非解析缺陷。
