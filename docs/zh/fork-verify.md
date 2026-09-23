# 分叉校验：实验室复现了活世界吗？

存档里有方块和实体的 NBT，但没有**关卡遍历实体的顺序**，而这个顺序决定了任何"逐个实体计算"的结果。所以"分叉一个活的世界"只有在**还原后的世界按记录的顺序 tick 实体**时才有意义。[protocol-snapshot.md](protocol-snapshot.md) 定义了快照是什么；`tools/fork_verify.py` 是这份契约的**消费侧**：它读取分叉目录、通过 loopback API 驱动 bridge 守护进程，并判定一次还原是否忠实。它不采信游戏的说法——结论来自文件，以及从磁盘上读回来的新快照。

## 它读什么

分叉目录就是 `mc_fork` 拷贝出来的东西：区域文件加上 `meta.json` 和 `entities.jsonl`。工具只需要后两个。

| 输入 | 用途 |
| --- | --- |
| `meta.json`：`protocol`、`entities`、`orderHash` | 校验：协议版本为 1、记录条数、以及文件必须复现的哈希 |
| `meta.json`：`radius` | `check` 里新快照会问同一个半径（0 或缺失 = 该关卡在 tick 的所有实体） |
| `meta.json`：`tick`、`dimension` | 仅用于定位；只打印，从不比对 |
| `entities.jsonl`：行的顺序 | tick 顺序——整条轨道存在的意义所在 |
| `entities.jsonl`：`uuid` | `orderHash`、重复检测，以及两个快照之间匹配实体 |
| `entities.jsonl`：`type`、`pos`、`nbt` | `restore` 用的 `/summon` 行 |
| `entities.jsonl`：`pos`、`vel` | `diff` 里的位置与速度差值 |

`orderHash` 是那个能证明"还原复现了顺序"的单一数值：`sha256(join(":", 按 tick 顺序的 uuids))[:16]`。工具会从文件重新算一遍，**从不**信任 `meta.json` 里那份副本。

## 子命令

| 命令 | 作用 | 退出码 |
| --- | --- | --- |
| `inspect <forkDir>` | 打印 meta 字段、记录数、类型直方图、重算的哈希，以及全部校验错误 | 0 有效，1 无效 |
| `restore <forkDir>` | 按记录顺序打印 `/summon` 行（演练，默认行为） | 0；若记录无法变成命令则 1 |
| `restore <forkDir> --apply --api-port 8765` | 通过 bridge 逐条发出，每 100 条报进度 | 0；任一条失败则 1 |
| `check <forkDir> --api-port 8765 [--name <tmp>]` | 要求一份新快照，比对哈希与各类型计数 | 0 MATCH，1 MISMATCH |
| `diff <forkDirA> <forkDirB>` | 顺序哈希、各类型计数、首个分歧下标、最大的位置/速度差 | 0 相同，1 不同 |
| `selftest` | 在临时目录里造合成快照，对着假 bridge 校验以上全部 | 0 通过，1 失败 |

`inspect` 和 `diff` 是离线的：只碰文件系统。`restore --apply` 和 `check` 需要 bridge；两者都会说明自己试了哪个端口，那里没有守护进程就退出码 2。

```bash
python tools/fork_verify.py inspect <forkDir>
python tools/fork_verify.py diff    <forkDirA> <forkDirB>     # 两次可对比的实验
python tools/fork_verify.py selftest                          # 不需要 bridge，也不需要游戏
```

演练的输出是 stdout 上一行一条命令、stderr 上一行摘要，所以它同时也是一个函数文件：

```bash
python tools/fork_verify.py restore <forkDir> > restore.mcfunction
python tools/fork_verify.py restore <forkDir> --from 500 --limit 100
```

## 真正跑起来的顺序

和工具链其余部分完全一致：bridge 持有唯一一条通向游戏的连接，而这个工具只是它的又一个 loopback 客户端。

```bash
# 0. 游戏侧（进程内冻结、方块已拷贝）：由 bridge 的 fork 工具完成
mc-bridge call fork '{"name": "before", "radius": 64}'

# 1. 我们录到了什么？（离线；文件自相矛盾就退出 1）
python tools/fork_verify.py inspect <forkDir>

# 2. 把它逐条 /summon 还原进隔离的实验室服务器
python tools/fork_verify.py restore <forkDir>                # 先看一眼
python tools/fork_verify.py restore <forkDir> --apply --api-port 8765

# 3. 验收测试：对实验室重新取样，和录制比对
python tools/fork_verify.py check <forkDir> --api-port 8765

# 4. 同一实验的两次运行，逐实体对比
python tools/fork_verify.py diff <forkDirA> <forkDirB>
```

`check` 会把新快照留在磁盘上（名字取自 `--name`，否则是 `forkverify-<UTC 时间戳>`），方便事后检查失败的那次，并打印它读取的路径。

## 怎么读 `check`

```
fork: <forkDir>  (521 records, hash 9f2c1d6a8b0e4f37, tick 104233, minecraft:overworld)
live: <snapshotDir>  (521 records, hash 9f2c1d6a8b0e4f37, tick 20, minecraft:overworld)
  type                   fork  live  delta
  ---------------------  ----  ----  -----
  minecraft:sulfur_cube   512   512      0
  minecraft:item            8     8      0
  minecraft:cow             1     1      0
  total                   521   521      0
orderHash: MATCH
counts: MATCH
result: MATCH
```

