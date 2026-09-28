# API 与运行时约束

> 前后端 API、轮询、并发、持久化和取消隔离。
>
> 返回 [SOURCE_MAP.md](../SOURCE_MAP.md) 选择其他主题。

## 11. 前后端 API 契约

### 11.1 通用约束

- 所有 `/api/` 请求必须带 `X-App-Token`。
- JSON 请求使用 `application/json`。
- 错误响应优先返回 `{"detail": "可展示说明"}`。
- 文件下载使用非 JSON 响应；中文文件名优先采用 RFC 5987 `filename*=utf-8''...`。
- 前端 `api()` 会根据响应 Content-Type 决定返回 JSON 还是原始 `Response`。
- 唯一 API client：`frontend/src/api/client.ts`；行为由契约测试 `api-client.test.ts` 锁定。
- 设置相关端点：`PUT /api/settings`（保存）、`POST /api/settings/test`（模型连接测试）、`POST /api/settings/feishu-test`（飞书测试消息）。飞书测试成功返回 `{"ok": true}`；Webhook 为空时返回本地校验说明，网络、HTTP 或飞书业务错误返回脱敏后的类别、状态或业务码及尝试次数，均为 HTTP 400 `detail`，不回传飞书原始 `msg`。

### 11.2 Job 前端依赖字段

```text
id, title, status, stage, progress, completed, total,
results, errors, elapsed_seconds,
evaluation_started_at, updated_at, archived_at
```

`reviewed` 是后端任务摘要和持久化字段，前端当前不读取。

候选人结果依赖字段：

```text
candidate_name, source_file, conclusion, grade,
one_line, blockers, next_action
```

`grade` 是前端唯一用于过滤、计数、对比勾选与徽章配色的机器值，取值 `S` / `A` / `B` / `C`。它由 `pipeline.result_preview()` 从结论派生；`main.public_job()` 对缺少该字段的历史结果在 API 出口补齐，因此持久化的 `job.json` 不需要迁移。

`conclusion` 是后端业务枚举，前端只作为原文保留，不参与任何界面判断：

```text
S电话沟通
A优先约面
B电话确认
C不推进
```

后端模型同时接受单字母缩写 `"S"`、`"A"`、`"B"`、`"C"`，在校验时自动展开为完整中文标签（`S电话沟通 / A优先约面 / B电话确认 / C不推进`）。`conclusion_grade()` 取结论首字母并限定在 `S/A/B/C` 内，其余值按 C 处理。若改为代码枚举或英文值，必须同步后端模型、证据守卫、排序、Excel、前端、对比逻辑和验证。

后端对可选字段显式返回 `null` 时归一为语义默认：候选人元信息字段与评估层级回退默认或空串；判定内容字段（`conclusion`、`one_line`、`next_action`）不套用语义默认，为空依旧校验失败。

### 11.3 Call 前端依赖字段

任务：

```text
id, title, title_mode, job_title, job_id, soft_skill_focus, soft_skill_dimensions,
status, stage, updated_at,
archived_at, errors, items, roster
```

条目：

```text
id, audio_file, candidate_name, stage, status,
progress, error, summary
```

摘要（服务端持久化并由前端读取）：

```text
candidate_name, call_date, narrative,
remark_sections[].{title,bullets[]}, soft_skill_summary_title,
soft_skill_summary[],
qa_records[].{question,answer},
fields[].{key,label,value,status,note},
facts[].{content,speaker,ref,start_time,end_time},
doubts, transcript
```

- `title_mode` 使用 `auto|custom`，区分可按界面语言显示的日期标题与保持原文的用户标题。
- `fields[].key` 是人工编辑的稳定身份；`fields[].status` 是模型根据通话语义给出的确定性。编辑保存时保留 `fields[].note`。
- 前端直接渲染 `narrative`、字段、事实、疑点和转写；后端把 `remark_sections`、软性素质评价与可选 `qa_records` 组合进 `narrative`。
- `facts[].ref` 只用于尝试定位录音时间，不参与正文、招聘判断或字段状态裁决。
- Call 结构没有 Job 专用的 `completed` / `total` 字段。

### 11.4 前端轮询

- Job：每 1200 ms 请求一次 `/api/jobs/<id>`。
- Call：每 2500 ms 请求一次 `/api/calls/<id>`。
- 网络错误不会停止轮询。
- 切换当前任务后，响应必须再次检查 ID，防止旧请求污染新视图。

修改详情 API 的负载大小或字段时，要考虑高频轮询成本。`GET /api/bootstrap` 和任务列表端点应继续返回摘要，不要无条件携带完整结果。

### 11.5 招聘接入端点

```text
GET  /api/boss/positions                         职位列表（boss-cli `positions`）
POST /api/boss/import                            拉取岗位 JD 与附件简历并新建任务
POST /api/boss/outreaches                        新建触达草稿
GET  /api/boss/outreaches                        列出未归档触达记录
POST /api/boss/outreaches/{id}/approve           批准并发送（pending → sent / failed）
POST /api/boss/outreaches/{id}/reject            否决（pending → rejected）
POST /api/boss/outreaches/approve-all            按顺序发送全部待审核触达
GET  /api/boss/automation/status                 引擎运行状态
POST /api/boss/automation/run                    立即执行一轮
POST /api/boss/automation/start / stop           启动 / 停止引擎
```

- 触达记录状态机为 `pending → approved/rejected → sent/failed`，只有 `pending` 可批准或否决，其余状态返回 409。
- `BossCliError` 统一映射为 503，详情携带 boss-cli 的原始错误文本；发送失败写入记录的 `error` 字段，不改变筛选或电话任务状态。
- 除 `/api/boss/positions` 外的端点都通过 `run_in_threadpool` 执行，避免进程调用阻塞事件循环。
- `/api/boss/import` 与前端暂无入口，仅后端提供；界面当前只消费引擎状态与触达审核两个端点。

## 12. 并发与持久化交叉影响

### 12.1 并发层级

```text
FastAPI 异步请求
  ├─ 每个岗位任务上传：asyncio.Lock 串行
  ├─ EvaluationEngine：最多 2 个岗位任务并行
  │    └─ 每个岗位内部：1–12 个候选人线程并行
  ├─ CallProcessor：最多 2 个电话任务并行
  │    └─ 每个电话任务内部：最多 5 条录音并发处理
  └─ JsonStore：RLock 保护同进程 JSON 读写
```

电话条目并发上限由 `phone_screening.py` 的 `ITEM_CONCURRENCY = 5` 定义。

### 12.2 不可破坏的并发保证

- `CallRepository.update_item()` 必须在锁内重新读取最新任务，只更新目标条目，避免并发覆盖其他条目。
- 同任务简历上传锁保护 `resume_files` 和 `resume_hashes` 的读改写。
- `JsonStore` 通过临时文件和 `os.replace` 原子写入 `job.json` / `record.json`。
- 简历候选人检查点和最终 `评估结果.json` / `解析清单.json` 均由 `atomic_write_json()` 原子写入；Excel 也通过临时文件替换。
- 筛选标准、解析文本、电话转写和电话摘要使用直接文件写入，不具备同样的进程中断原子性；调整保存顺序时必须单独检查恢复行为。
- 候选人失败隔离：单份失败不能丢失其他结果。
- 任务级取消隔离：取消任务 A 不能中止任务 B 的模型客户端。
- 对比缓存必须包含任务 ID 和结果文件哈希，避免跨任务污染或结果变化后返回旧排序。

涉及线程池、锁、保存顺序、future 或取消事件的修改，必须运行并发、取消、上传竞争、断点恢复和对比缓存相关验证。
