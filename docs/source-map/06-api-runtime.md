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
POST /api/boss/outreaches/approve-all            发送当前目标岗位的全部待审核触达
GET  /api/boss/target-job                        目标岗位选择器数据（读本地缓存）
POST /api/boss/target-job                        设置 BOSS 目标岗位
GET  /api/zhaopin/target-job                     智联目标岗位选择器数据（读本地缓存）
POST /api/zhaopin/target-job                     设置智联目标岗位
GET  /api/boss/automation/status                 引擎运行状态
POST /api/boss/automation/run                    立即执行一轮
POST /api/boss/automation/start / stop           启动 / 停止引擎
```

- 触达记录状态机为 `pending → sent / failed / rejected`，只有 `pending` 可批准或否决，其余状态返回 409。
- `GET /api/boss/automation/status` 返回 `running`（轮询线程是否存活）与 `stopping`（当前轮次是否正在收尾）。
- `stop` 会同时置位取消标记：当前轮次在**步骤边界**处收尾，不再启动新步骤。每个步骤都会驱动本机浏览器，只在方法内部的循环里检查停止会让停止后的后续步骤继续操作浏览器。当前正在执行的 CLI 调用会跑完（单次上限 180 秒）。
- `start` 与「立即运行一轮」（`POST /api/boss/automation/run`）都会清除取消标记，因此引擎已停止时「立即运行一轮」仍能完整执行一轮。
- `approve-all` 只发送当前目标岗位（`boss_target_job`）的待审核触达，其它岗位的草稿置为 `skipped`；返回体包含 `sent` / `failed` / `skipped` 与 `target_job`。
- `BossCliError` 与 `ZhaopinCliError` 统一映射为 503，详情携带 CLI 的原始错误文本；发送失败写入记录的 `error` 字段，不改变筛选或电话任务状态。
- 目标岗位选择器只读本地缓存，不调用 CLI，避免浏览器忙时下拉框取不到值。
- 除 `/api/boss/positions` 外的端点都通过 `run_in_threadpool` 执行，避免进程调用阻塞事件循环。
- `/api/boss/import` 与前端暂无入口，仅后端提供；界面当前只消费引擎状态与触达审核两个端点。

### 11.6 招聘作业（Plan）端点

```text
GET  /api/recruitment/plans                      列出招聘作业
POST /api/recruitment/plans                      创建作业草稿（job_keyword、mode）
GET  /api/recruitment/plans/{id}/checks          执行前检查项（BOSS 接入 / 岗位 / 模型配置）
POST /api/recruitment/plans/{id}/start           检查通过后置 running 并触发引擎一轮
POST /api/recruitment/plans/{id}/stop            停止作业
```

- 作业状态机为 `draft → running → stopped`；`start` 只在执行前检查全部通过后推进状态，阻塞时返回 409 与首个未通过项。
- 启动后复用 `AutomationEngine` 的被动咨询流程，不直接调用 boss-cli 发送。

### 11.7 招聘候选人与考勤端点

招聘候选人（`app/recruitment/routes.py`）与评分标准（`app/main.py`），与其他 `/api/` 一样只校验 `X-App-Token`：

```text
GET    /api/recruitment/stages                       阶段枚举与中文标签
GET    /api/recruitment/rubrics                      已保存的评分标准
GET    /api/recruitment/candidates                   候选人列表（可按阶段筛选）
POST   /api/recruitment/candidates                   新建候选人
PATCH  /api/recruitment/candidates/{id}              修改候选人（阶段、备注等）
DELETE /api/recruitment/candidates/{id}              删除候选人
POST   /api/recruitment/candidates/greet             对候选人打招呼
POST   /api/recruitment/candidates/score             批量预评分
POST   /api/recruitment/candidates/{id}/call-score   写入电话确认评分
POST   /api/rubric/generate                          按文本生成评分标准
POST   /api/rubric/generate-by-job                   按岗位任务生成评分标准
POST   /api/rubric/score                             按评分标准打分
POST   /api/rubric/parse                             解析上传的评分标准文件
POST   /api/rubric/export                            导出评分标准
```

人事中台（`app/hr.py`）聚合员工、考勤与招聘摘要，与其他 `/api/` 一样只校验 `X-App-Token`：

```text
GET    /api/hr/dashboard                             员工、考勤与招聘三类摘要
POST   /api/hr/feishu-employees/sync                 按飞书通讯录刷新员工档案
POST   /api/hr/feishu-sync                           立即触发一轮飞书考勤打卡同步
```

- `dashboard` 一次请求内分别读取考勤库与招聘库，不做任何写入：员工总数与在职数、部门分布、最近一个已完成批次的考勤汇总、候选人总数与阶段分布、筛选任务数，以及飞书凭证与员工同步状态。
- 考勤部分取 `import_batch` 中按 `year`、`month`、`created_at` 排序最新的 `completed` 批次；没有该批次时 `latest_period` 为 `null`，出勤率与计数为 0。其中的 `attendance.feishu` 另带同步开关、引擎运行状态、最近错误与最新一个 `feishu-sync` 批次。
- 员工同步复用「考勤管理」保存的飞书应用凭证，需要逐项开通字段权限且通讯录权限范围为全部成员：`contact:user.base:readonly`（姓名）、`contact:user.employee:readonly`（工号、职务、入职时间、在职状态）、`contact:user.department:readonly`（所属部门）、`contact:department.base:readonly`（部门名称），手机号另需 `contact:user.phone:readonly`。飞书已不再提供 `contact:contact:readonly` 这类宽泛权限，`contact:contact.base:readonly` 只决定接口能否调用：只开它时字段全空，成员会全部被跳过（此时直接报错，不记空摘要）。查询根部门下的子部门要求全员范围，否则飞书返回无部门权限，报错会附带 `contact/v3/scopes` 读到的实际授权范围（部门数与用户数）。同步在 `run_in_threadpool` 中执行，凭证未配置返回 400，飞书侧拒绝返回 502 与可执行提示。
- 通讯录两个接口的形状：`GET /contact/v3/departments/{department_id}/children` 用**路径参数**传部门 ID（根部门为 `0`），`GET /contact/v3/users/find_by_department` 用**查询参数**传 `department_id`；两者都以 `department_id_type=open_department_id` 对齐，分页读 `items`、`has_more`、`page_token`。
- 唯一键优先取工号，飞书没填工号时退回飞书用户 ID（`user_id`，即考勤接口的 `employee_id`），最后才是 `open_id`；摘要用 `fallback_user_id` 记下没用工号建档的人数。更新时按新键、飞书用户 ID、`open_id` 依次找回原档案，兜底键变化不会产生重复人员。读不到姓名的成员跳过并计入 `skipped`，全部读不到姓名时报错指向字段权限。只按键更新或新增，不删除本地已有员工。
- 打卡同步先按 `employee_type=employee_no` 查询，再把飞书返回的 `invalid_user_ids` 按 `employee_type=employee_id` 查一次后合并：两种键混存也能取到打卡。所选类型下整批都无效时飞书返回业务码 `1220001`，按「本类型无结果」处理并交给另一种类型兜底；**全部标识在两种类型下都不被认可且一条打卡都没取到时**直接报错、不写批次，避免把「标识无效」记成「当月没人打卡」的全零批次；个别标识无效时其余成员照常同步。
- 飞书只授予打卡数据权限时无法枚举员工：`attendance/v1/user_tasks/query` 必须显式传入工号；考勤组接口（`groups`、`groups/{group_id}/list_user`）另需 `attendance:rule:readonly`，且 `list_user` 只返回工号与部门 ID、不含姓名。因此员工档案只能来自通讯录权限或本地手工维护，人事中台的员工同步依赖「考勤管理 → 设置」里的同一套飞书应用凭证再加通讯录权限。
- 在职判定读飞书 `status`：`is_exited`、`is_resigned`、`is_unjoin` 都记为非在职（`employment_status` 取 `left`），`is_frozen` 仍算在职员工；飞书通讯录不区分试用期与已转正，在职成员保留本地既有的在职状态。
- 考勤同步同样在 `run_in_threadpool` 中执行一轮 `FeishuSyncEngine.sync_once()`，返回其结果字典：未配置、未开启或没有在职员工时是 `ok: false` 与 `detail`，属正常状态因而仍返回 200；引擎未初始化返回 503。

考勤（`app/attendance/routes.py`）使用独立的账号体系，除 `login` 外的端点都要带 `X-Attendance-Token`：

```text
POST       /api/attendance/login                     登录并签发会话 token
POST       /api/attendance/logout                    注销当前会话
GET        /api/attendance/me                        当前账号
POST       /api/attendance/change-password           修改密码
GET|POST   /api/attendance/policies                  考勤规则列表 / 新建
PUT|DELETE /api/attendance/policies/{id}             修改 / 删除
GET|POST   /api/attendance/tags                      标签列表 / 新建
PUT|DELETE /api/attendance/tags/{id}                 修改 / 删除
GET|POST   /api/attendance/employees                 员工列表 / 新建
PUT|DELETE /api/attendance/employees/{id}            修改 / 删除
GET|POST   /api/attendance/imports                   导入批次列表 / 上传并解析打卡表
GET        /api/attendance/imports/{id}              批次详情
GET        /api/attendance/imports/{id}/export       导出核算表
GET|PATCH  /api/attendance/results                   核算结果列表 / 人工调整
POST       /api/attendance/results/{id}/approve      确认核算结果
GET        /api/attendance/suspicions                跨日疑似列表
POST       /api/attendance/suspicions/{id}/resolve   处理跨日疑似
GET        /api/attendance/dashboard                 看板统计
GET|PUT    /api/attendance/feishu-config             飞书考勤配置读取 / 保存
POST       /api/attendance/feishu/sync               触发一次飞书打卡同步
GET        /api/attendance/feishu/status             同步状态
```

- 考勤角色为 `admin` / `hr` / `supervisor` / `viewer`；`supervisor` 与 `viewer` 的写操作返回 403。
- 默认管理员账号为 `admin`，初始密码为 `admin`；`account.must_change_password` 为真时所有写操作返回 403，直到 `POST /api/attendance/change-password` 成功。
- 员工档案字段限于工号、姓名、别名、部门、岗位、入职日期、用工状态、手机号、标签与考勤策略，不保存银行卡或支付宝等支付信息。
- 导入只接受 `.xlsx`；上传文件名先经 `safe_filename()` 消毒，再落到 `attendance_imports/<年>/<月>/`。
- 考勤与招聘候选人状态保存在本机 SQLite（`attendance.db` / `recruitment.db`），位置由 `app_data_dir()` 决定：`--data-dir` 会同步写入 `TALENT_HUB_DATA_DIR`，使两类 SQLite 与任务仓储共用同一根目录。

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
- 筛选标准（`筛选标准.json` 与标准 Markdown）和电话摘要（`summaries/*.json` / `*.md`）由 `atomic_write_text()` 原子写入；解析文本与电话转写等其余文本产物使用直接文件写入，不具备同样的进程中断原子性；调整保存顺序时必须单独检查恢复行为。
- 候选人失败隔离：单份失败不能丢失其他结果。
- 任务级取消隔离：取消任务 A 不能中止任务 B 的模型客户端。
- 对比缓存必须包含任务 ID 和结果文件哈希，避免跨任务污染或结果变化后返回旧排序。

涉及线程池、锁、保存顺序、future 或取消事件的修改，必须运行并发、取消、上传竞争、断点恢复和对比缓存相关验证。
