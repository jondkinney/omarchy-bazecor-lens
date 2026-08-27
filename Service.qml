import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland

// Bazecor's Layer Lens draws your current keyboard layer on screen. On Wayland
// an application cannot float, pin, or place its own window, so everything that
// makes that overlay behave like an overlay has to come from the compositor.
// This service keeps those rules in one place, applies them live, and puts them
// back after a config reload (which is also how it picks up a theme change).
Item {
    id: root

    // Injected by the shell's service loader (see shell.qml ensureService).
    // Service plugins are handed the shell rather than a settings object, so we
    // pick our own entry out of shell.json ourselves. Being a binding, this also
    // re-evaluates when shell.json is saved, so a toggle applies straight away.
    property var shell: null

    readonly property string pluginId: "io.github.jondkinney.bazecor-lens"

    readonly property var settings: {
        var cfg = shell && shell.shellConfig ? shell.shellConfig : null
        if (!cfg)
            return ({})
        // Our entry lives wherever the plugin is enabled from. A plain service
        // is listed in plugins[]; declaring a bar widget as well moves it into
        // bar.layout.<section>, which is what enables it — so look in both, and
        // keep working whichever way it was set up.
        var pools = [cfg.plugins]
        if (cfg.bar && cfg.bar.layout)
            pools.push(cfg.bar.layout.left, cfg.bar.layout.center, cfg.bar.layout.right)
        for (var p = 0; p < pools.length; p++) {
            var list = pools[p]
            if (!list)
                continue
            for (var i = 0; i < list.length; i++) {
                var entry = list[i]
                if (entry && String(entry.id) === pluginId)
                    return entry
            }
        }
        return ({})
    }

    function setting(name, fallback) {
        var value = settings ? settings[name] : undefined
        return value === undefined || value === null ? fallback : value
    }

    property bool floatOverlay: setting("floatOverlay", true) === true
    property bool pinOverlay: setting("pinOverlay", true) === true
    property bool themedBorder: setting("themedBorder", true) === true
    property bool rememberPosition: setting("rememberPosition", true) === true

    // Last place the overlay was seen. Bazecor hides Layer Lens by unmapping its
    // window, and a Wayland client cannot ask to be put anywhere, so without
    // this the compositor picks a fresh spot every time it comes back. Watching
    // where it sits and feeding that back as a `move` rule is the only way the
    // overlay can reappear where it was left.
    property int lastX: -1
    property int lastY: -1

    readonly property string overlayTitle: "Dygma Lens"
    readonly property string mainTitle: "Bazecor"

    function lua() {
        // Every managed property is written on every pass, never omitted.
        // hl.window_rule() adds a rule rather than replacing one, so rules
        // accumulate and the most recent explicit value for a property is what
        // wins — leaving a property out would silently keep whatever an earlier
        // pass set, and a toggle would appear to do nothing.
        return '
local floatOverlay, pinOverlay = ' + (floatOverlay ? "true" : "false") + ', ' + (pinOverlay ? "true" : "false") + '
local themedBorder = ' + (themedBorder ? "true" : "false") + '
local overlayTitle, mainTitle = "' + overlayTitle + '", "' + mainTitle + '"

-- Gradient tables hold "0xAARRGGBB" strings; border_color wants "rgba(rrggbbaa)".
local function colorOf(key, fallback)
  local g = hl.get_config(key)
  local a, rgb = tostring(g and g.colors and g.colors[1] or ""):lower():match("^0x(%x%x)(%x%x%x%x%x%x)$")
  return a and ("rgba(%s%s)"):format(rgb, a) or fallback
end

local active = colorOf("general.col.active_border", "rgba(81a1c1ff)")
local border
if themedBorder then
  -- Themed while focused, invisible when not: the overlay is click-through
  -- unless Resize Mode is on, so an idle border is only ever noise.
  border = active .. " rgba(00000000)"
else
  -- Off means "look like any other window", which has to be stated rather than
  -- omitted, or a previous themed rule would keep applying.
  border = active .. " " .. colorOf("general.col.inactive_border", "rgba(595959aa)")
end

local overlayRule = {
  match = { title = "^" .. overlayTitle .. "$" },
  float = floatOverlay,
  pin = pinOverlay,
  border_color = border,
}
' + ((rememberPosition && lastX >= 0)
        ? 'overlayRule.move = "' + lastX + ' ' + lastY + '"\n'
        : '') + 'hl.window_rule(overlayRule)

-- Only the overlay follows you around; the configurator is an ordinary window.
-- Matched on the exact title because both windows share the Bazecor class, and
-- a class-wide rule here would undo the overlay pin set just above.
hl.window_rule({ match = { class = "^" .. mainTitle .. "$", title = "^" .. mainTitle .. "$" }, pin = false })

-- Rules only bite when a window maps, so bring anything already on screen into
-- line too. The float and pin dispatchers toggle, hence comparing first.
for _, w in ipairs(hl.get_windows()) do
  local t = w.title or ""
  if t == overlayTitle then
    if floatOverlay ~= w.floating then hl.dispatch(hl.dsp.window.float({ window = w })) end
    if pinOverlay ~= w.pinned then hl.dispatch(hl.dsp.window.pin({ window = w })) end
  elseif t == mainTitle and w.pinned then
    hl.dispatch(hl.dsp.window.pin({ window = w }))
  end
end
'
    }

    function apply() {
        applyProc.command = ["hyprctl", "eval", root.lua()]
        applyProc.running = true
    }

    Process { id: applyProc }

    // Hyprland emits no event for a window being moved — verified by watching
    // its event socket while dispatching moves: openwindow, windowtitle,
    // changefloatingmode and closewindow all fire, a position change fires
    // nothing. Wayland has no protocol for it either, by design. So where the
    // overlay sits has to be sampled.
    //
    // Two things keep that cheap. Sampling runs only while Layer Lens is
    // actually on screen, started and stopped by openwindow/closewindow, so a
    // hidden Lens costs nothing at all. And it goes through Quickshell's own
    // Hyprland IPC rather than spawning hyprctl.
    property string overlayAddress: ""

    function sampleOverlay() {
        Hyprland.refreshToplevels()
        var model = Hyprland.toplevels
        var list = model && model.values ? model.values : []
        for (var i = 0; i < list.length; i++) {
            var w = list[i]
            if (!w || w.title !== root.overlayTitle)
                continue
            var ipc = w.lastIpcObject
            if (!ipc || !ipc.at)
                return
            var x = Math.round(ipc.at[0])
            var y = Math.round(ipc.at[1])
            if (x === root.lastX && y === root.lastY)
                return
            root.lastX = x
            root.lastY = y
            // Feed the new spot into the rule so the next map lands there.
            root.applySoon()
            return
        }
    }

    Timer {
        id: sampler
        interval: 1000
        repeat: true
        running: false
        onTriggered: root.sampleOverlay()
    }

    // Coalesces bursts — a config reload, a settings change and a position
    // sample can all land together.
    Timer {
        id: settle
        interval: 250
        onTriggered: root.apply()
    }

    function applySoon() { settle.restart() }

    Component.onCompleted: {
        applySoon()
        // Lens may already be on screen if the shell restarted under it.
        adopt.start()
    }

    // One deferred pass to pick up an overlay that was already mapped, once the
    // shell has finished handing us `shell` (and therefore our settings).
    Timer {
        id: adopt
        interval: 1200
        onTriggered: {
            Hyprland.refreshToplevels()
            var model = Hyprland.toplevels
            var list = model && model.values ? model.values : []
            for (var i = 0; i < list.length; i++) {
                var w = list[i]
                if (w && w.title === root.overlayTitle) {
                    root.overlayAddress = w.address ? String(w.address) : ""
                    if (root.rememberPosition)
                        sampler.running = true
                    root.sampleOverlay()
                    return
                }
            }
        }
    }

    onSettingsChanged: applySoon()

    onFloatOverlayChanged: applySoon()
    onPinOverlayChanged: applySoon()
    onThemedBorderChanged: applySoon()

    Connections {
        target: Hyprland

        function onRawEvent(event) {
            if (!event)
                return
            // A reload drops runtime rules, so they have to go back. Omarchy
            // reloads Hyprland when the theme changes, which is exactly when the
            // border colour needs re-reading anyway.
            if (event.name === "configreloaded") {
                root.applySoon()
                return
            }
            // openwindow>>address,workspace,class,title
            if (event.name === "openwindow") {
                var parts = String(event.data || "").split(",")
                var title = parts.slice(3).join(",")
                if (title === root.overlayTitle) {
                    root.overlayAddress = parts[0] || ""
                    if (root.rememberPosition)
                        sampler.running = true
                    root.sampleOverlay()
                } else if (parts[2] === root.mainTitle) {
                    // Bazecor started after we did; make sure it got the rules.
                    root.applySoon()
                }
                return
            }

            // closewindow>>address — only an address, hence remembering it above.
            if (event.name === "closewindow" && String(event.data || "").trim() === root.overlayAddress) {
                sampler.running = false
                root.overlayAddress = ""
            }
        }
    }
}
