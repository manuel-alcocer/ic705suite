## Install

**Linux** (x86_64, no Python needed): install, or upgrade to the latest version, with

```bash
curl -fsSL https://github.com/manuel-alcocer/ic705suite/releases/latest/download/ic705suite-install.sh | bash
```

or download `ic705suite-*-linux-x86_64.tar.gz`, unpack it and run `./install.sh`. Then
`install.sh --upgrade` upgrades and `install.sh --uninstall` removes it (`--purge` deletes its
settings and data too; hamrlog's logbook is left alone). The bare executable is
`ic705suite-linux-x86_64`.

**Windows 10/11** (x86_64, no Python needed, no administrator rights): from PowerShell,

```powershell
irm https://github.com/manuel-alcocer/ic705suite/releases/latest/download/ic705suite-install.ps1 | iex
```

or download `ic705suite-*-windows-x86_64.zip`, unpack it and run `.\install.ps1` (if
PowerShell refuses to run scripts: `powershell -ExecutionPolicy Bypass -File .\install.ps1`).
Then `install.ps1 -Upgrade` upgrades and `install.ps1 -Uninstall` removes it (`-Purge` deletes
its settings and data too; hamrlog's logbook is left alone). The bare executable is
`ic705suite-windows-x86_64.exe`.

Both installers ask for the main settings (callsign, radio address, user and password...)
after installing or upgrading, with the current ones as defaults; `ic705suite --configure`
asks again at any time.

**With Python 3.11 or newer**: `uv tool install` or `pipx install` the `.whl`
(add `[postgres]` for PostgreSQL).

Check the downloads with `SHA256SUMS.txt`. Every file is also signed with its build
provenance (made by this repository's release workflow, from this tag); with the GitHub CLI:

```bash
gh attestation verify ic705suite-linux-x86_64 --repo manuel-alcocer/ic705suite
```
