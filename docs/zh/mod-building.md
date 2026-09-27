# 为实验室构建 mod

智能体可能想把自己的观测代码放进游戏里——一个 logger、一个 tick 采样器、一条命令。在实验室里这是一个两步循环：对着实验室已经有的 jar **构建**，然后交给 `lab_server.py` **部署**并重启。Minecraft 26.2 的类文件没有混淆，所以不需要 Gradle，也不需要下载映射；`tools/build_mod.py` 直接用 `javac` 编译。

## 前置条件

* **一个至少启动过一次的实验室。** 第一次启动会让 Fabric 启动器把服务端 jar 解到 `labs/<lab>/versions/<mc>/server-<mc>.jar` 并安装依赖库；这些文件就是编译 classpath。
* **带 `javac` 的 JDK 25。** 可以用 `--jdk` 传 JDK home，或在 provision 时用 `--jdk PATH` 记录下来，也可以走 `JAVA_HOME` 或 `PATH`。实验室自己的 `java` 已经记在 `lab.json` 里，所以通常只需要多传 `--jdk`。纯 JRE 不能编译。
* 构建**不需要联网**：一切都来自 `labs/<lab>/` 和 `labs/_cache/`。

## 源码结构

```text
my-mod/
  src/main/java/...            Java 源码
  src/main/resources/
    fabric.mod.json            mod 元数据（必需）
    assets/...                 其他资源，会原样拷进 jar
```

`fabric.mod.json` 至少要写清楚 entrypoint 和 environment，例如：

```json
{
  "schemaVersion": 1,
  "id": "example-logger",
  "version": "${version}",
  "name": "Example Logger",
  "environment": "*",
  "entrypoints": { "main": ["dev.example.logger.LoggerMod"] },
  "depends": {
    "fabricloader": ">=0.19.0",
    "minecraft": "*",
    "fabric-api": "*",
    "java": ">=25"
  }
}
```

打包时 `${version}` 会被 `--version` 替换。`depends` 里只写实验室确实装了的依赖；只要声明的依赖缺失，Fabric 就会拒绝启动——这对可审计实例来说正是想要的行为。

服务端 mod 用 `"environment": "*"`；如果它绝不能在客户端加载，就用 `"server"`。实验室是独立服务端，entrypoint 走 `main` 阶段（`net.fabricmc.api.ModInitializer`）。

## 构建

```powershell
python tools/build_mod.py --source examples/smoke-mod --lab lab-a `
    --out labs/build/smoke-mod.jar --version 0.1.0 --compression store
```

输出：

```text
building examples\\smoke-mod for lab lab-a (minecraft 26.2)
  javac C:\\...\\bin\\javac.exe (javac 25.0.1)
  javac @...\\javac.args (1 sources, 87 classpath entries)
built labs\\build\\smoke-mod.jar (14.8 KB, sha256 e66ebdb16d53238a...)
  version 0.1.0, compression store, release 25
  87 classpath entries from lab lab-a
  metadata labs\\build\\smoke-mod.jar.build.json
