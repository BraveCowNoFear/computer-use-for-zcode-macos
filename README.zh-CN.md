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
签名 Authority。它会证明仅保存在 Plugin 数据目录内的持久遥测设置已关闭，同时关闭独立更新检查，再以
`--permission-mode unrestricted --dangerously-bypass-approvals` 启动本 Plugin
专用守护进程。只有版本和工具面与测试版本完全一致、实时状态回读为
`permission mode: unrestricted`，且没有配置 user、managed 或 session policy 时才会复用；
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

本项目结构遵循 ZCode 当前的
[Plugin 与 Marketplace 规范](https://zcode.z.ai/cn/docs/plugin)，包括
`.zcode-plugin/plugin.json`、`.mcp.json` 以及官方支持的 Plugin 根目录/数据目录模板变量。

## 能做什么

- 发现、启动原生 App，直接取得匹配的 pid/窗口集合，并精确选择工具真实返回的窗口。
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
- 主后端的文字输入、绑定 pid 的 Command-Shift-K 聚焦输入框快捷键、绑定元素的 Space
  单键会分别验证，最后才执行普通鼠标点击。
- 输入 Unicode、用真实修饰键按下/抬起序列发送 Mac 快捷键，兼容常见 X11/macOS
  与小键盘导航别名，并可直接设置辅助功能控件值。
- 默认在后台完成工作；只有刷新后确认动作未送达才临时切前台。前台兜底会抬起
  精确绑定的 AX 窗口；不支持 `AXRaise` 时依次尝试 Main/Focused 属性，并在输入前
  回读确认前台 pid 与焦点窗口 ID。
- 每个已声明的主 session 都有独立彩色语义光标。点击、拖拽、滚动、文字、按键、
  导航和 App 动作会产生可见动画，但不移动用户真实指针；光标可按 session 隐藏、
  回读，并在 `end_session` 时移除。光标本地徽标显示简短、面向任务的 session 名，
  便于区分并发 Agent，且不应包含密钥或复制来的用户内容。
  窗口态 `move_cursor` 可先放置虚拟光标以展示清晰的操作轨迹，不会触碰真实指针；
  桌面态同名工具则必须基于新鲜全桌面像素显式移动真实指针。
- 对支持的 Chromium/Electron 页面使用带类型的浏览器工具，同时用原生工具
  控制浏览器外壳、文件选择器和权限弹窗。
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
- Plugin 只在首次下载依赖时联网；被控制的浏览器/App 仍可能自行联网。
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
兜底，并检查固定发布归档、Cua AI 签名身份和真实 Plugin 自有首次安装。真实的后台
“截图 → 点击/输入 → 再截图”
闭环必须在已解锁且授予 TCC 的交互式 Mac 上测试，托管 CI 无法伪造这一点。

在这样的 Mac 上，可运行下面的一键闭环门禁。它只创建项目自带的临时 AppKit
窗口：先验证签名驱动身份以及主后端的后台截图/输入/点击，再验证直接兜底的
全桌面快捷键/文字输入、真实 Quartz 坐标点击、滑块拖拽、原始按住/移动/松开和
滚轮事件；两条路径都会重新观察并核对可见结果，结束前恢复原光标位置并自动关闭
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
