# 工具

本仓库里的一切都是"某个原语之上的薄工具"。如果这里没有你要的东西，答案通常是 `mc-bridge call <方法>`，而不是再写一个程序。

## 仓库结构

```
mc-agent/                  本仓库：文档、工具、RFC
mc-agent-interface-mod/    Fabric mod（客户端 + 服务端 vantage）
mc-agent-bridge/           守护进程、本地 API、MCP 前端
mc-agent-loop/             智能体 loop 与它的后端
```

## mc-bridge

守护进程与它的客户端。

| 命令 | 作用 |
| --- | --- |
| `mc-bridge run` | 起守护进程：持有游戏连接、提供本地 API |
| `mc-bridge run --api-port 8766 --port-file labs/<lab>/mc-agent-server/port.txt` | 接到实验室实例而不是默认实例 |
| `mc-bridge call <方法> [json]` | 对运行中的守护进程发一次调用 |
| `mc-bridge watch --events chat,game` | 以 JSON 行流式输出事件 |
| `mc-bridge mcp` | 以 MCP（stdio）暴露工具，供智能体运行时使用 |

方法：`status`、`capabilities`、`state`、`entities`、`screen`、`command`、`chat`、`mark`、`wait`、`record_start`、`record_stop`、`connect`、`world`、`lan`、`snapshot`、`snapshots`、`fork`、`restore`、`order`、`events`、`stop`。

本地 API 是换行分隔的 JSON，所以任何能开 socket 的东西都能用它——见 [bridge README](https://github.com/guajun/mc-agent-bridge)。

## MCP 工具

把 bridge 注册为 MCP server 后，智能体运行时看到的东西。

| 工具 | 用来做什么 |
| --- | --- |
| `mc_status`、`mc_capabilities` | 连上了吗、这个实例会什么 |
| `mc_state` | 玩家在哪、血量、维度、tick |
| `mc_entities` | **摘要版**实体列表：按类型计数 + 最近的 N 个 |
| `mc_command` | 发一条命令 |
| `mc_command_output` | 发一条命令**并读回它的回答**——关心回答时用这个 |
| `mc_chat` | 在聊天里说话 |
| `mc_record_start` / `mc_record_stop` | 逐 tick 采样写入 `samples.jsonl` |
| `mc_wait` | 阻塞到游戏推进了 N 个 tick |
| `mc_mark` | 往事件流里打标记（实验开始/结束） |
| `mc_screen`、`mc_connect`、`mc_world`、`mc_lan` | 客户端当前界面、加入服务器、打开存档、把世界开放到局域网 |
| `mc_snapshot`、`mc_snapshots`、`mc_fork`、`mc_restore`、`mc_order` | 分叉活世界并校验还原 |
| `mc_events` | 从游标开始重放缓冲的事件 |

`mc_entities` 刻意返回摘要：真实世界里半径 64 格会回 256 KB 的 JSON，模型没法有效阅读。想看更多就传 `types=` 过滤并调大 `limit`。

!!! info "服务端视角 Toolkit 与它的 Skill"
    bridge 正在成为接收方中立的服务端视角 Toolkit：`player` 和 `context` 会加进
    工具面，分别提供每个玩家的上下文和聊天瞬间的上下文包，而 `capabilities` 会
    如实报告连接的 mod 支持哪些操作。智能体的工作流写在可移植的
    [Toolkit Skill](toolkit-skill.md) 里；某个具体连接能做什么，以
    `mc-bridge call capabilities`（或 `mc_capabilities`）为准。想让它由游戏事件
    无人值守地触发，见 [Hermes 无人值守](hermes-unattended.md)。

## mc-agent-loop

| 命令 | 作用 |
| --- | --- |
| `mc-agent-loop run --backend hermes --trigger @codex` | 常驻，响应聊天 |
| `mc-agent-loop once "<提示>" --backend hermes` | 跑一轮，不需要聊天触发 |
| `mc-agent-loop backends` | 可用的后端：`hermes`、`echo` |

常用参数：`--env-file .env`（让 key 不出现在命令行里）、`--reply-mode command --reply-command 'execute as <名字> run say {text}'`（让智能体用自己的名义说话，见 [智能体在游戏里是谁](player-identity.md)）、`--trigger`、`--ignore-sender`、`--cooldown`、`--chunk-size`、`--history`。

## tools/

让三条轨道真正可用的程序。它们都很薄：只是在组合上面的原语。

| 工具 | 作用 |
| --- | --- |
| `smoke_offline.py` | 对着假 mod 跑整套链路，可带模型也可不带 |
| `launch_instance.py` | 直接启动游戏实例，不需要图形启动器；支持 `--world`、`--username`、`--jvm-property mcagent.autoConnect=host:port` |
| `lab_server.py` | 供给、启动、停止、`exec`、`identity`、`verify` 一个无头 Fabric 实验室（RCON 控制台，纯标准库） |
| `build_mod.py` | 对着实验室自己的服务端/依赖库/API jar 编译 Fabric mod，输出确定性 jar 与构建元数据 |
| `fork_verify.py` | `inspect` 录制、按顺序 `restore`、`check` 实验室是否复现、`diff` 两次录制逐实体对比 |
| `fake_player.py` | 生成、驱动、查询 Carpet 假人——不需要第二个客户端就有身体 |
| `game_cmd.py` | 执行一条游戏命令并打印它产生的反馈 |
| `mcp_probe.py` | 像智能体一样调用某一个 MCP 工具 |

例子：

```bash
python tools/fake_player.py spawn deepseek 103 95 52
python tools/fake_player.py action deepseek jump
python tools/fake_player.py say "来自智能体的问候" --as deepseek
python tools/fake_player.py status deepseek

python tools/lab_server.py provision --name lab-01 --void --fabric-api --carpet
python tools/lab_server.py exec --name lab-01 "tick freeze"
python tools/lab_server.py identity --name lab-01 --json
python tools/build_mod.py --source examples/smoke-mod --lab lab-01 --out labs/build/smoke-mod.jar

python tools/fork_verify.py diff "<录制 A>" "<录制 B>"
```

## 协议

| 协议 | 位于 | 参考 |
| --- | --- | --- |
| interface protocol v1 | mod 与 bridge 之间 | [mod README](https://github.com/guajun/mc-agent-interface-mod) |
| 本地 JSON-lines API | bridge 与其它一切之间 | [bridge README](https://github.com/guajun/mc-agent-bridge) |
| 快照 / 分叉协议 | mod、bridge 与实验室工具之间 | [分叉一个活的世界](protocol-snapshot.md) |
