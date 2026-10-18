# 思考通道兜底（reasoning → content）

Issue #135 的落地形态。国际版 deepseek 系模型在思考模式下，答案正文只出现在
`reasoning_content`，`content` 恒为空；只看 `content` 的 OpenAI 兼容客户端（大多数）
会把「只有思考」判成空回复 —— 用户看到的就是「模型没有输出」。

## 行为

在**整轮既没有正文、也没有工具调用**时，把思考原文**同时**作为正文下发：

- 非流式：`choices[0].message.content` 填思考原文，`reasoning_content` 原样保留；
- 流式：收尾前（`[DONE]` 之前）补一帧 `delta.content`，内容为整轮累积的思考。

有正文、或有工具调用的正常轮次**零改动**。工具调用轮尤其不能回填 —— 客户端会把
那段文字当成普通回答，而不是继续走工具链。

## 已知代价与开关

同时渲染 `reasoning_content` 与 `content` 的客户端（如 DeepSeek 自家客户端）会把同一段
话显示两遍。这是「空回复」与「重复一次」之间的取舍，默认选了前者。

关掉它（任选其一，改完重启上游）：

- `config.json` 顶层 `"reasoning_as_content": false`；
- 环境变量 `WB2A_REASONING_AS_CONTENT=0`（`1` / `true` 打开）。

默认 true。启动时若是关闭状态，上游会打一行日志（避免「为什么又空了」变成悬案）。

## 为什么不做成「客户端没要思考就不强开思考」

上游对 deepseek 的 `thinking: {"type":"enabled"}` 注入是对官方客户端行为的模拟（见
`internal/upstream/thinking.go`）：客户端不带 `thinking` 时默认开思考，是刻意的兼容
选择。把它改成「没要就不开」会改变所有 deepseek 调用方的回答风格，且仍有客户端会显式
要思考 —— 那条路还是空正文。所以这里只补响应侧，不动请求侧。

## 实现位置

`dev/upstream-patches/0009-reasoning-as-content.patch`：

- `internal/upstream/reasoning_fallback.go`：进程级开关（atomic，启动时设置一次）；
- `internal/upstream/sse.go`：非流式聚合与流式收尾两处回填；
- `cmd/server/config.go` / `cmd/server/main.go`：配置与接线；
- `internal/upstream/reasoning_fallback_test.go`、`cmd/server/config_test.go`：用例。

## 资产基线 → 产出

- 基线：v2.1.0 的 `upstream-src` 资产（889,722 字节，
  `sha256 5feccd84e00d3ebc9afa98eb973a0a7977d397f60a087c7b5ed1fc9aff6d1623`）；
- 产出：应用 0009 后重打（893,221 字节，
  `sha256 08be0e3dc1b5a667b5a86a3a4ca4926c408b7db705c7b3a9199e1d6bf6e7adbd`，
  307 个文件、0 个 CRLF）；
- 重入验证：对产出再跑一次 `apply_upstream_patches.py`，全部报 `already applied:`。
