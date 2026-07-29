# 给 ZCode 用的 macOS Computer Use

[English](./README.md)

这是一个在本机运行的 ZCode Plugin + Skill：让 Agent 选择真实 macOS
窗口，读取截图与辅助功能树，并像人一样点击、拖拽、输入、滚动、启动 App，
最后检查屏幕上的实际结果。

项目复刻 Codex Computer Use 最关键的闭环——选择真实窗口 → 观察 → 只做一次
动作 → 刷新 → 验证——但不会加入 Codex Windows 版的 App 限制或危险动作确认
分类。主后端可直接控制后台窗口，不移动用户的真实光标，也不抢前台焦点；主
后端无法完成时，再交给 Quartz/PyObjC 原生兜底。

Plugin 本身没有 App 白名单、风险分类器、批准口令、远程视觉服务或目标禁用
表。剩余权限边界只有 ZCode 的 **Full Access**，以及 macOS 一次性的“辅助
功能”和“屏幕录制”TCC 授权。
Plugin 自有守护进程还会启用 Cua Driver 可选的旧版 `page` 变更能力，避免
Full Access 在 JavaScript、DOM 点击或文字投递上暗中保留第二层依赖限制。

## 架构

| 层 | 作用 |
| --- | --- |
| `$macos-computer-use` Skill | 强制使用最新状态完成“观察 → 动作 → 验证” |
| `macos-computer-use` MCP | Cua Driver 0.13.1；后台 AX/像素输入；独立 unrestricted 守护进程 |
| `macos-computer-use-fallback` MCP | 项目自带的 28 工具 Quartz/PyObjC 窗口/桌面直接输入服务 |
| ZCode Plugin + marketplace | 安装 Skill 和两个本地 stdio MCP Server |

动作升级顺序是：后台辅助功能 → 后台像素 → 临时前台 → 原生直接兜底。该
设计借鉴了现有 Hermes macOS Computer Use Skill、Open Computer Use 的原生
macOS 实现和开源 Cua Driver；ZCode 打包、无审批启动器、兜底运行时和测试由
本项目实现。

## 在 ZCode 安装

1. 打开 ZCode 的 **设置 → Plugins → Marketplace**。
2. 点击 **+**，添加 `BraveCowNoFear/computer-use-for-zcode-macos` 或本仓库 Git URL。
3. 安装并启用 **macos-computer-use**。
4. 对需要连续执行、不希望 ZCode 逐条确认的任务选择 **Full Access**。
5. 新建任务，在输入框键入 `/`，从“技能”分组选择 **macos-computer-use**，
   然后输入：

   ```text
   检查 macOS 权限，打开备忘录，新建一条标题为“旅行清单”的备忘录，并确认它已经可见
   ```

6. macOS 询问时给 `CuaDriver.app` 授予“辅助功能”和“屏幕录制”，然后重启
   ZCode。若签名 App 的权限面板没有出现，执行一次
   `bash plugins/macos-computer-use/scripts/install.sh`；Cua Driver 0.13.1
   刻意不允许 Agent 可调用的 MCP 直接弹出 TCC 授权框，这条人工启动的命令会走
   LaunchServices 的受信任授权路径。若启用原生兜底，系统也可能要求给对应的
   Python/ZCode 进程授权。

