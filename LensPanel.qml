import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar icon plus its drop-down for Bazecor's Layer Lens.
//
// Layer Lens draws your current keyboard layer on screen. On Wayland an
// application cannot float, pin, or place its own window, so everything that
// makes that overlay behave like an overlay comes from the compositor — which
// is what this plugin supplies, and what these switches control.
Panel {
    id: root
    moduleName: "io.github.jondkinney.bazecor-lens"
    ipcTarget: "io.github.jondkinney.bazecor-lens"

    readonly property string pluginId: "io.github.jondkinney.bazecor-lens"
    readonly property string overlayTitle: "Dygma Lens"

    function setting(name, fallback) {
        var value = settings ? settings[name] : undefined
        return value === undefined || value === null ? fallback : value
    }

    // bazecorCommand comes from shell.json, which is editable, so it is treated
    // as untrusted. It names one executable — a bare name looked up on PATH, or
    // an absolute path — and nothing else. Anything outside that shape is
    // refused rather than sanitised, and it is never handed to a shell: see
    // resolveProc and toggleProc, which pass it as an argv element.
    readonly property string configuredCommand: String(setting("bazecorCommand", "bazecor"))

    readonly property bool commandWellFormed:
        /^(?:[A-Za-z0-9._][A-Za-z0-9._+-]*|\/[A-Za-z0-9._+\/-]+)$/.test(root.configuredCommand)

    // Absolute path of the executable once resolved, or "" if it could not be.
    // Only ever set from resolveProc's output, never from the setting directly.
    property string resolvedPath: ""

    property bool overlayVisible: false

    // Whether bazecorCommand actually resolves. Without this a missing Bazecor
    // just gives you a button that does nothing: bar.run() hands the command to
    // a shell, the shell can't find it, and nothing comes back. The window rules
    // are unaffected either way — they never touch Bazecor.
    property bool bazecorFound: true

    // `which` takes the name as an argv element, so no shell parses it and
    // metacharacters in the setting are just characters in a filename that
    // will not be found. It prints the absolute path it resolved.
    Process {
        id: resolveProc
        stdout: StdioCollector {
            onStreamFinished: {
                var line = text.trim().split("\n")[0] || ""
                root.resolvedPath = line.indexOf("/") === 0 ? line : ""
                root.bazecorFound = root.resolvedPath.length > 0
            }
        }
    }

    function checkBazecor() {
        if (!root.commandWellFormed) {
            root.resolvedPath = ""
            root.bazecorFound = false
            return
        }
        resolveProc.command = ["/usr/bin/which", "--", root.configuredCommand]
        resolveProc.running = true
    }

    readonly property color foreground: bar ? bar.foreground : Color.foreground
    readonly property color dim: Qt.darker(foreground, 1.55)
    readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

    function refreshVisible() {
        Hyprland.refreshToplevels()
        var model = Hyprland.toplevels
        var list = model && model.values ? model.values : []
        for (var i = 0; i < list.length; i++)
            if (list[i] && list[i].title === root.overlayTitle) {
                root.overlayVisible = true
                return
            }
        root.overlayVisible = false
    }

    // Relaunching Bazecor with the flag reaches the running instance through its
    // single-instance lock. It is the only way in: a global shortcut registered
    // by the app never fires under Wayland.
    //
    // Deliberately not bar.run(), which hands a string to `bash -lc`. This runs
    // the resolved absolute path as argv[0] with the flag as argv[1], so no
    // shell sees the value from shell.json at any point.
    Process { id: toggleProc }

    function toggleLens() {
        if (!root.resolvedPath)
            return
        toggleProc.command = [root.resolvedPath, "--toggle-lens"]
        toggleProc.running = true
        settleToggle.restart()
    }

    // Settings live in this plugin's own shell.json entry. The write is done by
    // set-flag.sh next to this file, invoked with the key and value as argv
    // rather than as shell text, so nothing here can be turned into a command.
    // The script opens shell.json once with O_NOFOLLOW and does everything from
    // that descriptor, then writes through a securely created temp file in the
    // same directory and renames it into place.
    Process { id: setFlagProc }

    readonly property string setFlagScript:
        String(Qt.resolvedUrl("set-flag.py")).replace(/^file:\/\//, "")

    function setFlag(key, value) {
        setFlagProc.command = [root.setFlagScript, key, value ? "true" : "false"]
        setFlagProc.running = true
    }

    Component.onCompleted: { refreshVisible(); checkBazecor() }

    onOpenedChanged: if (opened) { refreshVisible(); checkBazecor() }

    Connections {
        target: Hyprland
        function onRawEvent(event) {
            if (event && (event.name === "openwindow" || event.name === "closewindow"))
                root.refreshVisible()
        }
    }

    // The window takes a moment to map or unmap after a toggle lands.
    Timer {
        id: settleToggle
        interval: 900
        onTriggered: root.refreshVisible()
    }

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        // Use the button's own text slot rather than a custom iconComponent: it
        // renders the glyph in the bar's icon font, which a hand-rolled Text has
        // no way to know about — it just draws tofu.
        text: "󰌌"
        // Dimmed while the overlay is off screen, the same way the rest of the
        // bar shows an inactive module.
        foreground: root.overlayVisible ? root.barForeground : Qt.darker(root.barForeground, 1.55)
        tooltipText: (root.overlayVisible ? "Layer Lens is showing" : "Layer Lens is hidden")
            + "\nLeft click for settings · right click to toggle"
        onPressed: function (buttonCode) {
            if (buttonCode === Qt.RightButton)
                root.toggleLens()
            else
                root.toggle()
        }
    }

    KeyboardPanel {
        id: panel
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        contentWidth: panel.fittedContentWidth(Style.space(360))
        contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(520))

        ColumnLayout {
            id: content
            width: parent.width
            spacing: Style.space(4)

            PanelSectionHeader {
                Layout.fillWidth: true
                text: "Layer Lens"
                fontFamily: root.fontFamily
                foreground: root.foreground
            }

            // Show/hide, the thing most likely to be wanted on opening this.
            Button {
                visible: root.bazecorFound
                Layout.fillWidth: true
                text: root.overlayVisible ? "Hide the overlay" : "Show the overlay"
                iconText: root.overlayVisible ? "󰛐" : "󰛑"
                fontFamily: root.fontFamily
                foreground: root.foreground
                onClicked: root.toggleLens()
            }

            // Showing and hiding needs Bazecor; the rules below do not. Say so
            // rather than leaving a button that quietly does nothing.
            ColumnLayout {
                visible: !root.bazecorFound
                Layout.fillWidth: true
                spacing: 0

                Text {
                    Layout.fillWidth: true
                    text: "Can't find " + root.bazecorCommand
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    text: "Showing and hiding needs Bazecor on your PATH, or bazecorCommand set to it. The settings below work regardless."
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                }
            }

            PanelSeparator { Layout.fillWidth: true }

            Repeater {
                model: [
                    {
                        key: "floatOverlay",
                        label: "Keep it floating",
                        hint: "Stops the overlay being tiled into your layout"
                    },
                    {
                        key: "pinOverlay",
                        label: "Follow me across workspaces",
                        hint: "An overlay you can only see on one workspace is not much of an overlay"
                    },
                    {
                        key: "themedBorder",
                        label: "Border in your theme colour",
                        hint: "Themed while focused, invisible when not"
                    },
                    {
                        key: "rememberPosition",
                        label: "Reopen where you left it",
                        hint: "Wayland lets no app place its own window, so the compositor is told"
                    }
                ]

                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: Style.space(8)

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        Text {
                            Layout.fillWidth: true
                            text: modelData.label
                            color: root.foreground
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.body
                            elide: Text.ElideRight
                        }

                        Text {
                            Layout.fillWidth: true
                            text: modelData.hint
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.bodySmall
                            wrapMode: Text.WordWrap
                        }
                    }

                    ToggleSwitch {
                        checked: root.setting(modelData.key, true) === true
                        foreground: root.foreground
                        onToggled: root.setFlag(modelData.key, !checked)
                    }
                }
            }
        }
    }
}