`orderHash` 是验收标准：只有实验室按同样顺序 tick 同样的实体，它才可能匹配。`counts` 抓的是另一半——还原把正确的实体放进了正确的顺序，却给其中一个换了类型，这是哈希看不见的。**任何带校验问题的分叉目录永远不可能返回 MATCH**，因为一份无法校验的录制不是证据。

## 怎么读 `diff`

`diff` 回答的是实验问题（"B 次运行和 A 次差在哪、差多少？"），而不是还原问题：

* `order: first divergent index N` —— tick 顺序第一次不同的位置，以及两边各自在那里的 uuid。N 之前全都一致。
* `orderHash: MATCH|MISMATCH` 加上各类型计数。
* 两次运行都存在的 uuid 中，最大的位置差与速度差，每行带 uuid 与类型。`--top N` 控制行数（0 = 全部）。

退出码 1 意味着"不相同"，这是**发现**，不是错误：两次实验本来就该不一样。工具会把全零的差值表压成一行，这样 500 个实体的对比依然可读。

## 这个工具做过的决定，以及当初含糊的地方

1. **行的顺序就是 tick 顺序。** `order` 字段会与它所在的行核对（`order` 必须等于从 0 开始的**行号**，且取值集合必须正好是 `0..n-1`），但还原跟随的是**行**，不是字段。协议把文件描述为"一行一个 JSON 对象，按 tick 顺序"；一份自相矛盾的文件会被报告出来，而不是被悄悄重排。
2. **演练是默认行为。** 必须显式 `--apply` 才会碰游戏，同时提供 `--dry-run` 让脚本能表明意图。工具只打印时不发送任何东西。
3. **还原走 `command`，不走 `restore`。** bridge 有一个整文件级的 `restore` 工具，但这个工具每次 bridge 调用只发一条 `/summon`，这样 `--from`、`--limit`、每 100 条进度和逐条失败处理才都有意义。客户端类仍然按契约的名称包装 `snapshot`、`snapshots`、`fork`、`restore`、`order`（自测会断言方法名和参数形状）。
4. **`check` 从磁盘读新快照。** bridge 的 `snapshot` 返回一个目录，而各类型计数需要实体本身——所以这个工具假定了工具链其余部分也假定的事：bridge 和游戏都跑在运行此工具的机器上。如果实验室把快照目录挪到了读不到的地方，那是**硬错误**，不会悄悄降级成只比哈希。
5. **`check` 用分叉自己的半径。** 带半径的录制会和带半径的新快照比对；只有 `radius <= 0` 或缺失半径才问"所有实体"。半径是从玩家量起的（客户端 vantage），所以一个把实体还原到别的参考点周围的实验室会表现为计数不匹配——这是诚实的，不是噪声。
6. **`nbt` 是校验必需、还原容忍的。** 没有 `nbt` 的记录是校验错误（协议规定 `nbt` 就是 `/summon` 接受的东西），而 `restore` 会打印警告并在没有它的情况下 summon——实体出现，内存态丢失，所以对这条不能声称"忠实还原"。
7. **还原只拒绝它无法处理的东西。** `type`/`pos` 缺失或畸形、或某行无法解析，会中止整次运行；顺序、uuid、哈希的问题只是警告，因为这些行仍然可以 summon。`inspect` 永远展示完整列表。
8. **每条顶层记录一条命令，乘客也算。** 协议为关卡 tick 的每个实体写一行，所以还原就按文件顺序发出这些行，对 `passengers` 或 `vehicle` 不做任何"聪明"处理；连接关系是 NBT 的职责，在这一侧去重反而会破坏顺序保证。
9. **计数按记录原样比对**，`minecraft:cow` 和 `cow` 是两个不同的类型。mod 在两个 vantage 上都写完全限定名，而掩盖拼写差异只会掩盖真 bug。
10. **`--limit 0` 表示"剩下的全部"**，`--from` 是记录顺序里从 0 开始的下标；`--from` 超出末尾会什么都不打印并退出 0。
11. **缺 `vel` 不是错误。** `pos`/`vel`/姿态只是 `nbt` 里的便利副本，所以没有 `vel` 的快照依然有效；它只是没有可对比的东西，`diff` 会这么说，而不是编造零值。
12. **带校验问题的分叉永远无法通过 `check`**，即使活的那一侧与它相符。拿一份和自身元数据矛盾的文件去比对，什么也证明不了。
13. **由 `--api-port` 选择实例，不使用 `target`。** bridge 的 `restore` 和 `order` 接受可选的 `target`，客户端类会把它透传，但 CLI 到达实验室的方式和工具链其余部分一致：把 `--api-port` 指向持有那个实例的守护进程。

## 自测

```bash
python tools/fork_verify.py selftest
```

它会在临时目录下造七个合成快照（一对完全相同、一对交换了两个实体、一对其中某个实体移动了 0.5 格、一个类型变了、一个记录的哈希在撒谎、一个故意损坏），通过一个形状与 `LocalApiClient` 相同的假传输提供服务，并断言 `inspect`、`restore`、`check`、`diff` 的报告内容——包括退出码、打印出的 `/summon` 行、分歧下标、0.5 格的差值、各类型表，以及客户端发出的 bridge 方法名与参数。不需要 bridge、不需要游戏、不需要真实的快照：这是整条轨道里能在笔记本上验证的部分。

## 这个工具**不是**什么

* 它从不启动、停止或冻结 Minecraft，也从不拷贝文件：那是 mod 和 bridge 的事。
* 它不算物理，也不比对**方块**。`diff` 比对的是实体；区域文件是 bridge 的事，方块级对比会是另一个工具。
* 它不判断差异意味着什么。它报告首个分歧和最大差值；是不是一个发现，由实验决定。
