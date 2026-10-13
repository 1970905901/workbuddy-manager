# Windows 启动脚本

双击或命令行运行这里的脚本，管理**本机原生部署**的 WorkBuddy Manager
（Docker 部署用不到它们，见仓库根目录的 `docker-compose.yml`）。

| 脚本 | 作用 |
|---|---|
| `start.cmd` | 前台启动面板（**关掉窗口即停**），顺带拉起上游网关；首次运行会自动生成 `.env`、初始化上游 `config.json`、准备 Python 虚拟环境 |
| `stop.cmd` | 停掉面板与上游网关 |
| `update.cmd` | 一键更新：下载发布包 → **验签** → 替换 `server/` 与 `web/out/` → 同步依赖 |
| `service-tools.ps1` | 后台常驻方式：`start` / `stop` / `status` / `restart`（日志写 `data\manager.out.log`） |

```powershell
# 在前台看日志地跑（等价于双击 start.cmd）
.\windows\start.cmd

# 后台常驻
powershell -ExecutionPolicy Bypass -File .\windows\service-tools.ps1 start
powershell -ExecutionPolicy Bypass -File .\windows\service-tools.ps1 status
```

**这个目录不能挪。** 脚本按「自己所在目录的上一级」当项目根，去找 `server/`、
`web/out`、`.env`、`upstream/`；`update.ps1` 更新前后还会把这一套脚本备份到
`.tools\scripts_backup` 再按名字还原回**这里**。把它挪到别处（或把脚本单独复制出去）
会变成「启动时报找不到 .venv / 更新后根目录多出一套脚本」这类问题。

前置条件与原生模式的边界（更新上游不可用、手动构建 `wb2api.exe` 等）见
[`deploy/windows-native/README.md`](../deploy/windows-native/README.md) 与
[`deploy/README.md`](../deploy/README.md) 的「Windows 原生部署」一节。

## 启动器（安装版）

`launcher/` 是一个把上面这套脚本包成图形界面的安装器 + 启动器，产出**单文件 exe**：

| 产物 | 作用 |
|---|---|
| `build.ps1` | 从源码构建安装包：组装 payload、编译上游 `wb2api.exe`（本机无 Go 时自动下载便携工具链）、预置 Python 3.11 运行时与依赖、csc 编译 |
| `dist\WorkBuddyManagerSetup.exe` | 双击进入安装向导：选择目录安装、生成桌面与开始菜单快捷方式、注册「设置 → 应用」卸载入口 |
| 安装目录里的 `WorkBuddyManager.exe` | 控制窗口：启动 / 重启 / 关闭项目、运行状态、修改监听 IP 与端口、打开面板；点 X 可选最小化到托盘或退出（可记住选择） |
| 安装目录里的 `Uninstall.exe` | 卸载程序：停止服务、清理快捷方式与注册表，可选保留账号与配置数据 |

```powershell
# 构建安装包（产物在 windows\launcher\dist\）
powershell -ExecutionPolicy Bypass -File windows\launcher\build.ps1
```

安装包内嵌全部运行环境（Python 解释器与依赖、上游网关二进制、前端产物），
目标机器**无需安装 Python / Node / Go**，安装后即可直接启动。构建细节见
[`launcher/build.ps1`](launcher/build.ps1) 顶部注释。

