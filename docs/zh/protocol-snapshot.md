# 快照协议 v1（分叉一个活的世界）

mod、bridge 与实验室工具之间的契约。它在代码之前写成，好让三部分并行开发；这里的一切都可以改，但不能**悄悄**改。

## 为什么需要它

存档里有方块和实体 NBT，但**没有实体的 tick 顺序**。那个顺序是加载时重建的（区块加载时，实体被追加进关卡那张插入有序的 `EntityTickList`），而它会改变任何"逐个实体计算"的结果——挤压、堆叠、爆炸。

所以"分叉一个活的世界"需要两样不同的东西：

* **方块/区块**：`/tick freeze` → `/save-all flush` → 拷贝 region 文件；
* **实体 + 它们的顺序 + 内存态**：只有进程内能读到。

## 实例原语（mod 侧）

```
SNAPSHOT [radius] [name]     radius 省略或为 0 = 该关卡在 tick 的所有实体
SNAPSHOTS                    列出磁盘上已有的快照
```

回复：

```json
{"type":"snapshot_ack","id":"before","dir":"<绝对路径>","entities":521,
 "orderHash":"9f2c...","tick":104233,"dimension":"minecraft:overworld","bytes":48213}
```

文件位于 `<mcagent.dir>/snapshots/<name>/`：

`entities.jsonl` —— 每行一个 JSON 对象，**按 tick 顺序**：

```json
{"order":0,"uuid":"466e11e1-...","type":"minecraft:sulfur_cube","entityId":2096,
 "pos":[103.5,56.0,52.5],"vel":[0.0,-0.0784,0.0],"yaw":180.0,"pitch":0.0,
 "nbt":"{Motion:[0.0d,-0.0784d,0.0d],...}","passengers":[],"vehicle":null}
```

`meta.json`：

```json
{"protocol":1,"mod":"mc-agent-interface","modVersion":"0.5.0","minecraft":"26.2",
 "tick":104233,"dimension":"minecraft:overworld","radius":64.0,"entities":521,
 "orderHash":"9f2c...","createdAt":1790145600000,"frozen":true,
 "worldDir":"<绝对路径或 null>","instance":"server|client"}
```

规则：

* `orderHash` = `sha256(按 tick 顺序用 ":" 连接的 uuid)` 的前 16 个十六进制字符。它是那个能**证明**还原复现了顺序的单一数值。
* `nbt` 是 `Entity#saveWithoutId` 渲染成的 SNBT，也就是 `/summon <类型> <x> <y> <z> <nbt>` 正好接受的形式。
* `pos`/`vel`/姿态在 `nbt` 之外重复了一份，这样常见场景不必解析 SNBT。
* 快照**永远不含方块**。方块是 region 文件，由 bridge 拷贝。
* 同名快照会被覆盖，并在回复里说明（`"replaced": true`）。

## 服务端 vantage 的 `STATE` 附加字段

当 mod 以服务端 vantage 运行，`STATE` 会顺带报告世界文件在哪，这样 bridge 不必被额外告知：

```json
{"type":"state","inWorld":true,"instance":"server","levelName":"量子硫方怪",
 "worldDir":"C:/.../saves/量子硫方怪","tick":104233,"players":1,"entities":521}
```

端口：客户端 vantage 监听 `mcagent.port`（25580），服务端 vantage 监听 `mcagent.serverPort`（25581），数据放在 `mcagent.serverDir`。

## Bridge 工具（在原语之上组合）

| 工具 | 作用 |
| --- | --- |
| `mc_snapshot(radius, name)` | 执行 `SNAPSHOT`，返回 ack |
| `mc_snapshots()` | 列出实例上的快照 |
| `mc_fork(name, radius, regions, world_dir)` | 冻结 → `save-all flush` → 快照 → 拷贝世界文件 → 解冻；返回分叉目录与清单 |
| `mc_restore(dir, dry_run, target)` | 读 `entities.jsonl` 并**按记录顺序**发 `/summon`（默认演练） |
| `mc_order(dir, target)` | 对目标重新取样并与 `dir` 比对 `orderHash` |

## 还原，以及为什么不需要我们写代码

```bash
/summon minecraft:sulfur_cube 103.5 56.0 52.5 {Motion:[0.0d,-0.0784d,0.0d],...}
```

