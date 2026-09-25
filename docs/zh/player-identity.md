# 智能体在游戏里是谁

简短回答：接口 mod 是**客户端** mod，所以它借用运行它的那个客户端的身份。要让智能体拥有自己的身份，有三条路——而 **Carpet 假人是其中最便宜的一条**：不需要开局域网、不需要第二个账号、不需要第二个客户端。

## 0. Carpet 假人（推荐：身体、声音、数据，单客户端）

假人是**服务端**的玩家。单机里服务端就是集成服务端，所以其它什么都不用改：你自己的客户端用一条命令把它造出来，它就像任何玩家一样存在于世界里。

```bash
# 每个世界做一次：允许非 op 调用这条命令
python tools/game_cmd.py "carpet commandPlayer true"

# 身体：生成、驱动、删除
python tools/fake_player.py spawn deepseek 103 95 52
python tools/fake_player.py action deepseek look north
python tools/fake_player.py action deepseek jump
python tools/fake_player.py action deepseek attack continuous   # 挖掘/攻击
python tools/fake_player.py kill deepseek

# 声音：由服务端代它广播
python tools/fake_player.py say "来自智能体的问候" --as deepseek
# -> [deepseek] 来自智能体的问候

# 数据：权威的，直接来自服务端
python tools/fake_player.py status deepseek
```

在真实世界里验证过：`deepseek` 被生成、`look north` 让服务端的 `Rotation` 变成 `[180.0f, 0.0f]`、客户端看到同位置的 `RemotePlayer`、`/data get entity deepseek Motion` 返回服务端自己的数值、`execute as deepseek run say ...` 在聊天里产生 `[deepseek] ...`。

| 优点 | 代价 |
| --- | --- |
| 不需要局域网、不需要第二个账号、不需要第二个客户端、不额外吃内存 | 需要服务端装 Carpet、打开 `commandPlayer`、并且有命令权限 |
| 拿到的是**权威**状态（`/data get`），不是客户端插值视图 | 它没有自己的客户端：没有屏幕、没有相机、没有客户端 mod |
| 在游戏规则、生物仇恨、红石层面**算一个玩家** | 因为没有客户端，服务端"对它"发出的命令反馈没人看得见——用调用者身份去 `data get` 读状态，而不是 `execute as` |
| 可按命令粒度驱动：`use`、`attack`、`jump`、`look`、`move`、`mount`、`hotbar`、`drop`、`sneak`、`sprint`…… | 最快也是一 tick 一条命令；不是 tick 级精确的仪器（见 Scarpet / 服务端视角） |

假人自己没有命令权限（而且单机里没有 `/op`），所以它的"声音"是服务端**代它**广播：`execute as <名字> run say <文本>`。`mc-agent-loop` 可以直接这么配：

```bash
mc-agent-loop run --backend hermes --trigger @codex \
  --reply-mode command --reply-command 'execute as deepseek run say {text}'
```

## 1. 第二个客户端（有客户端视角的真玩家）

再跑一个装了同样 mod 的 Minecraft 实例，把第二个 bridge 指向那个实例的 `port.txt`——智能体**就是**那个玩家：自己的背包、坐标、视角和客户端 mod。用 `-Dmcagent.autoConnect=host:port` 启动，它会自己进服。

| 优点 | 代价 |
| --- | --- |
| 任何服务器都能用，不需要服务端 mod | 正版服需要第二个账号（离线服只需换个名字） |
| 智能体看到的是玩家看到的东西（实体、界面、客户端指令） | 一整个客户端的 CPU/GPU 和内存 |
| 两个智能体 = 两个客户端，完全独立 | |

### 已验证的配方（单机世界，两个身份）

