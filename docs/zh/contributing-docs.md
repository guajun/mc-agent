# 文档开发

本仓库存放跨项目文档、可移植 Toolkit Skill、RFC 历史和独立实验工具。
mod 与 Toolkit 代码位于[各自的仓库](concepts.md)。

网站使用 MkDocs Material 和 `mkdocs-static-i18n`。英文页面在 `docs/`，
对应中文页面在 `docs/zh/`。修改安装与能力介绍时，同步 README、`README.zh.md`
以及中英文首页。

## 本地预览

克隆文档仓库，在仓库根目录运行：

```bash
git clone https://github.com/guajun/mc-agent
cd mc-agent
```

=== "Windows · PowerShell"

    ```powershell
    python -m venv .venv
    .venv/Scripts/python -m pip install -r requirements-docs.txt
    .venv/Scripts/python -m mkdocs serve
    ```

=== "macOS / Linux"

    ```bash
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements-docs.txt
    .venv/bin/python -m mkdocs serve
    ```

打开[本地预览](http://127.0.0.1:8000/mc-agent/)，切换语言检查两个版本。
分别检查桌面和窄屏下的首页与安装指南。

## 校验与发布

按 Ctrl+C 停止预览，然后运行对应系统的命令：

=== "Windows · PowerShell"

    ```powershell
    .venv/Scripts/python -m mkdocs build --strict
    git diff --check
    ```

=== "macOS / Linux"

    ```bash
    .venv/bin/python -m mkdocs build --strict
    git diff --check
    ```

导航和翻译名称在 `mkdocs.yml` 中配置。详细协议和验收手册放在[进阶指南](advanced.md)，
首页专注能力、安装和架构。相关变更推送到 `main` 后，Pages workflow 会构建并部署。
本地构建输出到已被 Git 忽略的 `site/` 目录。
