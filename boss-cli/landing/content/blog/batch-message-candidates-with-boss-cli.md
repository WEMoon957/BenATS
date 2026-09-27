---
title: 用 boss-cli 批量发送候选人消息
description: 通过 boss-cli 命令行批量向 Boss直聘候选人发送跟进消息，提升 HR 沟通效率，适合脚本与 Agent 集成。
date: 2026-05-22
---

单条消息手工发送尚可，当需要**对一批候选人统一跟进**时，网页端操作会成为瓶颈。boss-cli 把「打开会话 → 输入内容 → 发送」封装为可重复执行的命令。

## 前置条件

1. 已安装 boss-cli：`npm install -g @joohw/boss-cli`
2. 已完成 Boss直聘账号登录（CLI 登录流程）
3. 明确消息模板与目标候选人范围（避免误发）

## 工作流思路

1. **列出或筛选候选人** — 使用 CLI 提供的列表/筛选能力获取目标集合。
2. **构造消息内容** — 支持模板化文案；注意 Boss直聘平台规范，避免 spam。
3. **批量发送** — 通过命令参数或脚本循环调用发送能力。
4. **记录结果** — 将成功/失败写入日志，便于 HR 复盘。

具体命令以当前版本 README 为准，见 [GitHub 文档](https://github.com/joohw/boss-cli#readme)。

## 与 AI Agent 配合

boss-cli 适合作为 Agent 的「手」：

- Agent 负责理解招聘需求、生成个性化话术
- boss-cli 负责在 Boss直聘侧执行发送
- 人工可在关键节点审核后再跑批量任务

## 注意事项

- 遵守 Boss直聘用户协议与频率限制
- 批量操作前先用少量候选人试跑
- 敏感数据不要提交到公开仓库

## 相关阅读

- [boss-cli 入门](/blog/boss-cli-hr-automation-intro)
- [GitHub Issues](https://github.com/joohw/boss-cli/issues) — 反馈与功能建议