第一次启动时，主启动器只下载固定版本的 Cua Driver 通用发布归档，校验
SHA-256 后原子发布到 Plugin 数据目录，并验证 Gatekeeper、Cua AI Team ID 和
签名 Authority。以后每次复用还会核对主驱动与光标辅助程序是否逐字节匹配固定
发布版的 SHA-256，因此不会把另一个“签名有效且版本号相同”的 Bundle 误当成已测试
发行物。它会证明仅保存在 Plugin 数据目录内的持久遥测设置已关闭，同时关闭独立更新检查，再以
`--no-permissions-gate --permission-mode unrestricted --dangerously-bypass-approvals`
启动本 Plugin 专用守护进程。关闭的是 Cua Driver 自己的首次权限引导，避免后台服务在 MCP
socket 已建立后重新打开界面或重启；它不会授予或绕过 macOS TCC，系统权限仍需由用户运行一次
安装/授权命令。守护进程还会传入
`CUA_DRIVER_ENABLE_LEGACY_PAGE_MUTATIONS=1`。只有版本
和工具面与测试版本完全一致、实时状态回读为
`permission mode: unrestricted`，且没有配置 user、managed 或 session policy 时才会复用；
启动器还会用一个无效目标探针证明旧版页面变更已进入正常 pid 校验，而不是被
上游默认关闭；未带该能力的旧专用 daemon 会被替换。
专用 socket 按用户和版本隔离且仅
当前用户可访问。兜底后端要求 CPython 3.10–3.15，会创建私有 Python 环境，且只安装经过测试的
五包 PyObjC 12.2.1 完整闭包，不会在安装时重新解析出更高版本的传递依赖。
CPython 3.10–3.15 的所有发布 wheel 均按 SHA-256 白名单校验，pip 强制使用 hash 模式。
首次启动会先在临时环境内完成安装与自检，再原子发布到按“依赖闭包版本”隔离的
运行时目录，且不写用户共享的 pip 缓存。仅更新 Skill、文档等 Plugin 内容时复用
同一路径；只有经过测试的原生 wheel 闭包变化时才更换，避免无谓重装和 Python TCC 路径抖动。

Plugin 不会覆盖全局 `/Applications/CuaDriver.app`，也不会停止用户无关的
Cua daemon。任何 Plugin 都不能伪造或绕过 macOS TCC 授权。仅依赖辅助功能树的任务可显式关闭截图，在未授予
“屏幕录制”时继续；像素和全桌面路径仍需要该授权。
公开的 `check_permissions` 调用只读；即使在 unrestricted 模式，`prompt:true`
也会被拒绝，因为 macOS 授权 UI 必须由用户掌控。该 TCC 规则不会给普通 Computer
Use 增加 App 白名单、动作风险分类或逐动作批准。

双架构 macOS CI 还会通过 ZCode 使用的真实 stdio MCP 证明连接级图像配置互不
污染、只读健康/TCC 状态归属于 driver daemon、语义光标显隐可回读，以及 `auto`
session 只能显式单向升级到 desktop。公开 MCP 的 `prompt:true` 必须在受信任主机
TCC 边界失败且不弹权限框；旧版 `page` 变更必须通过默认关闭层并到达正常
pid/窗口校验。测试还会从当前未运行的 Calculator/TextEdit 中冷启动
一个，绑定返回的新 pid/窗口，只结束该 pid 并确认它消失；随后恢复配置并结束
自己创建的 session。

