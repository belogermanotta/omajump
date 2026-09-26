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
  property var barActivations: []
  property var fallbackWindows: []
  property var baseTargets: []
  property var gridRect: null
  property var gridHistory: []
  property int gridFallbackId: -1
  property int gridDepth: 0
  property bool gridMode: false
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
    barActivations = []
    fallbackWindows = []
    baseTargets = []
    gridRect = null
    gridHistory = []
    gridFallbackId = -1
    gridDepth = 0
    gridMode = false
  }

  function generateLabels(count) {
    if (count <= 0) return []
    var leaves = alphabet.split("")
    while (leaves.length < count) {
      var shortest = leaves[0].length
      for (var i = 1; i < leaves.length; i++)
        shortest = Math.min(shortest, leaves[i].length)
      var expandAt = -1
      for (var j = 0; j < leaves.length; j++)
        if (leaves[j].length === shortest) expandAt = j
      var prefix = leaves.splice(expandAt, 1)[0]
      var expanded = []
      for (var k = 0; k < alphabet.length; k++)
        expanded.push(prefix + alphabet[k])
      leaves.splice.apply(leaves, [expandAt, 0].concat(expanded))
    }
    var ranked = []
    for (var index = 0; index < leaves.length; index++)
      ranked.push({ label: leaves[index], order: index })
    ranked.sort(function(left, right) {
      return left.label.length - right.label.length || left.order - right.order
    })
    var labels = []
    for (var rank = 0; rank < count; rank++) labels.push(ranked[rank].label)
    return labels
  }

  function collectBarTargets() {
    var rows = []
    var bar = root.shell && root.shell.bar ? root.shell.bar : null
    if (!bar || !bar.clickTargets || !root.activeScreen) return rows

    var screenName = String(root.activeScreen.name || "")
    for (var index = 0; index < bar.clickTargets.length; index++) {
      var target = bar.clickTargets[index]
      if (!target || (typeof bar.moduleTargetClickable === "function"
          && !bar.moduleTargetClickable(target))) continue

      var window = null
      try {
        window = typeof bar.targetWindow === "function"
          ? bar.targetWindow(target)
          : (target.QsWindow ? target.QsWindow.window : null)
      } catch (error) {
        continue
      }
      if (!window || !window.screen || String(window.screen.name || "") !== screenName
          || !window.contentItem) continue

      var point
      try {
        point = target.mapToItem(window.contentItem, 0, 0)
        if (typeof bar.windowScreenPoint === "function")
          point = bar.windowScreenPoint(point, window)
      } catch (error) {
        continue
      }
      var width = Number(target.width || 0)
      var height = Number(target.height || 0)
      if (width <= 1 || height <= 1) continue

      var activationIndex = barActivations.length
      barActivations.push(target)
      rows.push({
        targetId: -(activationIndex + 1),
        targetKind: "bar",
        gridIndex: -1,
        targetX: Number(point.x || 0),
        targetY: Number(point.y || 0),
        targetWidth: width,
        targetHeight: height
      })
    }
    return rows
  }

  function setTargetRows(rows, generateNewLabels) {
    targets.clear()
    var labels = generateNewLabels ? generateLabels(rows.length) : []
    for (var index = 0; index < rows.length; index++) {
      var row = rows[index]
      targets.append({
        targetId: Number(row.targetId),
        targetKind: String(row.targetKind || "semantic"),
        gridIndex: Number(row.gridIndex === undefined ? -1 : row.gridIndex),
        label: generateNewLabels ? labels[index] : String(row.label),
        targetX: Number(row.targetX),
        targetY: Number(row.targetY),
        targetWidth: Number(row.targetWidth),
        targetHeight: Number(row.targetHeight)
      })
    }
  }

  function showGrid(rect) {
    gridRect = rect
    typedPrefix = ""
    var rows = []
    var cellWidth = rect.width / 3
    var cellHeight = rect.height / 3
    for (var index = 0; index < 9; index++) {
      var column = index % 3
      var row = Math.floor(index / 3)
      rows.push({
        targetId: index,
        targetKind: "grid-cell",
        gridIndex: index,
        label: alphabet[index],
        targetX: rect.x + column * cellWidth,
        targetY: rect.y + row * cellHeight,
        targetWidth: cellWidth,
        targetHeight: cellHeight
      })
    }
    setTargetRows(rows, false)
  }

  function startGrid(index) {
    if (index < 0 || index >= fallbackWindows.length) return
    var fallback = fallbackWindows[index]
    gridMode = true
    gridFallbackId = Number(fallback.id)
    gridDepth = 0
    gridHistory = []
    showGrid({
      x: Number(fallback.x),
      y: Number(fallback.y),
      width: Number(fallback.width),
      height: Number(fallback.height)
    })
  }

  function refineGrid(cellIndex) {
    if (!gridMode || !gridRect || cellIndex < 0 || cellIndex > 8) return
    var column = cellIndex % 3
    var row = Math.floor(cellIndex / 3)
    var next = {
      x: gridRect.x + column * gridRect.width / 3,
      y: gridRect.y + row * gridRect.height / 3,
      width: gridRect.width / 3,
      height: gridRect.height / 3
    }
    if (gridDepth < 2) {
      var history = gridHistory.slice()
      history.push(gridRect)
      gridHistory = history
      gridDepth++
      showGrid(next)
      return
    }
    if (!helper.running) return
    activationPending = true
    overlayVisible = false
    helper.write(JSON.stringify({
      type: "click",
      fallbackId: gridFallbackId,
      x: next.x + next.width / 2,
      y: next.y + next.height / 2
    }) + "\n")
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
    barActivations = []
    fallbackWindows = []
    baseTargets = []
    gridMode = false
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
    barActivations = []
    fallbackWindows = Array.isArray(payload.fallbackWindows)
      ? payload.fallbackWindows : []
    var combined = []
    var rows = Array.isArray(payload.targets) ? payload.targets : []
    for (var index = 0; index < rows.length; index++) {
      var row = rows[index]
      combined.push({
        targetId: Number(row.id),
        targetKind: "semantic",
        gridIndex: -1,
        targetX: Number(row.x),
        targetY: Number(row.y),
        targetWidth: Number(row.width),
        targetHeight: Number(row.height)
      })
    }
    combined = combined.concat(collectBarTargets())
    for (var fallbackIndex = 0; fallbackIndex < fallbackWindows.length; fallbackIndex++) {
      var fallback = fallbackWindows[fallbackIndex]
      combined.push({
        targetId: -100000 - fallbackIndex,
        targetKind: "grid-start",
        gridIndex: fallbackIndex,
        targetX: Number(fallback.x) + Number(fallback.width) / 2,
        targetY: Number(fallback.y) + Number(fallback.height) / 2,
        targetWidth: 1,
        targetHeight: 1
      })
    }
    combined.sort(function(left, right) {
      return Math.round(left.targetY / 8) - Math.round(right.targetY / 8)
        || left.targetX - right.targetX || left.targetY - right.targetY
    })
    baseTargets = combined.slice()
    setTargetRows(combined, true)
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
    var exactId = null
    var exactKind = ""
    var exactGridIndex = -1
    var remaining = 0
    for (var index = 0; index < targets.count; index++) {
      var row = targets.get(index)
      if (row.label.indexOf(typedPrefix) === 0) remaining++
      if (row.label === typedPrefix) {
        exactId = row.targetId
        exactKind = row.targetKind
        exactGridIndex = row.gridIndex
      }
    }
    if (exactId !== null) activate(exactId, exactKind, exactGridIndex)
    else if (remaining === 0) typedPrefix = ""
  }

  function typeCharacter(character) {
    if (activationPending || statusText !== "") return
    if (alphabet.indexOf(character) === -1) return
    updatePrefix(typedPrefix + character)
  }

  function backspace() {
    if (activationPending || statusText !== "") return
    if (typedPrefix.length > 0) {
      updatePrefix(typedPrefix.substring(0, typedPrefix.length - 1))
      return
    }
    if (!gridMode) return
    if (gridDepth > 0 && gridHistory.length > 0) {
      var history = gridHistory.slice()
      var previous = history.pop()
      gridHistory = history
      gridDepth--
      showGrid(previous)
    } else {
      gridMode = false
      gridRect = null
      gridFallbackId = -1
      setTargetRows(baseTargets, true)
    }
  }

  function activate(targetId, targetKind, gridIndex) {
    if (activationPending) return
    if (targetKind === "grid-start") {
      startGrid(gridIndex)
      return
    }
    if (targetKind === "grid-cell") {
      refineGrid(gridIndex)
      return
    }
    if (targetKind === "bar") {
      var barTarget = barActivations[-targetId - 1]
      if (!barTarget || typeof barTarget.triggerPress !== "function") {
        showTransient("That bar control is no longer available")
        return
      }
      activationPending = true
      try {
        barTarget.triggerPress(Qt.LeftButton)
        root.dismiss()
      } catch (error) {
        activationPending = false
        showTransient("The bar rejected the action")
      }
      return
    }
    if (!helper.running) return
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
