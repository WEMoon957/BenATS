# zhaopin-cli

智联招聘自动化 CLI。基于 Puppeteer/CDP 驱动本机 Chrome，操作智联招聘 B 端，供 Talent Hub 以子进程方式调用。

> 给 HR 同事的安装与使用说明见 [HR使用说明.md](./HR使用说明.md)（不需要技术背景）。

## 安装

前置条件：Node.js ≥ 20，本机已安装 Chrome 或 Edge。CLI 会自动探测浏览器的常见安装位置，也可以设置 `CHROME_PATH` 指定可执行文件。

```bash
npm install -g @joohw/zhaopin-cli@latest
zhaopin help
```

在源码目录里开发时改用本地安装。`npm install` 会通过 `prepare` 脚本自动完成构建，再把命令注册到全局：

```bash
cd zhaopin-cli
npm install          # 装依赖，并自动执行 npm run build
npm install -g .     # 注册全局 zhaopin 命令，等价于 npm link
```

也可以不注册全局命令，直接用 `node dist/cli/index.js` 调用。

如果终端提示 `command not found: node`，说明 Node 没有加入 `PATH`。此时可以直接用包装脚本运行，不需要改动环境：

```bash
bash scripts/run.sh login          # 等价于 node dist/cli/index.js login
bash scripts/run.sh self-check     # 运行环境自检
```

包装脚本会依次从 `ZHAOPIN_NODE_BIN` 环境变量、`PATH`、以及本机已有的运行时目录中寻找 Node。

## 快速开始

```bash
zhaopin login                # 1. 打开登录页，用手机扫码或账号密码手动登录
zhaopin positions            # 2. 查看职位选择弹层里有哪些岗位
zhaopin recommend            # 3. 读取当前岗位的推荐候选人
zhaopin recommend 后端开发    #    先切换到指定岗位，再读取候选人
zhaopin open 张三             # 4. 打开某位候选人的详情，输出正文
zhaopin greet 张三 李四 王五   # 5. 对候选人打招呼；可一次传多人批量执行
zhaopin request 张三 李四 resume  # 6. 批量索要附件简历
zhaopin download 张三 李四    # 7. 批量下载对方发来的附件简历
zhaopin download-all         # 8. 扫描全部会话，统一下载所有收到的附件简历
zhaopin home                 # 需要时直接跳到企业端候选人推荐页
```

登录必须由用户本人完成，CLI 不代填任何平台凭据。登录态保存在 `~/.zhaopin-cli/.cache/browser-data`，登录一次即可长期复用。

## 命令说明

| 命令 | 说明 |
| --- | --- |
| `zhaopin login` | 打开智联招聘登录页，交由用户手动完成登录 |
| `zhaopin home` | 直接跳到企业端候选人推荐页；未登录时会提示先执行 `login` |
| `zhaopin positions [关键词]` | 打开职位选择弹层，列出可选岗位；传关键词则先过滤 |
| `zhaopin recommend [岗位关键词]` | 读取推荐候选人列表；传岗位关键词则先切换岗位 |
| `zhaopin open <姓名>` | 打开候选人详情面板，输出正文并关闭 |
| `zhaopin greet <姓名> [姓名...]` | 对候选人打招呼，首次会自动确认招呼语弹框；支持一次传多个姓名批量执行，姓名间用空格、逗号或顿号分隔 |
| `zhaopin request <姓名> [姓名...] [动作...]` | 打开聊天框索要信息并支持批量。动作可取 `resume`（要附件简历）、`phone`（要电话，含二次确认）、`wechat`（要微信），可组合；不填默认 `resume` |
| `zhaopin download <姓名> [姓名...]` | 下载指定候选人发来的附件简历，支持批量；此前已下载过的自动跳过 |
| `zhaopin download-all` | 扫描聊天列表全部会话，把对方发来的附件简历统一下载到本地 |

批量执行时一个人失败不会中断整批，输出末尾汇总成功与失败数量，有失败时进程以非零状态退出；相邻两人之间自动加 1.5～3 秒随机间隔降低风控风险，建议单批不超过 5～10 人。单人调用的输出格式与批量调用中单人的段落格式保持一致，Python 侧逐人调用的解析不受影响。

