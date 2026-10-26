# 上游网页改版监控（watchdog）

> 状态：**已实现**。落地位置：`server/services/watchdog.py`（抓取 / 归一化 /
> 判读 / 邮件）、`server/routers/watchdog.py`（`/api/watchdog*`）、
> `server/main.py` 的 lifespan 调度器。前端未加页面，先以 API 使用。

腾讯改版时**接口常常照旧**，变的是页面结构、入口位置与活动文案。于是会出现两种
静默失效：新活动没人知道；我们依赖的 DOM / 路径被改掉，抓取看起来正常、其实抓空。
本模块盯的是**结构指纹**，不是内容比对。

## 工作方式

```
抓目标页 → 归一化（结构行 + 可见文本行）→ 存指纹
      ├─ 指纹相同 → 结束
      └─ 指纹不同 → diff 摘要 + 关键词命中
                       ├─ LLM 判读（可选）→ {layout_changed, claimable, anomaly, ...}
                       ├─ 逐账号查「活动补偿」状态（只读）
                       ├─ auto_claim 且确有可领 → dailyplan 领一次（幂等）
                       └─ 按 notify_on 发邮件（先探 SMTP 出口）
```

归一化刻意**丢弃** `<script>` / `<style>` 内容与注释、`href/src` 的值：bundle
哈希与内联脚本每次发布都变，保留它们会让指纹天天抖。保留的是标签骨架
（`class` / `id` / `data-*` / `role` / `aria-*`）与可见文案——改版信号就在这两处。
`kind=js` 的目标只抽字符串字面量与 `/billing`、`/activity` 这类路径。

`kind=assets`（默认的 plans-usage 用它）会先抓页面骨架，再顺着 `<script src>` /
`<link href>` 抓最多 `asset_limit` 个**自有 CDN** 的前端构建产物，抽出其中的中文
字符串与接口路径一起进指纹。原因是 SPA 的活动文案与「领取」按钮并不在页面
HTML 里，而在 bundle 里；页面 HTML 只反映「有没有发布新版本」。第三方脚本
（如 `res.wx.qq.com`）排在最后，避免占掉名额。`asset_limit=0` 退化为只比骨架。

关键词规则是 LLM 的**退化路径**：没配 LLM、或 LLM 调用失败时，只要结构变了且
命中「领取 / 奖励 / 补偿 / claim / reward …」就告警，并标注 `source=rule`。

## 判定「有积分可领」用的是真实回执

`scan_compensation` 对每个账号调 `tencent.compensation_status`（**只读**），
按回执判断：

- `claimed` / `claimed_at` / `claimed_text` 任一成立 → `already`；
- 否则 `credit_num > 0` 或按钮文案含「领」→ `claimable`；
- 其余 → `unknown`（认不出结构，不冒充可领）。

关键词只能说明页面上有「领取」两个字，不能说明这个账号有得领——两者分开报告。

`auto_claim=true` 时，只有 `claimable > 0` 才调用 `dailyplan.run_task('compensation')`，
与手动「领补偿」同一条实现（幂等：有则领、无则空），不另写协议。

## 邮件出口预检（启用的硬条件）

多数云主机 / 容器 / 办公网封 25/465/587 出站。因此：

- `PUT /config` 里 `mail.enabled=true` 时，先探测**配置的端口**（DNS + TCP +
  等 SMTP 220 banner）；探不通就落库为 `enabled=false`，响应带 `warning` 与
  `mail_egress` 明细；
- 端点没变时不重复探测，沿用 `state.egress`；从没测过视为「未验证」；
- 每次发信前再探一次；探不通则跳过发信，并在 `state.egress` 留下原因——
  界面上要能区分「没有变化」与「有变化但邮件出不去」。

探测只做最小动作（TCP + banner）：再往下走会牵扯证书与凭据，把「出口被封」
和「密码错了」混成一个错误。`ports` 里同时给出 587/465/25 的诊断结果，
方便判断该换哪个端口。

POP3 / IMAP 不参与发信，因此没有接入：本功能只需要 SMTP 出站。

## 接口

所有接口都在 `/api/watchdog` 下；除 `GET /status` 外都要求**管理员**。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/config` | 当前配置（密码 / API Key 打码） |
| PUT | `/config` | 保存配置；邮件启用会先探出口 |
| GET | `/status` | 配置 + 上次运行结果 + `due` / `next_run_at` |
| POST | `/run` | 立即检测一轮（同步返回差异与判读） |
| POST | `/baseline` | 清空快照，下次运行重建基线且不告警 |
| POST | `/mail/egress` | 只探 SMTP 出口，并把结果写进状态 |
| POST | `/mail/test` | 先探出口，再发一封测试邮件 |
| POST | `/llm/test` | 用一段固定的示例差异试一次 LLM，回判读结果 |

写操作（改配置 / 立即运行 / 测试）都写审计日志。

## 配置示例

```json
{
  "enabled": true,
  "interval_minutes": 360,
  "targets": [
    {"name": "plans-usage", "url": "https://www.workbuddy.cn/profile/plans-usage",
     "kind": "assets", "enabled": true},
    {"name": "codebuddy-home", "url": "https://www.codebuddy.cn/",
     "kind": "html", "enabled": false}
  ],
  "timeout_seconds": 20,
  "asset_limit": 4,
  "notify_on": "change",
  "scan_compensation": true,
  "auto_claim": false,
  "allow_private_targets": false,
  "llm": {
    "enabled": true,
    "base_url": "https://wb.kuruomi.cloud/v1",
    "api_key": "（保存后只回 ********）",
    "model": "deepseek-v4.1-flash",
    "timeout_seconds": 60
  },
  "mail": {
    "enabled": true,
    "host": "smtp.example.com",
    "port": 465,
    "security": "ssl",
    "username": "notify@example.com",
    "password": "（保存后只回 ********）",
    "from": "notify@example.com",
    "to": ["you@example.com"],
    "subject_prefix": "[WorkBuddy 监控]"
  }
}
```

`notify_on`：

- `change`：只要指纹变了就发（最灵敏，改个文案也会收到）；
- `claimable`：LLM 或补偿扫描认为**有可领**才发；
- `anomaly`：LLM 认为值得处理、或有可领时才发（推荐配合 LLM 使用）。

## 安全边界

- 目标 URL 只允许 http/https；默认**拒绝解析到内网 / 回环 / 链路本地 / 保留地址**
  的目标（SSRF），确需监控内网再显式打开 `allow_private_targets`；
- 单目标响应上限 4 MB，超限即中止并记错误；
- 快照写在 `DATA_DIR/watchdog/`，只存归一化后的行，不存原始 HTML；
- 邮件密码 / LLM Key 读取时打码，保存时回传 `********` 表示「不改」；
- LLM 输入只包含**差异片段与关键词**，不整页回传；输出要求严格 JSON，
  解析失败按规则判读处理（`llm_error` 会带在结果里）。

## 已知边界

- 目标页需要登录时，抓到的是登录页；它的结构变化同样会告警，但无法据此判断
  活动内容——真正「有没有得领」仍以补偿状态回执为准。
- 首轮运行只建立基线、不告警，避免刚开启就收到一封"整页都是差异"的邮件。
- 一轮最多发一封邮件：多个目标同时变化时，正文里已经列全；如需逐个目标发信，
  改 `run_once` 的发送循环即可。
