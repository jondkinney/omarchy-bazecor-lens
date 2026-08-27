# Bazecor Layer Lens

Window rules for [Bazecor](https://github.com/Dygmalab/Bazecor)'s Layer Lens
overlay, for Omarchy / Hyprland.

Layer Lens draws your current keyboard layer on screen. On Wayland an
application cannot float, pin, or place its own window — those are the
compositor's to decide — so Bazecor cannot make its own overlay behave like one
no matter what it does internally. This plugin supplies the missing half.

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
