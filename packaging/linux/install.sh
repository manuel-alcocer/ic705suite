#!/usr/bin/env bash
#
# Linux installer for ic705suite: installs, upgrades and uninstalls the
# self-contained executable published with each release. Needs no Python.
#
#   ./install.sh                      install the ic705suite next to this script,
#                                     or download the latest release
#   ./install.sh --upgrade            download and install the latest release
#   ./install.sh --uninstall          remove it; settings and data stay
#   ./install.sh --uninstall --purge  remove it along with its settings and data
#
# Straight from the web:
#   curl -fsSL https://github.com/manuel-alcocer/ic705suite/releases/latest/download/ic705suite-install.sh | bash
#
set -euo pipefail

APP="ic705suite"
PLATFORM="linux-x86_64"
RELEASES_URL="${IC705SUITE_RELEASES_URL:-https://github.com/manuel-alcocer/ic705suite/releases}"

# Messages follow the locale, like the suite does.
case "${IC705SUITE_LANG:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}" in
    es*) SPANISH=1 ;;
    *) SPANISH=0 ;;
esac

# Prints the English or the Spanish text.
t() { if [[ $SPANISH == 1 ]]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    if [[ $SPANISH == 1 ]]; then
        cat <<'EOF'
Uso: install.sh [opciones]

  (sin opciones)     Instala el ic705suite que acompaña al script o, si no hay
                     ninguno, descarga la última versión. Si ya hay una
                     instalada más antigua, la actualiza.
  --upgrade          Descarga la última versión y actualiza la instalada.
  --uninstall        Desinstala ic705suite. La configuración y los datos se quedan.
  --purge            Desinstala y borra también la configuración y los datos
                     de la suite (el diario de hamrlog no se toca).
  --status           Muestra la versión instalada.
  --version X.Y.Z    Descarga esa versión en lugar de la última.
  --system           Instala para todos los usuarios en /usr/local (con sudo).
  --prefix DIR       Instala en DIR (por defecto ~/.local).
  --force            Reinstala la misma versión, permite volver a una anterior
                     o sustituye un ic705suite instalado por otro medio.
  -y, --yes          No pide confirmación antes de borrar datos.
  -h, --help         Muestra esta ayuda.
EOF
    else
        cat <<'EOF'
Usage: install.sh [options]

  (no options)       Install the ic705suite shipped with this script or, when
                     there is none, download the latest release. An older
                     installed version is upgraded.
  --upgrade          Download the latest release and upgrade to it.
  --uninstall        Uninstall ic705suite. Its settings and data stay.
  --purge            Uninstall and delete the suite's settings and data as
                     well (hamrlog's logbook is left alone).
  --status           Show the installed version.
  --version X.Y.Z    Download that version instead of the latest.
  --system           Install for every user in /usr/local (with sudo).
  --prefix DIR       Install under DIR (default ~/.local).
  --force            Reinstall the same version, go back to an older one or
                     replace an ic705suite installed by other means.
  -y, --yes          Do not ask before deleting data.
  -h, --help         Show this help.
EOF
    fi
}

action="install"
prefix=""
system=0
pinned_version=""
purge=0
force=0
assume_yes=0

while (($#)); do
    case "$1" in
        --upgrade) action="upgrade" ;;
        --uninstall) action="uninstall" ;;
        --purge) action="uninstall"; purge=1 ;;
        --status) action="status" ;;
        --system) system=1 ;;
        --prefix)
            [[ $# -ge 2 ]] || die "$(t "--prefix needs a directory" "--prefix necesita un directorio")"
            prefix="$2"; shift ;;
        --prefix=*) prefix="${1#*=}" ;;
        --version)
            [[ $# -ge 2 ]] || die "$(t "--version needs a number" "--version necesita un número")"
            pinned_version="${2#v}"; shift ;;
        --version=*) pinned_version="${1#*=}"; pinned_version="${pinned_version#v}" ;;
        --force) force=1 ;;
        -y|--yes) assume_yes=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "$(t "Unknown option: $1 (see --help)" "Opción desconocida: $1 (mira --help)")" ;;
    esac
    shift
done

if [[ -z $prefix ]]; then
    if ((system)); then prefix="/usr/local"; else prefix="$HOME/.local"; fi
fi
if ((system)) && [[ $EUID -ne 0 ]]; then
    die "$(t "--system needs root: run it with sudo" "--system necesita ser root: ejecútalo con sudo")"
fi

BIN_DIR="$prefix/bin"
BIN="$BIN_DIR/$APP"
DESKTOP_DIR="$prefix/share/applications"
DESKTOP="$DESKTOP_DIR/$APP.desktop"
# What this installer put on disk, so uninstalling removes exactly that.
STATE_DIR="$prefix/lib/$APP"
MANIFEST="$STATE_DIR/manifest"
VERSION_FILE="$STATE_DIR/version"