```bash
# 客户端 A：你的，作为主机
python tools/launch_instance.py --minecraft-dir <实例> --version 26.2-Fabric
mc-bridge run --api-port 8765            # bridge A -> 你的客户端
mc-bridge call world '{"level": "<存档目录名>"}'    # 打开存档
mc-bridge call lan '{"port": 25577, "mode": "offline"}'   # 开局域网，不校验会话

# 客户端 B：智能体自己的玩家
python tools/launch_instance.py --minecraft-dir <实例> --version 26.2-Fabric \
    --username deepseek \
    --jvm-property mcagent.dir=<实例>/mc-agent-b \
    --jvm-property mcagent.port=25591 \
    --jvm-property mcagent.autoConnect=127.0.0.1:25577
mc-bridge run --api-port 8766 --mod-port 25591        # bridge B -> 智能体的客户端
mc-agent-loop run --backend hermes --trigger @codex --api-port 8766 --env-file .env
```

然后在你自己的客户端里打 `@codex ...`，智能体就会用**它自己的玩家**回答。真实世界里的观测：`gua_jun` 问智能体坐标，`deepseek` 回答"我在主世界（overworld），坐标 X 7.5 / Y 113 / Z -3.5"。

三个要紧的细节：

* **`mode="offline"`** —— 没有 Mojang 会话的客户端（智能体）过不了局域网服务器的会话校验；主机必须接受离线名字（`MinecraftServer.setUsesAuthentication(false)`）。仅限局域网、仅限可信网络。
* **一个玩家一个 bridge。** 两个客户端都会写端口文件，所以给第二个自己的 `-Dmcagent.dir`（以及端口），否则互相覆盖。
* **MCP 工具只指向一个 bridge，因此只指向一个玩家。** 智能体的 `mc_*` 工具必须用它**自己的** bridge（8766）；再注册一个指向 8765 的 MCP server，它就能同时看你的客户端。当工具指向你的 bridge 时，它会兴高采烈地报**你的**坐标——这在重新指向之前真实发生过。

## 2. 服务端视角（mod 0.5.0 已发出）

以上两条路都是通过命令驱动服务端。如果智能体需要**权威的数据**——大多数大炮/TNT 工作真正的主题——诚实的视角就是**服务端侧**，而它不再需要第二个适配器：同一个接口 mod 现在有服务端入口（端口 25581，含快照），单机也覆盖——集成服务端就跑在客户端自己的进程里。见 [分叉一个活的世界](protocol-snapshot.md)。

