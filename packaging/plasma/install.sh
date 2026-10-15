#!/usr/bin/env bash
#
# Installs (or upgrades) the ic705suite logbook applet for KDE Plasma 6, for this user:
#
#   ./install.sh               install or upgrade it
#   ./install.sh --uninstall   remove it
#
# Then: right click on the panel or the desktop > Add widgets > «ic705suite logbook».
# It needs an ic705suite with --logbook-json (0.21 or later) in the PATH, or its path set
# in the applet's settings.
set -euo pipefail

ID="org.ic705suite.logbook"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "--uninstall" ]]; then
    kpackagetool6 -t Plasma/Applet --remove "$ID"
    rm -f "${XDG_DATA_HOME:-$HOME/.local/share}"/locale/*/LC_MESSAGES/"plasma_applet_$ID.mo"
    exit 0
fi

command -v kpackagetool6 >/dev/null || { echo "kpackagetool6 not found: is this Plasma 6?" >&2; exit 1; }

# The package with its translations compiled in.
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
cp -r "$HERE/$ID" "$BUILD/"
if command -v msgfmt >/dev/null; then
    # In the package, and where KDE looks for every program's translations too.
    LOCALE="${XDG_DATA_HOME:-$HOME/.local/share}/locale"
    for po in "$HERE"/po/*.po; do
        lang="$(basename "$po" .po)"
        mkdir -p "$BUILD/$ID/contents/locale/$lang/LC_MESSAGES" "$LOCALE/$lang/LC_MESSAGES"
        msgfmt -o "$BUILD/$ID/contents/locale/$lang/LC_MESSAGES/plasma_applet_$ID.mo" "$po"
        cp "$BUILD/$ID/contents/locale/$lang/LC_MESSAGES/plasma_applet_$ID.mo" \
           "$LOCALE/$lang/LC_MESSAGES/"
    done
else
    echo "msgfmt not found (gettext): the applet will be in English" >&2
fi

if kpackagetool6 -t Plasma/Applet --show "$ID" >/dev/null 2>&1; then
    kpackagetool6 -t Plasma/Applet --upgrade "$BUILD/$ID"
else
    kpackagetool6 -t Plasma/Applet --install "$BUILD/$ID"
fi
echo "Installed. Add it with: right click on the panel > Add widgets > ic705suite logbook."
echo "Already on the panel? It takes the new version after: plasmashell --replace &"