# The same folders as ic705suite.paths, for --purge. hamrlog's logbook lives in
# hamrlog's own folders, shared with a standalone hamrlog, and is not touched.
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$APP"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/$APP"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/$APP"

# A binary next to the script means the script came inside a release tarball.
SCRIPT_DIR=""
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

installed_version() {
    if [[ -f $VERSION_FILE ]]; then cat "$VERSION_FILE"; fi
}

# "ic705suite 0.2.0" -> "0.2.0"; empty when the binary does not run here.
version_of() {
    "$1" --version 2>/dev/null | awk '{print $2}' || true
}

# True when $1 is an older version than $2.
version_lt() {
    [[ $1 != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" == "$1" ]]
}

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$1"
    else
        die "$(t "curl or wget is needed to download ic705suite" "Hace falta curl o wget para descargar ic705suite")"
    fi
}

write_desktop_entry() {
    mkdir -p "$DESKTOP_DIR"
    cat >"$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=ic705suite
GenericName=Amateur radio station suite
GenericName[es]=Estación de radioaficionado
Comment=Rig control, CW, antenna, logbook and packet for the Icom IC-705, in the terminal
Comment[es]=Control del equipo, CW, antena, diario y packet para el Icom IC-705, en modo texto
Exec="$BIN"
Icon=utilities-terminal
Terminal=true
Categories=Network;HamRadio;
Keywords=ham;radio;ic-705;icom;cw;swr;logbook;packet;
EOF
}

refresh_desktop_database() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
    fi
}

# Installs the executable at $1: fresh install, upgrade or reinstall.
install_binary() {
    local source="$1" new current
    new="$(version_of "$source")"
    [[ -n $new ]] || die "$(t "$source does not run on this system" "$source no arranca en este sistema")"
    current="$(installed_version)"

    if [[ -n $current ]]; then
        if [[ $current == "$new" ]] && ! ((force)); then
            info "$(t "ic705suite $new is already installed in $BIN" "ic705suite $new ya está instalado en $BIN")"
            return 0
        fi
        if version_lt "$new" "$current" && ! ((force)); then
            die "$(t "ic705suite $current is installed, newer than $new. Use --force to go back." \
                     "Está instalado ic705suite $current, más nuevo que $new. Usa --force para volver atrás.")"
        fi
        if [[ $current == "$new" ]]; then
            info "$(t "Reinstalling ic705suite $new" "Reinstalando ic705suite $new")"
        else
            info "$(t "Upgrading ic705suite from $current to $new" "Actualizando ic705suite de $current a $new")"
        fi
    else
        if [[ -e $BIN || -L $BIN ]] && ! ((force)); then
            die "$(t "$BIN already exists and was not installed by this script (perhaps by uv tool or pipx). Remove it first or use --force." \
                     "$BIN ya existe y no lo instaló este script (quizá uv tool o pipx). Quítalo antes o usa --force.")"
        fi
        info "$(t "Installing ic705suite $new in $BIN" "Instalando ic705suite $new en $BIN")"
    fi

    mkdir -p "$BIN_DIR" "$STATE_DIR"
    # Copy, then rename: a running ic705suite keeps its old file and nobody
    # sees a half-written one.
    install -m 755 "$source" "$BIN.new"
    mv -f "$BIN.new" "$BIN"
    write_desktop_entry
    printf '%s\n' "$BIN" "$DESKTOP" >"$MANIFEST"
    printf '%s\n' "$new" >"$VERSION_FILE"
    refresh_desktop_database

    info "$(t "Done: ic705suite $new" "Hecho: ic705suite $new")"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            warn "$(t "$BIN_DIR is not in PATH. Add this line to ~/.bashrc or ~/.zshrc:" \
                      "$BIN_DIR no está en el PATH. Añade esta línea a ~/.bashrc o ~/.zshrc:")"
            printf '\n    export PATH="%s:$PATH"\n\n' "$BIN_DIR"
            ;;
    esac
    # The settings are left to the user: writing a template here would stop
    # the first run from importing cwic705's.
    info "$(t "Try it without a radio:  ic705suite --sim" \
              "Pruébalo sin equipo:  ic705suite --sim --lang es")"
    info "$(t "Then set up your radio: ic705suite --init-config  (~/.config/ic705suite/config.toml)" \
              "Luego configura tu equipo: ic705suite --init-config  (~/.config/ic705suite/config.toml)")"
}

