# Windows 启动脚本

双击或命令行运行这里的脚本，管理**本机原生部署**的 WorkBuddy Manager
（Docker 部署用不到它们，见仓库根目录的 `docker-compose.yml`）。

| 脚本 | 作用 |
|---|---|
| `start.cmd` | 前台启动面板（**关掉窗口即停**），顺带拉起上游网关；首次运行会自动生成 `.env`、初始化上游 `config.json`、准备 Python 虚拟环境 |
| `stop.cmd` | 停掉面板与上游网关 |
| `update.cmd` | 一键更新：下载发布包 → **核对 sha256** → 替换 `server/` 与 `web/out/` → 同步依赖 |
| `service-tools.ps1` | 后台常驻方式：`start` / `stop` / `status` / `restart`（日志写 `data\manager.out.log`） |

```powershell
# 在前台看日志地跑（等价于双击 start.cmd）
.\windows\start.cmd

# 后台常驻
powershell -ExecutionPolicy Bypass -File .\windows\service-tools.ps1 start
powershell -ExecutionPolicy Bypass -File .\windows\service-tools.ps1 status
```

**更新脚本本身要从仓库取。** 发布包里不带这个目录（这些脚本是给本机原生部署用的，
跟着你的路径与习惯走），`update.ps1` 更新前后还会把它们备份到 `.tools\scripts_backup`
再按名字还原回这里 —— 也就是说它**不会**被发布包替换。

v1.0.86 起发布包不再带 `.sig`，仓库里的 `update.ps1` 也从「验签」改成了「核对 sha256」。
所以用 `update.cmd` 的用户请**从仓库重新取一次本目录的脚本**（或改用面板的
「设置 → 系统更新」，那条路走的是已经改好的 Python 更新器）：停在旧脚本上，v1.0.87
起的新版本会被它自己拦下（「缺少对应的数字签名文件」）。

**这个目录不能挪。** 脚本按「自己所在目录的上一级」当项目根，去找 `server/`、
`web/out`、`.env`、`upstream/`；`update.ps1` 更新前后还会把这一套脚本备份到
`.tools\scripts_backup` 再按名字还原回**这里**。把它挪到别处（或把脚本单独复制出去）
会变成「启动时报找不到 .venv / 更新后根目录多出一套脚本」这类问题。

前置条件与原生模式的边界（更新上游不可用、手动构建 `wb2api.exe` 等）见
[`deploy/windows-native/README.md`](../deploy/windows-native/README.md) 与
[`deploy/README.md`](../deploy/README.md) 的「Windows 原生部署」一节。
