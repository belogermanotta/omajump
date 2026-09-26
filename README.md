# OmaJump

OmaJump adds Homerow-style keyboard hints to Omarchy. Summon the overlay,
type the home-row label shown on a control, and OmaJump invokes that control's
accessibility action without moving the mouse.

Version 0.1 targets the focused application window and supports primary
activation, Backspace, and Escape. It does not take screenshots, connect to the
network, create a virtual input device, or require root.

## Requirements

- Omarchy 4 with `omarchy-shell`
- Hyprland and Quickshell
- Python 3 with PyGObject and the AT-SPI 2 introspection bindings
- An application that exposes its controls through AT-SPI

These dependencies are present in a standard Omarchy 4 desktop. Applications
with incomplete accessibility support may expose only some controls—or none.

## Install

Once this repository is published, install and enable it using its Git URL:

```bash
omarchy plugin add <git-url> --enable
```

Add the recommended binding to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + CTRL + SPACE", "OmaJump", "omarchy-shell shell toggle omajump '{}'")
```

Check `omarchy menu keybindings --print` first and choose another key if
`SUPER + ALT + CTRL + SPACE` is already occupied. Hyprland normally reloads the file
automatically; validate it explicitly after editing:

```bash
hyprctl reload
hyprctl configerrors
```

OmaJump deliberately does not edit your bindings during installation.

## Use

1. Focus the application you want to control.
2. Press `SUPER + ALT + CTRL + SPACE` (or your chosen binding).
3. Type a visible hint using `a s d f g h j k l`.
4. Use Backspace to correct a partial hint or Escape to close the overlay.

Labels are prefix-free, so a complete label activates immediately. Targets are
ordered from top to bottom and left to right; the earliest targets receive the
shortest labels.

## Update and remove

```bash
omarchy plugin update omajump
omarchy plugin disable omajump
omarchy plugin remove omajump
```

Remove the optional Hyprland binding yourself if you no longer use the plugin.

## Privacy and security

Omarchy plugins run as unsandboxed code inside the shell, so review third-party
plugins before enabling them. OmaJump's helper reads only the focused window's
AT-SPI tree and Hyprland geometry. Accessible names remain in the helper and
are neither sent to QML nor stored. Only anonymous rectangles, numeric IDs, and
generated labels cross the local process pipe.

## Development

Validate the plugin and run its unit tests:

```bash
omarchy plugin validate .
python -m unittest discover -s tests -v
python -m compileall -q omajump scripts
```

Run a read-only scan of the focused application without invoking any action:

```bash
python scripts/omajump_backend.py --probe | python -m json.tool
```

Run the standalone overlay fixture (Escape closes it):

```bash
quickshell -p dev.qml
```

For a non-interactive smoke run:

```bash
OMAJUMP_SMOKE_TEST=1 quickshell -p dev.qml
```

The live backend keeps AT-SPI objects only for the lifetime of one overlay.
QML and the backend communicate using one compact JSON object per line: the
backend emits a `targets` event, QML sends an `activate` command, and the
backend replies with `activated` or `error`.

## License

MIT. See [LICENSE](LICENSE).