# Downloads a release, checks it against SHA256SUMS.txt and installs it.
install_release() {
    local base workdir name remote current
    if [[ -n $pinned_version ]]; then
        base="$RELEASES_URL/download/v$pinned_version"
    else
        base="$RELEASES_URL/latest/download"
    fi

    workdir="$(mktemp -d)"
    # shellcheck disable=SC2064  # expand now: workdir is local
    trap "rm -rf '$workdir'" EXIT

    info "$(t "Looking for the release in $base" "Buscando la versión en $base")"
    fetch "$base/SHA256SUMS.txt" "$workdir/SHA256SUMS.txt" ||
        die "$(t "Could not download $base/SHA256SUMS.txt" "No se pudo descargar $base/SHA256SUMS.txt")"
    name="$(grep -oE "$APP-[0-9][0-9A-Za-z.]*-$PLATFORM\.tar\.gz" "$workdir/SHA256SUMS.txt" | head -n1 || true)"
    [[ -n $name ]] || die "$(t "That release has no Linux package" "Esa versión no tiene paquete para Linux")"
    remote="${name#"$APP-"}"
    remote="${remote%"-$PLATFORM.tar.gz"}"

    current="$(installed_version)"
    if [[ $current == "$remote" ]] && ! ((force)); then
        info "$(t "ic705suite $current is already the latest version" "ic705suite $current ya es la última versión")"
        return 0
    fi

    info "$(t "Downloading $name" "Descargando $name")"
    fetch "$base/$name" "$workdir/$name" ||
        die "$(t "Could not download $base/$name" "No se pudo descargar $base/$name")"
    (cd "$workdir" && grep "  $name\$" SHA256SUMS.txt | sha256sum -c --quiet - >/dev/null 2>&1) ||
        die "$(t "$name does not match its SHA-256 sum; nothing was installed" \
                 "$name no coincide con su suma SHA-256; no se ha instalado nada")"
    tar -xzf "$workdir/$name" -C "$workdir"
    install_binary "$workdir/$APP-$remote-$PLATFORM/$APP"
}

uninstall() {
    local current
    current="$(installed_version)"
    if [[ -n $current ]]; then
        info "$(t "Uninstalling ic705suite $current from $prefix" "Desinstalando ic705suite $current de $prefix")"
        while IFS= read -r file; do
            [[ -n $file ]] && rm -f -- "$file"
        done <"$MANIFEST"
        rm -rf -- "$STATE_DIR"
        refresh_desktop_database
    elif ((purge)); then
        warn "$(t "ic705suite is not installed in $prefix; deleting its data only" \
                  "ic705suite no está instalado en $prefix; solo se borran sus datos")"
    else
        die "$(t "ic705suite is not installed in $prefix (installed with --system or --prefix?)" \
                 "ic705suite no está instalado en $prefix (¿se instaló con --system o --prefix?)")"
    fi

    if ! ((purge)); then
        info "$(t "Done. Your settings stay in $CONFIG_DIR and your data in $DATA_DIR" \
                  "Hecho. Tu configuración sigue en $CONFIG_DIR y tus datos en $DATA_DIR")"
        return 0
    fi

    if ! ((assume_yes)); then
        local answer=""
        printf '%s ' "$(t "Delete $CONFIG_DIR and $DATA_DIR, with the CW channels and the antenna history? [y/N]" \
                          "¿Borrar $CONFIG_DIR y $DATA_DIR, con los canales de CW y el historial de antenas? [s/N]")"
        read -r answer </dev/tty ||
            die "$(t "No terminal to confirm; add --yes" "No hay terminal para confirmar; añade --yes")"
        case "$answer" in
            [yYsS]*) ;;
            *) info "$(t "Data kept in $DATA_DIR" "Datos conservados en $DATA_DIR")"; return 0 ;;
        esac
    fi
    rm -rf -- "$CONFIG_DIR" "$DATA_DIR" "$CACHE_DIR" "$LOG_DIR"
    info "$(t "Data deleted (hamrlog's logbook was left alone)" \
              "Datos borrados (el diario de hamrlog no se ha tocado)")"
}

case "$action" in
    status)
        current="$(installed_version)"
        if [[ -n $current ]]; then
            echo "ic705suite $current ($BIN)"
        else
            echo "$(t "ic705suite is not installed in $prefix" "ic705suite no está instalado en $prefix")"
            exit 1
        fi
        ;;
    uninstall)
        uninstall
        ;;
    upgrade)
        [[ -n $(installed_version) ]] ||
            die "$(t "ic705suite is not installed in $prefix; run without --upgrade to install it" \
                     "ic705suite no está instalado en $prefix; ejecuta sin --upgrade para instalarlo")"
        install_release
        ;;
    install)
        if [[ -z $pinned_version && -n $SCRIPT_DIR && -f $SCRIPT_DIR/$APP && -x $SCRIPT_DIR/$APP ]]; then
            install_binary "$SCRIPT_DIR/$APP"
        else
            install_release
        fi
        ;;
esac
