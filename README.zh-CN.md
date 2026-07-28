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
| `macos-computer-use` MCP | Cua Driver 0.12.6；后台 AX/像素输入；独立 unrestricted 守护进程 |
| `macos-computer-use-fallback` MCP | 项目自带的 28 工具 Quartz/PyObjC 窗口/桌面直接输入服务 |
| ZCode Plugin + marketplace | 安装 Skill 和两个本地 stdio MCP Server |

动作升级顺序是：后台辅助功能 → 后台像素 → 临时前台 → 原生直接兜底。该
设计借鉴了现有 Hermes macOS Computer Use Skill 和开源 Cua Driver；ZCode
打包、无审批启动器、兜底运行时和测试由本项目实现。

## 在 ZCode 安装

1. 打开 ZCode 的 **设置 → Plugins → Marketplace**。
2. 点击 **+**，添加 `BraveCowNoFear/computer-use-for-zcode-macos` 或本仓库 Git URL。
3. 安装并启用 **macos-computer-use**。
4. 对需要连续执行、不希望 ZCode 逐条确认的任务选择 **Full Access**。
5. 新建任务并输入：

   ```text
   $macos-computer-use 检查 macOS 权限，打开备忘录，新建一条标题为“旅行清单”的备忘录，并确认它已经可见
   ```

6. macOS 询问时给 `CuaDriver.app` 授予“辅助功能”和“屏幕录制”，然后重启
   ZCode。若启用原生兜底，系统也可能要求给对应的 Python/ZCode 进程授权。

第一次启动时，主启动器会下载固定版本的 Cua Driver 安装器、辅助脚本和通用
发布归档，逐一校验 SHA-256，安装签名的 `/Applications/CuaDriver.app`，再验证
代码签名和 Gatekeeper 评估，关闭其遥测，然后以
`--permission-mode unrestricted --dangerously-bypass-approvals` 启动本 Plugin
专用守护进程。只有版本和工具面与测试版本完全一致，并且实时状态回读为
`permission mode: unrestricted` 时才会复用；专用 socket 按用户和版本隔离且仅
当前用户可访问。兜底后端要求 CPython 3.10 或更新版本，会创建私有 Python 环境，且只安装经过测试的
PyObjC 12.2.1 二进制 wheel。

如果当前用户不能写入 `/Applications`，主后端会给出明确诊断，原生兜底仍
可使用。任何 Plugin 都不能伪造或绕过 macOS TCC 授权。

## 能做什么

- 发现、启动原生 App，并精确选择工具真实返回的窗口。
- 同时获取窗口截图和带索引的辅助功能树。
- 按 AX 元素或窗口内像素点击、双击、右击、拖拽和滚动。
- 输入 Unicode、使用 Mac 快捷键、直接设置辅助功能控件值。
- 默认在后台完成工作；只有刷新后确认动作未送达才临时切前台。
- 对支持的 Chromium/Electron 页面使用带类型的浏览器工具，同时用原生工具
  控制浏览器外壳、文件选择器和权限弹窗。
- 最后兜底到真实全局鼠标/键盘事件以及剪贴板读写。
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
- 主后端启动前会用环境变量和持久设置双重关闭 Cua Driver 遥测。
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

契约与 MCP 传输测试同时在 Windows 和 macOS 运行；macOS CI 还会导入原生
兜底，并检查固定版本的安装器、辅助脚本和发布归档校验契约。真实的后台
“截图 → 点击/输入 → 再截图”
闭环必须在已解锁且授予 TCC 的交互式 Mac 上测试，托管 CI 无法伪造这一点。

在这样的 Mac 上，可运行下面的一键闭环门禁。它只创建项目自带的临时 AppKit
窗口：先验证签名驱动身份以及主后端的后台截图/输入/点击，再验证直接兜底的
全桌面快捷键/文字输入；两条路径都会重新观察并核对可见结果，最后自动关闭
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
参考了 [Hermes macOS Computer Use Skill](https://github.com/NousResearch/hermes-agent/tree/main/skills/apple/macos-computer-use)。
本项目采用 MIT 许可证，详见 [LICENSE](./LICENSE) 和
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。
