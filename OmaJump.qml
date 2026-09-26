import Quickshell
import Quickshell.Io
import QtQuick
import qs.Commons

Item {
  id: root

  property string omarchyPath: Quickshell.env("OMARCHY_PATH")
  property var shell: null
  property var manifest: null

  property bool scanning: false
  property bool overlayVisible: false
  readonly property bool opened: scanning || overlayVisible
  property bool activationPending: false
  property bool stoppingHelper: false
  property var activeScreen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
  property string typedPrefix: ""
  property string statusText: ""
  readonly property string alphabet: "asdfghjkl"

  function pluginId() {
    return root.manifest && root.manifest.id ? root.manifest.id : "omajump"
  }

  function filePath(url) {
    var value = String(url)
    if (value.indexOf("file://") === 0) value = value.substring(7)
    return decodeURIComponent(value)
  }

  function helperPath() {
    return filePath(Qt.resolvedUrl("scripts/omajump_backend.py"))
  }

  function screenNamed(name) {
    for (var index = 0; index < Quickshell.screens.length; index++) {
      if (String(Quickshell.screens[index].name) === String(name))
        return Quickshell.screens[index]
    }
    return Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
  }

  function resetState() {
    scanTimeout.stop()
    dismissTimer.stop()
    targets.clear()
    typedPrefix = ""
    statusText = ""
    activationPending = false
  }

  function open(payloadJson) {
    if (root.opened) return
    resetState()
    scanning = true
    helper.command = ["python3", helperPath()]
    helper.running = true
    scanTimeout.restart()
  }

  function close() {
    scanTimeout.stop()
    dismissTimer.stop()
    scanning = false
    overlayVisible = false
    activationPending = false
    targets.clear()
    typedPrefix = ""
    statusText = ""
    if (helper.running) {
      stoppingHelper = true
      helper.running = false
    }
  }

  function dismiss() {
    close()
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide(pluginId())
  }

  function toggle() {
    if (root.opened) dismiss()
    else open("{}")
  }

  function showTransient(message) {
    scanning = false
    overlayVisible = true
    statusText = message
    if (helper.running) {
      stoppingHelper = true
      helper.running = false
    }
    dismissTimer.restart()
  }

  function handleContext(payload) {
    activeScreen = screenNamed(payload.monitor || "")
    overlayVisible = true
    statusText = ""
  }

  function handleTargets(payload) {
    scanTimeout.stop()
    scanning = false
    activeScreen = screenNamed(payload.monitor || "")
    targets.clear()
    var rows = Array.isArray(payload.targets) ? payload.targets : []
    for (var index = 0; index < rows.length; index++) {
      var row = rows[index]
      targets.append({
        targetId: Number(row.id),
        label: String(row.label),
        targetX: Number(row.x),
        targetY: Number(row.y),
        targetWidth: Number(row.width),
        targetHeight: Number(row.height)
      })
    }
    overlayVisible = true
    if (targets.count === 0) {
      statusText = "No accessible controls found on this screen"
      dismissTimer.restart()
    } else {
      statusText = ""
    }
  }

  function handleBackendLine(line) {
    var text = String(line || "").trim()
    if (!text) return
    var payload
    try {
      payload = JSON.parse(text)
    } catch (error) {
      showTransient("OmaJump received invalid backend data")
      return
    }

    if (payload.type === "context") {
      handleContext(payload)
    } else if (payload.type === "targets") {
      handleTargets(payload)
    } else if (payload.type === "activated") {
      root.dismiss()
    } else if (payload.type === "error") {
      activationPending = false
      showTransient(payload.message || "OmaJump could not inspect this application")
    }
  }

  function updatePrefix(nextPrefix) {
    typedPrefix = nextPrefix
    var exactId = -1
    var remaining = 0
    for (var index = 0; index < targets.count; index++) {
      var row = targets.get(index)
      if (row.label.indexOf(typedPrefix) === 0) remaining++
      if (row.label === typedPrefix) exactId = row.targetId
    }
    if (exactId >= 0) activate(exactId)
    else if (remaining === 0) typedPrefix = ""
  }

  function typeCharacter(character) {
    if (activationPending || statusText !== "") return
    if (alphabet.indexOf(character) === -1) return
    updatePrefix(typedPrefix + character)
  }

  function backspace() {
    if (activationPending || statusText !== "" || typedPrefix.length === 0) return
    updatePrefix(typedPrefix.substring(0, typedPrefix.length - 1))
  }

  function activate(targetId) {
    if (activationPending || !helper.running) return
    activationPending = true
    statusText = "Activating…"
    helper.write(JSON.stringify({ type: "activate", id: targetId }) + "\n")
  }

  ListModel { id: targets }

  Process {
    id: helper
    running: false
    stdinEnabled: true

    stdout: SplitParser {
      onRead: function(line) { root.handleBackendLine(line) }
    }

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var detail = String(text || "").trim()
        if (detail && !root.stoppingHelper) console.warn("OmaJump backend:", detail)
      }
    }

    onExited: function(exitCode, exitStatus) {
      if (root.stoppingHelper) {
        root.stoppingHelper = false
        return
      }
      if (root.opened)
        root.showTransient("OmaJump backend stopped unexpectedly")
    }
  }

  Timer {
    id: scanTimeout
    interval: 3000
    repeat: false
    onTriggered: root.showTransient("OmaJump timed out while inspecting this application")
  }

  Timer {
    id: dismissTimer
    interval: 1700
    repeat: false
    onTriggered: root.dismiss()
  }

  HintOverlay {
    id: overlay
    opened: root.overlayVisible
    activeScreen: root.activeScreen
    targetModel: targets
    typedPrefix: root.typedPrefix
    statusText: root.statusText
    loading: root.scanning
    badgeBackground: Color.menu.selectedBackground
    badgeForeground: Color.menu.selectedText
    badgeBorder: Color.menu.border
    statusBackground: Color.menu.background
    statusForeground: Color.menu.text
    fontFamily: Style.font.family
    badgeFontSize: Style.font.subtitle
    cornerRadius: Style.cornerRadius

    onCharacterTyped: function(character) { root.typeCharacter(character) }
    onBackspaceRequested: root.backspace()
    onCancelRequested: root.dismiss()
  }
}