```

classpath 按这个顺序从实验室拼出来：

1. `labs/<lab>/versions/<mc>/server-<mc>.jar`——解包后的、未混淆的 Minecraft 服务端类；
2. `labs/<lab>/libraries/` 下的每个 jar——Fabric loader、mixin、Brigadier、Gson 以及服务端的其他依赖库；
3. `labs/<lab>/.fabric/processedMods/` 下的每个 jar——运行中的服务端实际会加载的 Fabric API 模块；
4. `labs/<lab>/mods/` 里的每个 jar——这样 mod 可以对着 Carpet 或其他已安装 mod 编译；
5. `--classpath-extra` 传入的任何东西。

全部用 `--release 25` 编译，并由同一个工具打包。jar 是确定性的：条目排序、时间戳固定，默认 `--compression store` 让等长修改保持等长。相同源码重复构建得到逐字节相同的结果，改一个字符串就会改变哈希。旁边的 `<out>.build.json` 记录了 jar 哈希与大小、JDK 与 javac 版本、服务端 jar 哈希、classpath 摘要和每个源码文件的哈希——请把它和实验审计一起保存。

## 部署并读取结果

Fabric 只在服务端启动时加载 mod，运行中永远不会。停止后再部署，然后启动：

```powershell
python tools/lab_server.py stop --name lab-a
python tools/lab_server.py provision --name lab-a --mod-jar labs/build/smoke-mod.jar
python tools/lab_server.py start --name lab-a --wait 300
python tools/lab_server.py exec --name lab-a "mcagent-smoke status"
python tools/lab_server.py exec --name lab-a "mcagent-smoke sample first"
```

`provision` 按 SHA-256 比较 jar 与已部署文件。报 `replaced` 说明字节确实换了（哪怕大小没变）；报 `unchanged` 说明字节确实一样。它拒绝把 mod 部署进运行中的实验室。`start` 会把磁盘上的哈希写进 `run.json`（`modsAtStart`），让这次运行与确切的构建绑定。

游戏侧 mod 把证据写到实验室指定的地方：`start` 会传 `-Dmcagent.auditDir=...`（以及 `-Dmcagent.labName`、`-Dmcagent.labInstance`、`-Dmcagent.worldDir`、`-Dmcagent.serverDir`、`-Dmcagent.serverPort`），mod 不必猜自己在哪个实验室。要读的结果在 `labs/<lab>/audit/` 和控制台日志 `labs/<lab>/logs/console.log`。

## smoke mod

`examples/smoke-mod/` 是一个刻意保持通用的观测 mod：加载时打印 `[mc-agent-smoke] loaded build=... lab=...`，服务端 ready 时写 `audit/start.json` 和一行 `smoke.log`，并注册两条控制台命令（`mcagent-smoke status`、`mcagent-smoke sample <label>`）。它不含 ROM 或任何用例逻辑——它存在的意义是验证构建/部署/重启这条路径。把真实 logger 建在它上面即可。

## 构建或加载失败时

工具从不隐藏编译器输出。缺少符号时是：

```text
...\\SmokeMod.java:51: error: cannot find symbol
    private static final Object BROKEN = doesNotExist();
                                         ^
  symbol:   method doesNotExist()
error: build failed: javac exited 1; the compiler output above is the error
```

加载失败会出现在服务端日志和 `start` 的失败输出里。缺依赖时 Fabric 会打印清晰的块：

```text
[main/ERROR]: Incompatible mods found!
  Fix: add [add:mc-agent-absent-mod 1 (( -∞,∞ ))], remove [], replace []
  Mod 'MC Agent Lab Smoke Mod' (mc-agent-lab-smoke) 0.1.0 requires mc-agent-absent-mod ...
```

entrypoint 写错时：

```text
java.lang.ClassNotFoundException: dev.mcagent.smoke.AbsentEntrypoint
```

`lab_server.py start` 以非零退出，打印日志尾部，并把看起来像错误的行（`exception`、`failed`、`missing`、`requires`、`incompatible`）加上 `!!` 前缀重复一遍。如果实验室把某个 mod 标记为必需（`--test-mod` 或 `--require-mod`），该 jar 缺失时 `start` 会直接拒绝，坏掉的审计环境不可能被当成干净运行。

## 限制

* `build_mod.py` 只编译服务端 mod。实验室里只有服务端 jar；客户端 mod 必须对着已安装的客户端实例构建（interface mod 自己的 `build.py` 就是例子）。
* 工具对着**实验室当前记录的状态**构建。如果 Minecraft、loader 或某个 mod 升级了，先用新 jar 重新 provision 再构建。
* 没有 Gradle/loom 式的资源处理；`fabric.mod.json` 和普通资源只是被拷贝（带 `${version}` 替换）。
* 实验室构建不做 remap，这对未混淆的 26.2 服务端是正确的；更老的、混淆过的 Minecraft 需要 intermediary 映射和另一条构建路径。
