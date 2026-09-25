# Lab smoke mod

A minimal, generic Fabric mod used to verify the lab mod-deployment path:
build with `tools/build_mod.py`, copy with `lab_server.py provision --mod-jar`
(or `--test-mod`), restart the lab, read the evidence from `labs/<lab>/audit/`.

It contains no ROM, minecart, or other use-case logic. It exists to prove that

* a self-built jar compiles against the jars a lab already downloaded,
* a same-size rebuild is deployed (the `BUILD` marker changes),
* Fabric loads exactly the deployed build at server start,
* load evidence and command samples are readable outside the game.

```powershell
python tools/build_mod.py --source examples/smoke-mod --lab lab-a `
    --out labs/build/smoke-mod.jar --version 0.1.0 --compression store
python tools/lab_server.py provision --name lab-a --void --fabric-api `
    --mod-jar labs/build/smoke-mod.jar
python tools/lab_server.py start --name lab-a --wait 300
python tools/lab_server.py exec --name lab-a "mcagent-smoke status"
python tools/lab_server.py exec --name lab-a "mcagent-smoke sample sm1"
```

The mod reads its identity from system properties that `lab_server.py start`
sets for every lab (`mcagent.labName`, `mcagent.labInstance`, `mcagent.auditDir`,
`mcagent.worldDir`); it never guesses which lab it is in. The generic build
guide is [docs/mod-building.md](../../docs/mod-building.md).
