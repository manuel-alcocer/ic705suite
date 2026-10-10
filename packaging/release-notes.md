## Install

**Linux** (x86_64, no Python needed): install, or upgrade to the latest version, with

```bash
curl -fsSL https://github.com/manuel-alcocer/ic705suite/releases/latest/download/ic705suite-install.sh | bash
```

or download `ic705suite-*-linux-x86_64.tar.gz`, unpack it and run `./install.sh`. Then
`install.sh --upgrade` upgrades and `install.sh --uninstall` removes it (`--purge` deletes its
settings and data too; hamrlog's logbook is left alone). The bare executable is
`ic705suite-linux-x86_64`.

**With Python 3.11 or newer**: `uv tool install` or `pipx install` the `.whl`
(add `[postgres]` for PostgreSQL).

Check the downloads with `SHA256SUMS.txt`.