附件简历统一下载到 `~/.zhaopin-cli/downloads/`，文件名带候选人姓名与时间戳；服务端直发的 `.doc` 等附件由浏览器直接落盘，保留原始文件名。对方尚未发送（「附件简历索要中」）的会话不算可下载。

输出为便于阅读与程序解析的纯文本，Python 侧 `app/connectors/zhaopin_cli.py` 依赖该格式做正则解析，调整输出时需同步更新。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CHROME_PATH` | 自动探测 | 浏览器可执行文件路径 |
| `ZHAOPIN_BROWSER_USER_DATA_DIR` | `~/.zhaopin-cli/.cache/browser-data` | 复用登录态的用户数据目录 |
| `ZHAOPIN_BROWSER_REMOTE_DEBUGGING_PORT` | `53471` | 远程调试端口，与 boss-cli 的 53470 错开 |
| `ZHAOPIN_BROWSER_HEADLESS` | `false` | 设为 `true` 时无头运行 |
| `ZHAOPIN_BROWSER_SYSTEM_KEYCHAIN` | `false` | 设为 `true` 时改用系统钥匙串保存 Cookie。默认使用内存临时密钥，登录态只有本 CLI 能读；需要让其它 Chromium 工具（如 agent-browser）复用同一份登录态时开启此项。 |
| `ZHAOPIN_BROWSER_NO_SANDBOX` | `false` | 设为 `true` 时追加 `--no-sandbox`，供受限环境排查启动失败使用 |

## 实现约束

- 点击、滚动、输入全部使用 puppeteer 原生能力（真实鼠标与键盘事件、真实滚轮），不通过注入脚本驱动页面。
- 只在读取文本与属性时做最小化的信息提取，不修改页面 DOM、样式、滚动位置或运行状态。
- 页面选择器集中定义在 `src/common/selectors.ts`，是页面适配的唯一来源；页面改版时只需改这一处。
- 命令结束后只断开 CDP 连接，不关闭浏览器窗口，保留登录态供下次命令复用。

## 目录结构

```text
zhaopin-cli/
├─ src/
│  ├─ config.ts                  数据目录与调试端口
│  ├─ browser/                   CDP 连接、会话管理、人类化延时
│  ├─ common/                    页面选择器与定位辅助
│  ├─ toolset/                   各命令实现
│  └─ cli/                       命令行入口与路由
└─ dist/                         构建产物
```

## 自检

首次使用或排查问题时，先跑一遍自检。源码目录里直接运行：

```bash
node scripts/self-check.mjs
```

全局安装（`npm install -g`）时脚本随包一起发布，用 `npm root -g` 定位：

```bash
node "$(npm root -g)/@joohw/zhaopin-cli/scripts/self-check.mjs"
```

脚本会逐项检查 Node 版本、浏览器可执行文件、数据目录写入、智联域名可达性，以及浏览器能否启动并建立 CDP 连接。任一项失败都会给出具体原因。

若「浏览器启动与 CDP 连接」失败且提示中包含 `sandbox initialization failed`，说明当前运行环境阻止了浏览器的子进程沙箱（常见于受限的容器或沙箱化终端），请换到没有沙箱限制的普通终端重跑。

## 开发

```bash
npm run typecheck    # 类型检查
npm run build        # 构建
npm run dev -- --help
```

## 发布

包名 `@joohw/zhaopin-cli`，发布到公共 npm registry，发布后任何人无需账号即可 `npm install -g` 安装。

```bash
npm login            # 需要 @joohw scope 的发布权限
npm publish
```

`package.json` 已声明 `publishConfig.access: "public"`，把访问级别显式固定为公开可安装。npm 对新建包的默认值本来就是 public，这样写是为了不受本地 npm 配置影响（`restricted` 会让外部用户安装时报 404）。

`prepare` 脚本会在打包前自动执行 `npm run build`，因此 `dist/` 不需要提交到版本库。
