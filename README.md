# Bazecor Layer Lens

Window rules for [Bazecor](https://github.com/Dygmalab/Bazecor)'s Layer Lens
overlay, for Omarchy / Hyprland.

Layer Lens draws your current keyboard layer on screen. On Wayland an
application cannot float, pin, or place its own window — those are the
compositor's to decide — so Bazecor cannot make its own overlay behave like one
no matter what it does internally. This plugin supplies the missing half.

## Install

```bash
omarchy plugin add https://github.com/jondkinney/omarchy-bazecor-lens
omarchy bar move io.github.jondkinney.bazecor-lens --section right
omarchy restart shell
```

The restart matters: the service half only starts on a full shell restart,
and `rescanPlugins` alone will not bring it up.

Bazecor needs to be reachable as `bazecor` for the show/hide button. A
packaged install already is; for a local build, either link it onto your PATH
or set `bazecorCommand` in the plugin's entry in `~/.config/omarchy/shell.json`
to the binary's full path.

## Remove

```bash
omarchy plugin remove io.github.jondkinney.bazecor-lens
omarchy restart shell
```

That takes the plugin and its bar entry with it. Nothing is left behind
elsewhere: the plugin never writes to your Hyprland config, and its window
rules are applied at runtime, so they are gone the moment the shell stops.
If you installed the optional launcher entry, remove that too:

```bash
rm ~/.local/share/applications/omarchy-bazecor-lens.desktop
```

## What it does

| Setting | Default | Effect |
|---|---|---|
| Keep the overlay floating | on | Stops the overlay being tiled into your layout |
| Overlay follows you across workspaces | on | Pins it, so it is visible wherever you are |
| Border in your theme colour | on | Themed border while focused, none when not |
| Keep the Bazecor window on its own workspace | on | Only the overlay follows you |

All four are switches in the bar drop-down; the defaults above are what most
people will want.

## Using it

The bar icon dims while the overlay is hidden.

| Action | Effect |
|---|---|
| Left click | Open the drop-down: show/hide, and the switches |
| Right click | Toggle the overlay without opening anything |

Showing and hiding runs `bazecor --toggle-lens`, which reaches the running app
through its single-instance lock — the only route in on Wayland, where a
global shortcut registered by an application never fires. Point
`bazecorCommand` at your binary if Bazecor is not on `PATH`.

## Launcher entry (optional)

The bar icon and the app launcher are separate mechanisms, and installing a
plugin never touches `.desktop` files — so link this one yourself if you want
Layer Lens in the launcher as well as the bar:

```bash
cp ~/.config/omarchy/plugins/io.github.jondkinney.bazecor-lens/omarchy-bazecor-lens.desktop \
   ~/.local/share/applications/
update-desktop-database ~/.local/share/applications
```

Launching it toggles the overlay; its "Layer Lens settings" action opens the
drop-down. Remove it again with:

```bash
rm ~/.local/share/applications/omarchy-bazecor-lens.desktop
```

`Exec` runs `bazecor` through a login shell, so a Bazecor that lives on your
`PATH` only via `~/.profile` (a local build linked into `~/.local/bin`, say) is
still found — a plain `bash -c` inherits the session `PATH`, which typically
has no `~/.local/bin` in it.

## Notes

Rules are applied at runtime rather than written into your Hyprland config, so
nothing here edits files you own. They are re-applied after a config reload —
which is also when a theme change is picked up, so the border colour follows
`omarchy theme set` on its own.

Window rules normally only take effect when a window maps, so each pass also
brings any already-open Bazecor window into line. Toggling a setting takes
effect immediately, without restarting Bazecor.

The overlay is matched on its exact window title, `Dygma Lens`, and the
configurator on `Bazecor`. Both share the `Bazecor` window class, which is why
neither rule matches on class alone.

## Requires

Bazecor with Layer Lens, and Hyprland 0.55+ (the Lua configuration API).