一行一个实体，按文件顺序。vanilla 会按这个顺序重建 tick 列表，所以重新取样应当报出**同样的 `orderHash`**——这个相等就是整条轨道的验收标准。

## 已知限制（刻意保留）

* 冻结 tick 不会冻结**客户端**；客户端 vantage 的快照是视角，不是真相。
* 命令长度：极大的实体 NBT 在通过玩家连接还原时可能超限；实验室通过服务端控制台还原，余量大得多。
* 不做 mixin、不注册新内容：这个协议只观测与复现，不改变游戏行为。

## 在真实世界上的验证（26.2、单机、集成服务端）

| 步骤 | 结果 |
| --- | --- |
| 同一进程里的客户端与服务端 vantage | 端口 25580（客户端）与 25581（集成服务端） |
| `SNAPSHOT 64 compact` | 521 个实体、610 KB、顺序哈希 `a600f3f3ab41890c`，同一会话内二次取样一致 |
| `mc_fork live-fork-01` | 拷贝 82 个文件、47.5 MB，跳过 `players/` 与 `session.lock`，世界里有 56 个 `.mca` 位于 `dimensions/` |
| `fork_verify.py inspect` | 521 条记录、类型直方图、顺序哈希与 meta 一致、无校验错误 |
| 把某个实体的 NBT 交给游戏自己的解析器 | `data modify storage mcagent:probe entity set value <nbt>` 成功，读回完全一致，随后删除——紧凑 SNBT 可以直接 summon |

## 把一次分叉还原进实验室：能跑通的配方

```bash
tools/lab_server.py provision --name <lab> --world <fork>/world --fabric-api --carpet \
    --mod-jar <mc-agent-interface.jar> --java <java25>
tools/lab_server.py start --name <lab> --wait 300
# 接到实验室自己的服务端 vantage（端口在它的 mc-agent-server/port.txt）
mc-bridge run --api-port 8766 --port-file labs/<lab>/mc-agent-server/port.txt

# 确定性的一炉
exec "gamerule spawn_monsters false"      # 26.2 把 gamerule 改成了 snake_case：
exec "gamerule random_tick_speed 0"       # doMobSpawning -> spawn_monsters，等等
exec "gamerule advance_time false"
exec "difficulty peaceful"

# 清掉拷贝来的区块仍带着的实体——在游戏里清，然后落盘
exec "kill @e[type=!player]"              # 反复执行，直到数量不再下降
exec "save-all flush"
exec "tick freeze"

tools/fork_verify.py restore <fork> --apply --api-port 8766   # 会自己 force-load 录制范围
tools/fork_verify.py check   <fork> --api-port 8766 --radius 0
```

这次运行教给我们的，除了"哈希对上了"：

| 发现 | 后果 |
| --- | --- |
| 无头服务器没有玩家，所以**没有区块被加载** | 从录制推导出 `forceload add <范围>` 是还原的一部分，不是优化 |
| 半径是**从玩家**量起的 | 实验室取样要"该关卡所有在 tick 的实体"（`--radius 0`） |
| 实体数据同时活在 **region 文件**里，不只是 `entities/*.mca` | 只在拷贝里剥掉实体目录不够；实验室要在游戏内清场并落盘 |
| 在**冻结**状态下 kill 会留下"濒死但仍在"的实体 | 运行时 kill、`save-all flush`、再冻结 |
| 26.2 改了 gamerule 名字（`spawn_monsters`、`random_tick_speed`、`advance_time`） | 旧的驼峰名会报 "Incorrect argument" |
| 乘客是在载具内部被重建的 | 还原会跳过它；当载具与乘客在记录里相邻时，顺序依然成立 |

## 结果

```
fork: 523 records, hash dfc0a562d5395486, tick 9825
lab : 523 records, hash dfc0a562d5395486, tick 860
  minecraft:sulfur_cube       467 / 467
  minecraft:minecart           53 /  53
  minecraft:trader_llama        2 /   2
  minecraft:wandering_trader    1 /   1
orderHash: MATCH   counts: MATCH   result: MATCH
```

实体 tick 顺序——存档唯一不记录、也正是整条轨道存在理由的那个东西——在隔离的无头实例里**完全一致地回来了**。
