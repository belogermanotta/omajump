# OmaJump

OmaJump adds Homerow-style keyboard hints to Omarchy. Summon the overlay,
type the home-row label shown on a control, and OmaJump invokes that control's
accessibility action without moving the mouse.

Version 0.2 targets every visible application window on every monitor's active
workspace. It supports semantic activation, text highlighting, scroll-region
selection, the Omarchy toolbar, and a refined pointer-grid fallback. It does
not take screenshots, run OCR, connect to the network, create a virtual input
device, or require root.

## Requirements

- Omarchy 4 with `omarchy-shell`
- Hyprland and Quickshell
- Python 3 with PyGObject and the AT-SPI 2 introspection bindings
- An application that exposes its controls through AT-SPI

These dependencies are present in a standard Omarchy 4 desktop. Applications
with incomplete accessibility support may expose only some controls—or none.
Chromium and Electron accessibility trees are requested automatically when the
overlay opens. OmaJump integrates directly with the built-in Omarchy bar's
clickable widgets because Quickshell layer surfaces do not expose them through
AT-SPI.

## Install

Once this repository is published, install and enable it using its Git URL:

```bash
omarchy plugin add <git-url> --enable
```

Add the recommended binding to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + CTRL + SPACE", "OmaJump", "omarchy-shell shell toggle omajump '{}'")
o.bind("SUPER + ALT + CTRL + SLASH", "OmaJump text search", "omarchy-shell shell summon omajump '{\"mode\":\"search\"}'")
o.bind("SUPER + ALT + CTRL + J", "OmaJump scroll regions", "omarchy-shell shell summon omajump '{\"mode\":\"scroll\"}'")
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

Press `SUPER + ALT + CTRL + SPACE`, then type a visible hint using
`a s d f g h j k l`. Hints appear simultaneously on every connected screen;
the screen that was focused when OmaJump opened captures the keyboard. For a
multi-key hint, characters already typed turn grey. Use Backspace to correct a
partial hint or Escape to close the overlay.

Labels are prefix-free, so a complete label activates immediately. Targets are
ordered from top to bottom and left to right; the earliest targets receive the
shortest labels.

If a window such as WezTerm exposes no actionable accessibility controls,
OmaJump places one hint at its center. Choose it to enter a highlighted 3×3
grid, then choose three home-row cells to refine the pointer location and send
a left click. Backspace moves to the previous grid level. The pointer action is
sent directly through Hyprland to the selected window.

Press `SUPER + ALT + CTRL + /` for text-search mode. Type at least three
characters; all matching accessible text is highlighted across every active
screen. Continue typing to narrow the highlights, Backspace to widen them, or
Escape to close.

Press `SUPER + ALT + CTRL + J` to label all accessible scroll regions. Choose
a region, then use Up/Down or `i`/`k` to scroll that exact window. Backspace
returns to region selection. Apps with sparse accessibility data, including
terminals such as WezTerm, receive a whole-window scroll region.

## Update and remove

```bash
omarchy plugin update omajump
omarchy plugin disable omajump
omarchy plugin remove omajump
```

Remove the optional Hyprland binding yourself if you no longer use the plugin.

## Privacy and security

Omarchy plugins run as unsandboxed code inside the shell, so review third-party
plugins before enabling them. OmaJump's helper reads the AT-SPI trees of visible
windows on all active monitors and their Hyprland geometry. Accessible names
cross the local process pipe only while text-search mode is open and are never
stored. Normal hint and scroll modes expose only rectangles and numeric IDs.
Bar-widget geometry and activation stay inside the existing Omarchy Shell
process. Pointer-grid activation sends only the chosen coordinate and target
window to the local Hyprland compositor.

## Development

Validate the plugin and run its unit tests:

```bash
omarchy plugin validate .
python -m unittest discover -s tests -v
python -m compileall -q omajump scripts
```

Run read-only scans of all active screens without invoking any action:

```bash
python scripts/omajump_backend.py --probe | python -m json.tool
python scripts/omajump_backend.py --probe --mode search | python -m json.tool
python scripts/omajump_backend.py --probe --mode scroll | python -m json.tool
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