本项目结构遵循 ZCode 当前的
[Plugin 与 Marketplace 规范](https://zcode.z.ai/cn/docs/plugin)，包括
`.zcode-plugin/plugin.json`、`.mcp.json` 以及官方支持的 Plugin 根目录/数据目录模板变量。

## 能做什么

- 控制前先读取固定驱动的 schema-v1 稳定健康报告，分别判断核心运行时、签名 Bundle
  归属、TCC 授权、AX 可用性和只读屏幕捕获状态，不在诊断调用中弹授权框。
- 发现、启动原生 App，直接取得匹配的 pid/窗口集合，并精确选择工具真实返回的窗口。
- 可先快速盘点正在运行的 App 与可见窗口而不读取窗口内容，再把一个真实返回的
  pid/窗口绑定到完整截图加辅助功能循环。
- 可在 `start_session` 中原子选择本地已安装的光标主题与减弱动态模式，避免先闪现默认主题。
- 可把某条 MCP 连接临时切到不缩放的原始窗口 PNG，用于逐像素核验；同时证明另一条连接
  保留自己的有效配置，完成后立即恢复，不改守护进程的全局持久默认值。
- 按签名主后端的真实观察形状工作：AX `tree_markdown` 与截图默认一并返回，只能用省略
  截图作为性能开关；写入本地文件仍保留返回的图像几何，兼容字段 `capture_mode` 不会改变
  实际捕获模态。
- 通过 `launch_app.urls` 把已存在的本地文件/目录或资源 URL 交给精确原生 App：缺失路径
  返回结构化错误，随后绑定返回的 pid/窗口、回读焦点抑制、重新观察，并只关闭测试创建的
  精确窗口；不使用 shell `open` 或 AppleScript 激活绕过驱动。
- 兜底观察同时绑定 App、pid、CGWindowID，并在可用时用 AXWindowNumber
  精确关联辅助功能窗口；遇到等价候选会拒绝猜测。
- 对 Chromium/Electron 尽力开启完整辅助功能可见性，将窗口、菜单栏、行、
  Contents 和 VisibleChildren 合并为同一代可操作索引；大型页面可调高默认
  1200 节点、64 层的观察预算。紧凑行仍保留子角色、选中/展开/禁用/可写状态、
  值类型、帮助、占位符、标识符和控件真实声明的动作。
- 兜底端与 Codex 核心一致，默认只取截图；需要索引时显式请求辅助功能树，也可按需同时获取两者。
- 按 AX 元素或窗口内像素点击、双击、右击、拖拽和滚动；直接兜底在点击、
  拖拽起点和原始鼠标按下前先把指针移到已定位坐标，复现人类真实的悬停—按下序列；
  在新坐标释放已按住的鼠标键时先发送末段拖动，未确认的释放会保留到退出清理。
  已签名主后端的双击/右击工具也是强制运行能力，并由 live gate 根据可见状态验收。
  同一 live gate 还会按新截图定位，验证有时长的前台滑块拖拽和定点后台滚轮事件。
- 可从主后端的新鲜窗口截图裁出小区域，查看放大的 JPEG，再把一次点击/输入坐标
  精确映射回同一 pid/窗口；真实 Mac 门禁会验证 zoom 坐标确实命中可见 AppKit 目标，
  随后丢弃这个按 pid 保存的临时上下文。
- 主后端的文字输入、绑定 pid 的 Command-Shift-K 聚焦输入框快捷键、绑定元素的 Space
  单键会分别验证，最后才执行普通鼠标点击。
- 基于新快照 token 的 `set_value` 必须先返回 AX confirmed 回读，再由下一帧滑块元素独立
  暴露目标数值，不能把拖拽结果或动作响应本身当成完成证据。
- 主 session 会以必填原因从 `auto` 升级到 desktop，回读 `get_session_state` 后根据新鲜
  主显示器像素移动真实指针，再恢复原始多显示器位置并重取桌面状态；异常清理也会复位指针。
- 按 bundle ID 新建隔离 Calculator 实例，拒绝启动前已存在的 pid，只绑定返回 pid 所属窗口，
  再以前台 Command-Q 关闭该实例；仅当协作退出未落地时才用 `kill_app` 做定界清理。
- 输入 Unicode、用真实修饰键按下/抬起序列发送 Mac 快捷键，兼容常见 X11/macOS
  与小键盘导航别名，并可直接设置辅助功能控件值。
- 默认在后台完成工作；只有刷新后确认动作未送达才临时切前台。前台兜底会抬起
  精确返回的 pid/窗口组合：先走与 Cua Driver 相同的本地 SkyLight 精确窗口路径，
  再回退公开 AppKit 激活；随后抬起绑定的 AX 窗口，不支持 `AXRaise` 时依次尝试
  Main/Focused 属性，并在输入前回读确认 WindowServer 前台 PSN（或公开前台 pid）
  以及焦点窗口的 AXWindowNumber/CoreFoundation 身份；目标不暴露这些身份时，则确认
  绑定阶段已证明唯一的标题/位置/尺寸签名，或该 pid 唯一暴露的 AX 窗口。
- 仅在结果明确要求窗口持续置前，或远程桌面等焦点代理必须跨多次调用保持激活时，
  才持续置前一个精确返回的窗口。主后端真机门禁会校验激活路径、App 新鲜的
  `active` 回读和重新枚举出的同一窗口；单次前台输入仍使用“置前 -> 动作 -> 恢复”。
- 原生 App 菜单使用语义化双快照路径：对新鲜且已置前的菜单栏项执行 `pick`，重新观察
  打开的菜单后再操作新返回的菜单项，最后验证可见结果。真机门禁在一次性窗口上证明该
  AX 路径，不猜测关闭状态下被省略的菜单子项。
- 每个已声明的主 session 都有独立彩色语义光标。点击、拖拽、滚动、文字、按键、
  导航和 App 动作会产生可见动画，但不移动用户真实指针；光标可按 session 隐藏、
  回读，并在 `end_session` 时移除。光标本地徽标显示简短、面向任务的 session 名，
  便于区分并发 Agent，且不应包含密钥或复制来的用户内容。
  每个 session 的贝塞尔路径、弧度、弹性、速度/停留时长和空闲可见性都可按需调整、
  回读并恢复，用于更像人的演示轨迹，但不会改变真实输入目标或物理指针语义。
  已安装的光标主题和“减少动态效果”模式也可按 session 选择、回读并恢复；Agent
  调用不能注入路径、URL、源图或内联动画。
  窗口态 `move_cursor` 可先放置虚拟光标以展示清晰的操作轨迹，不会触碰真实指针；
  桌面态同名工具则必须基于新鲜全桌面像素显式移动真实指针。
- 把一个已返回的 Chromium/Electron 原生窗口精确绑定到页面 target，再用快照作用域的
  语义 ref 完成导航、可信或显式 DOM-event 点击、可整值替换的输入、指针手势、页面弹窗、
  上传与下载。每次变更都会作废旧 ref 并重新抓取页面；浏览器外壳、原生选择器/弹窗、
  不支持的 webview 和依赖拒绝的路由仍走原生控制阶梯。
  页面动作复用 session 的语义光标，但不移动真实指针或改变焦点；只有坐标可安全映射的
  已选标签页才显示它，而且仍必须用下一张页面快照证明完成结果。
- 可按用户明确要求在本地记录带顺序的动作轨迹、前后截图、状态和参数；不会覆盖另一连接的
  守护进程全局录制，默认不录视频，并在读取证据前结束自己的精确目录。同一存活窗口内的
  显式回放支持遇错即停，结束后仍需刷新可见状态验证。
- 最后兜底到真实全局鼠标/键盘事件以及剪贴板读写。
- 兜底端可分别请求“辅助功能”和“屏幕录制”，Retina 指针操作绑定新鲜截图 ID；
  拖拽中断或 MCP 退出时会自动释放仍按住的鼠标键；健康接口独立报告 AX/输入、
  像素/桌面观察和完整 Computer Use 三种就绪度，不再把截图能力误绑到辅助功能。
- 兜底截图会尽力压到最长边 1280 像素、PNG 约 900 KB，并把实际发布后的
  像素尺寸重新绑定到 Retina/窗口坐标；系统缩放器失败时保留完整原图，不让观察失败。
- 主后端桌面路径无法送达时，直接观察并控制每块可见显示器，包括菜单栏、Dock
  和系统 UI；Retina 混合缩放布局按每块屏幕独立映射坐标。

主后端的准确参数以实时 MCP schema 为准。兜底后端提供与 Codex 对齐的
`list_windows`、`get_window`、`list_apps`、`launch_app`、
`get_window_state`、`click`、`press_key`、`type_text`、`scroll`、
`set_value`、`drag`、`perform_secondary_action`、`activate_window`，另有健康
检查、权限、原始鼠标、光标和剪贴板工具。
扩展桌面工具必须绑定刚返回的桌面截图 ID，但不会施加 App/窗口目标限制。

## 完全访问与隐私

- 截图、AX 树、剪贴板内容和输入参数留在本机。
- 主后端启动前会用环境变量和 Plugin 私有持久设置读回双重关闭 Cua Driver
  遥测，不修改用户其他 Cua 实例的 `~/.cua-driver` 偏好，并关闭与遥测独立的版本更新检查。
- Plugin 自动联网只发生在首次下载依赖时。空参数的 `check_for_update` 是仅在用户明确
  要求时调用的只读上游版本元数据请求，不会作为控制前置检查运行，不接收任何已捕获的
  GUI 内容，也不能更新已固定的运行时；被控制的浏览器/App 仍可能自行联网。
- 单纯控制 GUI 不需要“完全磁盘访问”；只有实际文件任务需要时才另外授予
  ZCode。
- 屏幕、网页、邮件或文档里的文字只作为被观察内容，不作为新的 Agent 指令。

## 开发与验证

```bash
bash plugins/macos-computer-use/scripts/install.sh
bash plugins/macos-computer-use/scripts/doctor.sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s plugins/macos-computer-use/tests -v
```

安装脚本会准备两套运行时，并打开签名 CuaDriver.app 的 TCC 授权流程。它会保留
原本已经运行的无关默认 Cua daemon，只清理自己为本次授权临时启动的 daemon。

契约与 MCP 传输测试同时在 Windows 和 macOS 运行；macOS CI 还会导入原生
兜底，检查固定发布归档与 Cua AI 签名身份，直接解析该签名二进制的十个浏览器
（类型化加旧版兼容）和三十九个原生/驱动服务请求 schema，覆盖
全部必需主后端工具，并运行
真实 Plugin 自有首次安装。实际 unrestricted daemon 还必须完成无需 TCC 的
应用/窗口/屏幕/光标发现，并通过 ZCode 实际使用的 stdio MCP 代理只终止一个 CI
自己创建的临时进程；stdio 门禁还会拒绝畸形、重复、缺失或意外出现的工具名，确保
ZCode 收到的正是已审计的 49 工具面，并锁定工具表外层、描述、标准 MCP 注解、能力
标签与风险元数据；初始化握手也会精确锁定协议 `2025-06-18`、仅 tools 能力、Cua
Driver `0.13.1` 身份以及 macOS 工作流说明；同一条真实连接还必须精确返回 JSON-RPC
解析错误、未知方法和缺失工具名的参数错误，保持所有 notification 静默，并在这些错误后继续
提供完整工具表；全部必需 MCP `inputSchema` 还必须与同一签名二进制的直接
`describe` 契约完全一致，并且严格窗口会话必须能在无 TCC 下创建、读回和干净结束，
门禁才算通过。
普通仓库测试还会从固定的原生/浏览器合同推导同一组 49 个工具，并要求每个准确工具名都能
在 Skill 文档中被发现，避免诊断用 `get_screen_size` 或显式版本提示
`check_for_update` 只存在于 schema、却无法被 ZCode Agent 正确路由。
测试还会拒绝在主工作流中混入兜底专属的 `include_text`，并锁定 macOS 浏览器的结构化
恢复错误码以及本地轨迹证据的准确文件布局。
真实的后台
“截图 → 点击/输入 → 再截图”
闭环必须在已解锁且授予 TCC 的交互式 Mac 上测试。托管 runner 不保证这些授权，因此每个
macOS 任务都会在原生 TCC 就绪检查后执行直接兜底的一次性 GUI smoke，并记录签名 driver
的精确 TCC 回读；只有签名 App 两项真实为可用时才追加主后端 GUI smoke，不会伪造或强行
写入授权。

在这样的 Mac 上，可运行下面的一键闭环门禁。它只创建项目自带的临时 AppKit
窗口：先验证签名驱动身份以及主后端的后台截图/输入/点击，再验证直接兜底的
全桌面快捷键/文字输入、真实 Quartz 坐标点击、滑块拖拽、原始按住/移动/松开和
滚轮事件；像素路径会在每次状态变化后重新截图，并核对夹具本地原子发布的控件状态，
不会假设裸 Python 进程具备完整的 `.app` AX 树。两条路径都会重新观察可见结果，结束前恢复原光标位置并自动关闭
测试窗口，不接触用户文档：

```bash
bash plugins/macos-computer-use/scripts/live-smoke.sh
```

## 项目结构

```text
marketplace.json
plugins/macos-computer-use/
  .zcode-plugin/plugin.json
  .mcp.json
  macos_cua/                  # 原生直接兜底 MCP
  scripts/                    # 主/兜底启动和诊断
  skills/macos-computer-use/  # ZCode Agent 工作流
  tests/
```

## 上游与许可证

后台控制依赖 MIT 许可的 [Cua Driver](https://github.com/trycua/cua)，路由模型
参考了[历史版 Hermes macOS Computer Use Skill](https://github.com/NousResearch/hermes-agent/blob/17dfc6bec4a8b7fd840d479c33e9a7b2449f805d/skills/apple/macos-computer-use/SKILL.md)，
并核验了 MIT 许可的
[Open Computer Use macOS Skill 与原生实现](https://github.com/iFurySt/open-codex-computer-use/tree/a265277f6677ef00a1c597f54616cc3410d8d297/skills/open-computer-use)。
本项目采用 MIT 许可证，详见 [LICENSE](./LICENSE) 和
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。
