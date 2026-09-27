# Documentation development

This repository contains cross-project documentation, the portable Toolkit Skill,
RFC history and standalone experiment utilities. The mod and Toolkit code live
in their [own repositories](concepts.md).

The website uses MkDocs Material and `mkdocs-static-i18n`. Edit English pages
under `docs/` and their Chinese counterparts under `docs/zh/`. Keep the README,
`README.zh.md` and both homepages aligned on installation and capabilities.

## Preview locally

Clone this documentation repository and run the commands from its root:

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

Open [the local preview](http://127.0.0.1:8000/mc-agent/) and switch languages to check both
versions. Check the homepage and installation guide at desktop and narrow widths.

## Validate and publish

Stop the preview with Ctrl+C, then run the appropriate command:

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

Navigation and language labels live in `mkdocs.yml`. Keep deeper protocols and
runbooks in [Advanced guides](advanced.md); the homepage introduces capabilities,
installation and architecture. The Pages workflow builds and deploys on relevant
pushes to `main`. Local builds write to the ignored `site/` directory.
