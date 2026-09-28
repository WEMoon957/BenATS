# 招聘接入链路

> BOSS 直聘职位与简历导入、触达审核状态机、自动化引擎轮次和去重状态。
>
> 返回 [SOURCE_MAP.md](../SOURCE_MAP.md) 选择其他主题。

## 20. 招聘接入端到端数据流

```text
boss-cli（独立部署，子进程调用）
  ├─ positions ────────────── 在招职位列表
  ├─ jd <职位名> ──────────── 职位 JD（Markdown）
  ├─ recommend [关键词] ───── 职位推荐候选人
  ├─ list / list --unread ─── 已沟通候选人 / 对方主动发来的消息
  └─ download-resume <姓名> ─ 附件简历本地路径
        │
        ▼
AutomationEngine.run_once()（默认每 300 秒一轮，daemon 线程）
  1. sync_positions()          新职位 → JobRepository.create() + 写入 JD
  2. sync_candidates()         新候选人 → OutreachStore.create(greet) 草稿
  3. sync_inbound()            未读消息 → agree-resume 或 send+求简历 草稿
  4. sync_outreach_results()   已发送触达 → 回写候选人状态为 outreached
  5. sync_resumes()            已触达候选人 → 下载附件简历写入任务
  6. start_ready_jobs()        draft/waiting 且材料齐备的任务 → EvaluationEngine.start()
  7. sync_s_calls()            已完成的筛选任务 → S 级名单电话确认任务
        │
        ▼
OutreachStore（outreaches/<id>/outreach.json，pending → sent / failed / rejected）
  ├─ 界面审核：批准发送 / 否决 / 一键发送
  └─ execute_outreach() 把动作翻译成 boss 命令并执行
```

引擎只由 `main()` 启动路径拉起（`app.state.automation.start()`），`create_app()` 只装配不启动。

## 20.1 触达动作与状态机

动作 `OutreachAction`：

| 字段 | 说明 |
| --- | --- |
| `kind` | `greet` 打招呼、`send` 发消息、`action` 聊天页操作 |
| `target` | 候选人姓名 |
| `text` | 消息正文（`send`） |
| `request_resume` | `send` 时是否附带索要简历 |
| `job_keyword` | 关联职位名，用于回写候选人状态 |
| `command` | `action` 的具体命令：`agree-resume`、`request-attachment-resume`、`remark`、`not-fit` |
| `remark` | `remark` 命令的备注内容 |

草稿状态机为 `pending → sent / failed / rejected`，只有 `pending` 可批准或否决，其他状态由端点返回 409。`execute_outreach()` 失败时把 `BossCliError` 文本写入记录的 `error`，状态置 `failed`，不影响其他草稿，也不改变筛选或电话任务状态。

候选人状态（`AutomationStore.candidates`，键为 `<job_id>:<姓名>`）：

```text
outreach_pending → outreached    打招呼/回复草稿发送成功
agree_pending    → outreached    同意接收附件简历草稿发送成功
inbound_pending  → outreached    回复并索要简历草稿发送成功
resume_requested → outreached    求简历草稿发送成功，回到下载探测
outreached       → resume_downloaded   附件简历下载并写入任务成功
```

下载失败计数仅在候选人处于 `outreached` 时累加；达到 3 轮失败生成一次「求简历」草稿并置为 `resume_requested`。

## 20.2 去重与持久化状态

- `automation.json` 的 `positions`：职位名 → `{job_id, s_call_synced}`。职位名已存在时不重复建任务；`s_call_synced` 保证每个职位的 S 级电话任务只创建一次。
- `automation.json` 的 `candidates`：`<job_id>:<姓名>` → `{status, download_failures}`。候选人已有任意状态时不重复生成草稿。
- 附件简历写入任务时同步记录 SHA-256 哈希，与手工上传共用同一套判重字段。
- S 级名单从任务的 `评估结果.json` 读取，按结论原文枚举筛选；该文件是模型输出经守卫后的落盘结果，不读前端结果预览。

## 20.3 修改时必须同步检查

- 修改 `boss` 子命令、参数或输出格式：同步 `boss_cli.py` 的解析正则与 `outreach.py` 的命令翻译，二者与 boss-cli 的实际输出耦合。
- 修改触达动作字段：同步 `OutreachAction`、`OutreachInput`、`_new_record()`、`execute_outreach()`、`BossView` 的展示字段与 `tests/test_automation.py`。
- 修改候选人状态名：同步 `AutomationStore` 的状态判断、`sync_*` 各步的迁移条件与自动化验证。
- 修改轮询间隔或引擎启停方式：同步 `AutomationEngine` 构造参数、`main()` 启动路径、`/api/boss/automation/*` 端点与界面状态文案。
- 修改去重键：同步 `positions` / `candidates` 的键格式，并检查已持久化的 `automation.json` 兼容性。
- 新增对外发送动作时，必须保持「先生成草稿、HR 审核后才执行」的边界，不得直接发送。
