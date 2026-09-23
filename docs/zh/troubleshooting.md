# 疑难排查

下面这些大多是做这套东西时在真实世界里撞出来的。

## 游戏侧

### bridge 说连不上 mod

```
[mc-agent-bridge] cannot reach interface mod on 127.0.0.1:25580; retrying
```

按顺序检查：

1. mod 在 `<实例>/mods/` 里吗？日志里有 `[mc-agent-interface] listening on ...` 吗？
2. 游戏**在世界里**吗？不少原语（命令、聊天、实体）需要已进入世界。
3. 端口被别的进程占了吗？mod 会自动往后让一位并写进 `port.txt`；用 `--port-file` 指过去。
4. 防火墙拦了 loopback 吗？（少见，但某些公司 VPN 客户端会。）

### `/mcagent` 提示 "unknown or incomplete command"

涉及世界的客户端指令需要先进入世界；而**新加的** mod 版本要重启才生效。如果这条命令从来没成功过，就是 jar 没被加载——检查日志里的 mod 列表。

### 两个客户端抢一个 port 文件

每个客户端都会写 `<gameDir>/mc-agent/port.txt`，共用同一游戏目录的两个客户端会互相覆盖。给第二个自己的数据目录和端口：

```
-Dmcagent.dir=<实例>/mc-agent-b, -Dmcagent.port=25591
```

### 某个 mod 让客户端在启动时崩溃（而且只在远程桌面下）

在 Axiom 5.5.0 + RDP 下见过：编辑器构建字体图集时报
`Dear ImGui Assertion Failed: ... Out of texture memory`。不是我们的代码——升级那个 mod 就好了。如果有崩溃看起来和接口无关，把那个 mod 移出去看是否还崩。

## 命令与反馈

### 命令执行了，但什么都没返回

反馈就是聊天。`mc_command` 只负责**发送**；想拿到回答要用 **`mc_command_output`**（或 `tools/game_cmd.py`），它会把命令产生的消息收集起来。

### gamerule 报 "Incorrect argument for command"

Minecraft 26.2 把 gamerule 全部改成了 `snake_case`：

| 旧名 | 26.2 |
| --- | --- |
| `doMobSpawning` | `spawn_monsters` |
| `randomTickSpeed` | `random_tick_speed` |
| `doDaylightCycle` | `advance_time` |

### `/summon` 悄悄什么都没做

目标位置如果在未加载的区块里，实体根本不会被创建，而命令依然报告成功。实验室里要先把区域 force-load 起来——`fork_verify.py restore` 会根据录制的包围盒自动帮你做这件事。

## Tick、顺序与确定性

### 实体死了却一直不消失

要在游戏**运行中** kill，然后 `save-all flush` 落盘，再冻结。冻结状态下的游戏永远不会跑"移除濒死实体"的那个循环，于是它们以幽灵形式留在 tick 列表里——选择器选不到，但任何读列表的东西都能看见。

### 还原后的分叉少了实体

按顺序检查：区块加载了吗（见上）、录制用的半径是多少（半径是**从玩家**量起的；无头实验室没有玩家，所以要"所有实体"）、以及那个实体是不是乘客——乘客的 NBT 内联在载具里，会随载具一起回来，所以不需要单独 summon。

### 同一个实验在不同次运行里数值不同

实体 tick 顺序是加载时按区块加载顺序重建的，而物理是逐个实体计算的。在实验室里你可以**拥有**这个顺序：`mc_fork` 会记录它，`fork_verify.py check` 会比对它。如果两次运行不同，`diff` 会告诉你哪些实体动得不一样——那是**测量结果**，不是失败。

实验室里值得一并钉住的旋钮：`gamerule spawn_monsters false`、`gamerule random_tick_speed 0`、`gamerule advance_time false`、`difficulty peaceful`，以及用 `tick freeze` + `tick step N` 做确定性步进。

## 实验室

### 世界已经拷进去了，实验室里却没有实体

无头服没有玩家，所以没有区块被加载。先把关心的区域 force-load 起来（`forceload add <x1> <z1> <x2> <z2>`），并且记住：**拷贝来的世界在 region 文件里仍然带着实体数据**——如果你只想保留录制的那批实体，就在游戏内清场并 `save-all flush`，然后再还原。

### 两个服务器，一个端口

mod 会从基准端口往上扫，所以第二个实例会落到下一个空闲端口。请读各自实例的 `port.txt`，不要假设一定是 25580/25581。

## 智能体

### 智能体自己和自己对话，或者永远不回

loop 会忽略它所附着那个客户端发出的聊天，所以单机会话没法用它自己的聊天触发。要么用第二个玩家（见 [智能体在游戏里是谁](player-identity.md)），要么用一次性模式：
`mc-agent-loop once "..."`。

### 回复被截断

聊天行很短。`--chunk-size` 负责切分长回答，`--chunk-delay` 控制间隔。

### 模型看不到游戏

智能体需要 bridge 的 MCP 工具，而这些工具指向**一个** bridge——因此指向**一个**实例。如果模型报出来的是别的玩家的坐标，说明它的 MCP server 接错了 bridge；见 [智能体在游戏里是谁](player-identity.md)。
