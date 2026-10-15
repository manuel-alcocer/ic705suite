/*
    The ic705suite logbook in the panel: today's QSOs on the icon; open, the counters, a
    band and a mode to narrow the list by, and the latest QSOs (a click looks the call up
    on QRZ.com). The data comes from `ic705suite --logbook-json [BAND] [MODE]`, the same
    logbook the suite's clients open (the core's, when this machine is a client of it).

    SPDX-License-Identifier: MIT
*/

import QtQuick
import QtQuick.Layouts

import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    // The last answer of --logbook-json, null until the first one.
    property var log: null
    property string error: ""
    property bool loading: false

    readonly property string band: Plasmoid.configuration.band
    readonly property string mode: Plasmoid.configuration.mode
    readonly property int today: log && log.ok ? log.stats.today : 0
    readonly property var latest: log && log.ok && log.qsos.length ? log.qsos[0] : null

    Plasmoid.icon: "view-list-details"
    Plasmoid.status: error ? PlasmaCore.Types.NeedsAttentionStatus
                           : PlasmaCore.Types.ActiveStatus
    toolTipMainText: i18n("Logbook")
    toolTipSubText: {
        if (error)
            return error
        if (!log || !log.ok)
            return i18n("Loading…")
        let text = i18n("%1 QSOs, %2 today", log.stats.total, log.stats.today)
        if (latest)
            text += "\n" + i18n("Last: %1 %2 %3 at %4 UTC", latest.call, latest.band,
                                latest.mode, utcTime(latest.qso_utc, false))
        return text
    }

    // ---- data ----

    // Only names as the logbook writes them reach the shell.
    function safe(word) {
        return /^[A-Za-z0-9.\-]*$/.test(word) ? word : ""
    }

    // Plasma's PATH often lacks ~/.local/bin, where the installer puts ic705suite.
    function program() {
        return 'PATH="$HOME/.local/bin:$PATH" ' + Plasmoid.configuration.command
    }

    function refresh() {
        const filter = [safe(band), safe(mode)].filter(w => w).join(" ")
        const command = program() + " --logbook-json " + filter
        loading = true
        executable.connectSource(command.trim())
    }

    function setFilter(newBand, newMode) {
        Plasmoid.configuration.band = newBand
        Plasmoid.configuration.mode = newMode
        refresh()
    }

    function openLogbook() {
        executable.connectSource("sh -c '" + program() + " --gui log >/dev/null 2>&1 &'")
    }

    // "2026-10-15T14:02:15Z" -> "14:02", or "15/10 14:02" when not today (UTC both).
    function utcTime(iso, withDay) {
        const when = new Date(iso)
        const now = new Date()
        const pad = n => String(n).padStart(2, "0")
        const time = pad(when.getUTCHours()) + ":" + pad(when.getUTCMinutes())
        const sameDay = when.getUTCFullYear() === now.getUTCFullYear()
            && when.getUTCMonth() === now.getUTCMonth() && when.getUTCDate() === now.getUTCDate()
        if (!withDay || sameDay)
            return time
        return pad(when.getUTCDate()) + "/" + pad(when.getUTCMonth() + 1) + " " + time
    }

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source)
            if (source.indexOf("--logbook-json") < 0)
                return                                  // the logbook being opened
            root.loading = false
            const stdout = (data["stdout"] || "").trim()
            if (data["exit code"] !== 0 || !stdout) {
                root.error = (data["stderr"] || "").trim().split("\n").pop()
                    || i18n("ic705suite did not answer (is it installed and up to date?)")
                return
            }
            try {
                const answer = JSON.parse(stdout.split("\n").pop())
                root.error = answer.ok ? "" : answer.error
                if (answer.ok)
                    root.log = answer
            } catch (e) {
                root.error = i18n("Unexpected answer from ic705suite")
            }
        }
    }

    Timer {
        interval: Plasmoid.configuration.refreshMinutes * 60 * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    onExpandedChanged: if (expanded) refresh()

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: i18n("Open the logbook")
            icon.name: "document-open"
            onTriggered: root.openLogbook()
        },
        PlasmaCore.Action {
            text: i18n("Refresh")
            icon.name: "view-refresh"
            onTriggered: root.refresh()
        }
    ]

    // ---- in the panel: the icon, with today's QSOs ----

    compactRepresentation: MouseArea {
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded

        Kirigami.Icon {
            anchors.fill: parent
            source: Plasmoid.icon
            active: parent.containsMouse
        }

        Rectangle {
            visible: root.today > 0
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            width: Math.max(height, badge.implicitWidth + Kirigami.Units.smallSpacing)
            height: Math.round(parent.height * 0.5)
            radius: height / 2
            color: Kirigami.Theme.highlightColor

            PlasmaComponents.Label {
                id: badge
                anchors.centerIn: parent
                text: root.today
                color: Kirigami.Theme.highlightedTextColor
                font.pixelSize: Math.round(parent.height * 0.7)
                font.bold: true
            }
        }
    }

    // ---- open ----

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 22
        Layout.minimumHeight: Kirigami.Units.gridUnit * 24
        Layout.preferredWidth: Kirigami.Units.gridUnit * 26
        Layout.preferredHeight: Kirigami.Units.gridUnit * 30
        collapseMarginsHint: true

        header: PlasmaExtras.PlasmoidHeading {
            RowLayout {
                anchors.fill: parent

                PlasmaExtras.Heading {
                    Layout.fillWidth: true
                    level: 1
                    text: root.log && root.log.ok && root.log.callsign
                          ? i18n("Logbook %1", root.log.callsign) : i18n("Logbook")
                    elide: Text.ElideRight
                }
                PlasmaComponents.BusyIndicator {
                    visible: root.loading
                    Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                    Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                }
                PlasmaComponents.ToolButton {
                    icon.name: "view-refresh"
                    onClicked: root.refresh()
                    PlasmaComponents.ToolTip { text: i18n("Refresh") }
                }
                PlasmaComponents.ToolButton {
                    icon.name: "document-open"
                    onClicked: root.openLogbook()
                    PlasmaComponents.ToolTip { text: i18n("Open the logbook") }
                }
            }
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.smallSpacing

            // The counters.
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.smallSpacing
                uniformCellSizes: true                      // the four, equally wide
                visible: root.log !== null
                Repeater {
                    model: root.log && root.log.ok ? [
                        [root.log.stats.total, i18n("QSOs")],
                        [root.log.stats.today, i18n("Today")],
                        [root.log.stats.unique_calls, i18n("Calls")],
                        [root.log.stats.countries, i18n("Countries")],
                    ] : []
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        PlasmaExtras.Heading {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            level: 2
                            text: modelData[0]
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            text: modelData[1]
                            opacity: 0.7
                            font: Kirigami.Theme.smallFont
                        }
                    }
                }
            }

            // The filter: a band and a mode, with how many QSOs each has.
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.smallSpacing
                Layout.rightMargin: Kirigami.Units.smallSpacing
                visible: root.log !== null

                PlasmaComponents.ComboBox {
                    id: bandBox
                    Layout.fillWidth: true
                    textRole: "text"
                    valueRole: "value"
                    model: [{ text: i18n("All bands"), value: "" }].concat(
                        root.log ? root.log.bands.map(b => ({
                            text: b + "  ·  " + (root.log.stats.by_band[b] || 0), value: b })) : [])
                    currentIndex: Math.max(0, model.findIndex(item => item.value === root.band))
                    onActivated: root.setFilter(currentValue, root.mode)
                }
                PlasmaComponents.ComboBox {
                    id: modeBox
                    Layout.fillWidth: true
                    textRole: "text"
                    valueRole: "value"
                    model: [{ text: i18n("All modes"), value: "" }].concat(
                        root.log ? root.log.modes.map(m => ({
                            text: m + "  ·  " + (root.log.stats.by_mode[m] || 0), value: m })) : [])
                    currentIndex: Math.max(0, model.findIndex(item => item.value === root.mode))
                    onActivated: root.setFilter(root.band, currentValue)
                }
            }

            PlasmaComponents.ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ListView {
                    id: list
                    clip: true
                    model: root.log && root.log.ok ? root.log.qsos : []
                    highlight: PlasmaExtras.Highlight {}
                    highlightMoveDuration: 0
                    currentIndex: -1

                    delegate: PlasmaComponents.ItemDelegate {
                        id: row
                        required property var modelData
                        required property int index
                        width: ListView.view.width
                        hoverEnabled: true
                        onHoveredChanged: if (hovered) ListView.view.currentIndex = index
                        onClicked: Qt.openUrlExternally(
                            "https://www.qrz.com/db/" + encodeURIComponent(modelData.call))

                        PlasmaComponents.ToolTip {
                            text: i18n("Look %1 up on QRZ.com", row.modelData.call)
                        }

                        contentItem: ColumnLayout {
                            spacing: 0
                            RowLayout {
                                Layout.fillWidth: true
                                PlasmaComponents.Label {
                                    text: row.modelData.call
                                    font.bold: true
                                    font.family: "monospace"
                                }
                                PlasmaComponents.Label {
                                    text: row.modelData.name
                                    visible: text !== ""
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Item {
                                    Layout.fillWidth: true
                                    visible: row.modelData.name === ""
                                }
                                PlasmaComponents.Label {
                                    text: row.modelData.band + " " + row.modelData.mode
                                    color: Kirigami.Theme.positiveTextColor
                                }
                                PlasmaComponents.Label {
                                    text: root.utcTime(row.modelData.qso_utc, true)
                                    font.family: "monospace"
                                    opacity: 0.8
                                }
                            }
                            PlasmaComponents.Label {
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                                font: Kirigami.Theme.smallFont
                                opacity: 0.7
                                text: [
                                    row.modelData.country,
                                    row.modelData.gridsquare,
                                    row.modelData.qth,
                                    row.modelData.rst_sent || row.modelData.rst_rcvd
                                        ? i18n("S %1 R %2", row.modelData.rst_sent || "-",
                                               row.modelData.rst_rcvd || "-") : "",
                                ].filter(part => part).join("  ·  ")
                            }
                        }
                    }

                    PlasmaExtras.PlaceholderMessage {
                        anchors.centerIn: parent
                        width: parent.width - Kirigami.Units.gridUnit * 2
                        visible: list.count === 0
                        iconName: root.error ? "dialog-warning" : "view-list-details"
                        text: root.error ? root.error
                              : !root.log ? i18n("Loading…")
                              : root.band || root.mode ? i18n("No QSOs of %1",
                                    [root.band, root.mode].filter(w => w).join(" "))
                              : i18n("No QSOs yet")
                    }
                }
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.smallSpacing
                visible: root.error !== "" && list.count > 0
                text: root.error
                color: Kirigami.Theme.negativeTextColor
                wrapMode: Text.Wrap
                font: Kirigami.Theme.smallFont
            }
        }
    }
}