仍然开放的是把这个视角按 tick 率流式输出、订阅更丰富的游戏事件——见 [#5](https://github.com/guajun/mc-agent/issues/5)。

Scarpet（Carpet 的脚本语言，`/script`）是一个已经存在的中间方案：脚本跑在服务端内部，能读实体 NBT、能调度工作，以文件形式安装。智能体可以用它已有的两个原语管理这些脚本（写文件、执行命令）。

## 3. 任务用户：身份与视线目标（已实测）

服务端视角同时也是**身份**视角。任务一般用 UUID 指名它的用户，智能体用 `mc_player`（MCP）或 `mc-bridge call player`（CLI）解析，拿到的就是**该玩家此刻**的服务端记录：身份、维度、位置、yaw/pitch、眼睛，以及 `player.view.target`——从该玩家自己的眼睛和视线算出的服务端射线。它不会回退到宿主客户端，也不会回退到玩家列表里的第一个人。

实测组合（2026-09-25）：专用 Fabric 26.2 服务器、两个 Carpet 假人、没有任何玩家客户端连着。

| 组件 | 版本 / 提交 | 说明 |
| --- | --- | --- |
| 接口 mod | 0.6.0，`3b93ceb` | 服务端视角 `PLAYER`/`CONTEXT`（PR #4 + #5） |
| bridge | 0.4.1，[`#8`](https://github.com/guajun/mc-agent-bridge/pull/8) | 0.4.0 会丢掉 mod 分离给出的 `view`；0.4.1 把它并进 `player.view` |
| Minecraft / Fabric loader | 26.2 / 0.19.5 | Carpet 26.2+v260616、Fabric API 0.161.0+26.2 |
| Java | 25.0.1 | 实验室服务器所用 |

场景：Alice 在 `(0.5, 100.0, 0.5)` 面向 `(0, 101, 4)` 的发射器，Bob 在 `(4.5, 100.0, 0.5)` 面向盔甲架。调用方式与智能体运行时一致：对 bridge 的 MCP server 用 `tools/mcp_probe.py`。

| 调用 | 结果 |
| --- | --- |
| `mc_player` 传 Alice 的 UUID | `found: true`、`name: Alice`、`view.target.type: block`、`block.id: minecraft:dispenser`、`distance: 3.5` |
| `mc_player` 传 Bob 的 UUID（`state.playerList` 里 Alice 排第一） | `found: true`、`name: Bob`、`view.target.type: entity`、`entity.type: minecraft:armor_stand` |
| Bob 抬头看天（pitch -90） | `view.target.type: miss`、`distance: 5.0` |
| 未知 UUID 或未知名字 | `found: false`——不会替换成另一个玩家或列表第一人 |
| 任务开始时先记录，然后把 Alice `tp` 前进两格 | `uuid`/`name` 不变；`z` 0.5 -> 2.5，发射器距离 3.5 -> 1.5；每次调用都是当前值，不是缓存的开场快照 |

一条 `mc_player` 回复（节选）：

```json
"player": {
  "uuid": "f0a8f4ba-99f5-412a-9189-db832c934913", "name": "Alice",
  "dimension": "minecraft:overworld", "x": 0.5, "y": 100.0, "z": 0.5,
  "yaw": 0.0, "pitch": 0.0, "eye": [0.5, 101.62000000476837, 0.5],
  "view": {
    "blockRange": 5.0, "entityRange": 5.0,
    "target": {"type": "block", "distance": 3.5,
      "block": {"x": 0, "y": 101, "z": 4, "id": "minecraft:dispenser", "face": "north"}}
  }
}
```

### 冷启动入口

首轮冷启动不需要 webhook、也不需要模型后端：外部任务入口带上身份，智能体随后做一次**实时** `mc_player` 调用。上面实测的就是这条路径。

* **入口**：产生任务的那一方——CLI、队列、webhook、操作员——把玩家传进来。不要求游戏侧有回执。
* **字段**：`{"player": "<uuid-或-名字>"}`；优先带横线的 UUID。32 位不带横线的 UUID 也接受，传名字只是便利。后续要携带的稳定值是回复里的 `uuid`，不是显示名。
* **`found: false` 就是答案**：如实报告，不要换成别的玩家重试。谁真的在线看 `state.playerList`。
* **`context_id` 只在入口确实产生它时使用。** 聊天事件会带 `contextId`，`mc_context` 取回冻结的上下文包。先看 `timing`：`receipt` 是网络聊天包，`broadcast` 是服务端 `say`——Carpet 假人的 `execute as <名字> run say ...` 是 `broadcast`，它是真实可取回的上下文包，但**不是**收包时刻的历史。真实客户端的网络聊天路径本实验室没有覆盖，不做声明。
* **这里不新增 Harness 模型后端。** 任务入口加实时 `mc_player` 调用就是完整的身份路径；模型循环是另一项交付。

完整原始记录、版本和修复前后对比见[证据包](evidence/rom13-meta16/live-identity.json)。

## "客户端视角"对测量意味着什么

客户端的实体位置和速度是为渲染插值过的，所以 `record_start` 采到的是 20 Hz 的**客户端视角**。对形状和时序够用；但要精确数值，优先用服务端自己的回答（`/data get`、Scarpet，或 25581 上的服务端视角）。上面测试里出现过：同一个跳起来的假人，客户端报 `vy = 0.333`，而服务端在同一时刻是 `-0.078`——同一个实体，不同的观察点与 tick。
