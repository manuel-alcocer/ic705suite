/*
    SPDX-License-Identifier: MIT
*/

import QtQuick
import QtQuick.Controls as QQC2

import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    property alias cfg_command: command.text
    property alias cfg_refreshMinutes: refreshMinutes.value
    // Kept as they are: chosen in the applet itself.
    property string cfg_band
    property string cfg_mode

    Kirigami.FormLayout {
        QQC2.TextField {
            id: command
            Kirigami.FormData.label: i18n("Command:")
            placeholderText: "ic705suite"
        }
        QQC2.Label {
            text: i18n("How ic705suite is run, e.g. ~/.local/bin/ic705suite.")
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }
        QQC2.SpinBox {
            id: refreshMinutes
            Kirigami.FormData.label: i18n("Refresh every (minutes):")
            from: 1
            to: 60
        }
    }
}
