# 故障排查

以下多数问题是我们在真实世界里边做边踩出来的。

## 连接

### `doctor` 报 `daemon_not_running`（退出码 4）

```bash
mc-agent daemon start
mc-agent daemon status
mc-agent doctor
```

daemon 会在状态目录写 `daemon.log`。如果 `daemon status` 显示 `staleState`，说明
状态文件指向已死进程：删除它或直接启动新 daemon。

### `connection_failed`、TLS 或 pin 错误

1. `<gameDir>/mc-agent-server/control/fingerprint.txt` 是否存在？没有就带上
   `-Dmcagent.control=true` 重启游戏/服务器。
2. 地址是否为**实际**游戏端口（LAN 世界是公布端口）？`mc-agent target show <name>`
   会打印配置的地址。
3. 指纹变化后重新读取并用 `target add --force` 更新。证书校验无法关闭，这是有意
   设计。
4. 凭证错误时在服务器控制台重新签发，并用 `--token-stdin`/`--token-env`/
   `--token-file` 保存。

### `target_unknown` 或连到了错误的世界

`mc-agent target list` 显示目标与默认项。用 `--target NAME` 或用 `target use NAME`。
daemon 从不猜世界。

### 游戏内 `/mcagent` 提示控制传输已关闭

该实例 JVM 参数缺少开关。加入 `-Dmcagent.control=true` 并重启。新增的 mod 版本也
只有重启后生效；先检查日志中的 mod 列表。

### 两个客户端共用一个端口文件（遗留适配器）

每个遗留端点都会写端口文件，共用游戏目录的客户端可能冲突。给第二个实例独立目录
与端口：

```
-Dmcagent.dir=<instance>/mc-agent-b -Dmcagent.port=25591
```

### 某 mod 只在远程桌面下导致客户端启动崩溃

在 Axiom 5.5.0 + RDP 下见过：`Dear ImGui Assertion Failed: ... Out of texture
memory`，发生在编辑器构建字体图集时。不是我们的代码——升级该 mod 后恢复。如果崩溃
看起来与 interface 无关，先移除其他 mod 验证。

## 命令与反馈

### 命令执行了但没有返回

反馈走聊天。`mc-agent command` 只负责发送；需要回答时用
`mc-agent command-output "<line>"`（或 `tools/game_cmd.py`），它会收集命令产生的
消息。

### gamerule 报 “Incorrect argument for command”

Minecraft 26.2 把 gamerule 改为 `snake_case`：

| 旧名 | 26.2 |
| --- | --- |
| `doMobSpawning` | `spawn_monsters` |
| `randomTickSpeed` | `random_tick_speed` |
| `doDaylightCycle` | `advance_time` |

### 写操作超时且 `resultUnknown`

不要盲目重放。查看 `mc-agent request-status <id>` 与 `mc-agent requests` 账本：
在服务器认领之前就超时的请求会被取消、可以安全重试；已经在运行的会保留账本条目，
直到服务器报告最终状态。如果服务器没有记录（例如游戏重启），条目保持 `unresolved`，
由运营者决定。

### `/summon` 静默失败

如果目标位置在未加载区块，实体根本不会创建，命令仍报告成功。实验室里先强制加载
该区域——`fork_verify.py restore` 会按录制的包围盒处理（遗留本地工具）。

## Tick、顺序与确定性

### 已死亡但不清除的实体

在**游戏运行中**先杀死它们，然后 `save-all flush`，再冻结。冻结的游戏不会运行清除
死亡实体的循环，它们会以幽灵形式留在 tick 列表里——选择器看不到，读列表的工具能看到。

### 恢复的 fork 实体不全

依次检查：区块是否加载（见上）、录制半径（半径以*玩家*为中心；无头 lab 没有玩家，
应请求全部），以及录制的实体是否是乘客——乘客 NBT 嵌在载具里随载具恢复，不会单独
生成。

### 同一实验在不同运行中数字不同

实体 tick 顺序在加载时按区块加载顺序重建，物理逐实体计算。实验室里可以掌控顺序：
录制并比较快照。两次运行不同时，diff 会显示哪些实体轨迹不同——这是测量结果，不是
失败。

实验室里值得固定的开关：`gamerule spawn_monsters false`、
`gamerule random_tick_speed 0`、`gamerule advance_time false`、
`difficulty peaceful`，以及 `tick freeze` + `tick step N` 做确定性步进。

## 实验室

### 世界已复制，但 lab 里没有实体

无头服务器没有玩家，因此没有区块加载。强制加载关心的区域
（`forceload add <x1> <z1> <x2> <z2>`），并记住复制的世界在区域文件里仍带实体数据
——只想保留录制实体时，先在游戏内清理并 `save-all flush` 再恢复。

### 两个服务器抢一个端口

遗留适配器会从基础端口向上扫描，第二个实例会落到下一个空闲端口。读取每个实例自己
的端口文件，不要假定 25580/25581。控制传输使用各服务器自己的游戏端口。

## 智能体

### 智能体看不到游戏

先调用 `mc-agent version`、`doctor` 和 `capabilities`。根据实际错误码区分 daemon
不可达、mod 未连接和操作不支持。如果描述错了玩家，用该玩家的稳定 UUID 调用
`player`；一个服务端视角 daemon 能解析所有在线玩家。

### 兼容 loop 自己回答自己，或从不回答

这只适用于遗留客户端视角的 `mc-agent-loop`，不适用于用户主动型 Harness 或 webhook
方案。loop 会忽略它所连接客户端自己的聊天，所以单机会话无法用自身聊天触发它：要么
使用第二个玩家（[玩家身份](player-identity.md)），要么用一次性回合
`mc-agent-loop once "..."`。loop 依赖遗留 Python bridge，Codex、Claude Code 或任何
直接运行 CLI 的 Harness 都不需要它。

### 回复被截断

聊天行很短。`--chunk-size` 拆分长回答，`--chunk-delay` 控制节奏。这是 loop 配置，
不是 Toolkit 命令。

### 智能体的回答没有出现在游戏里

daemon 从不把回复写回 Minecraft。请在 Harness 或 webhook 路由中配置投递；见
[Hermes 与无人值守](hermes-setup.md)。
