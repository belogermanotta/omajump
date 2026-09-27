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
  property string inputMonitorName: Quickshell.screens.length > 0
    ? String(Quickshell.screens[0].name || "") : ""
  property string interactionMode: "hints"
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
  property int selectedScrollId: -1
  readonly property string alphabet: "asdfghjkl"

  function pluginId() {
    return root.manifest && root.manifest.id ? root.manifest.id : "omajump"
  }

  function filePath(url) {
    var value = String(url)
    if (value.indexOf("file://") === 0) value = value.substring(7)
    return decodeURIComponent(value)
  }

  function helperPath() { return filePath(Qt.resolvedUrl("scripts/omajump_backend.py")) }

  function screenRank(name) {
    for (var index = 0; index < Quickshell.screens.length; index++)
      if (String(Quickshell.screens[index].name || "") === String(name)) return index
    return 9999
  }

  function resetState() {
    scanTimeout.stop(); dismissTimer.stop(); targets.clear()
    typedPrefix = ""; statusText = ""; activationPending = false
    barActivations = []; fallbackWindows = []; baseTargets = []
    gridRect = null; gridHistory = []; gridFallbackId = -1
    gridDepth = 0; gridMode = false; selectedScrollId = -1
  }

  function generateLabels(count) {
    if (count <= 0) return []
    var leaves = alphabet.split("")
    while (leaves.length < count) {
      var shortest = leaves[0].length
      for (var i = 1; i < leaves.length; i++) shortest = Math.min(shortest, leaves[i].length)
      var expandAt = -1
      for (var j = 0; j < leaves.length; j++) if (leaves[j].length === shortest) expandAt = j
      var prefix = leaves.splice(expandAt, 1)[0]
      var expanded = []
      for (var k = 0; k < alphabet.length; k++) expanded.push(prefix + alphabet[k])
      leaves.splice.apply(leaves, [expandAt, 0].concat(expanded))
    }
    var ranked = []
    for (var index = 0; index < leaves.length; index++) ranked.push({ label: leaves[index], order: index })
    ranked.sort(function(left, right) { return left.label.length - right.label.length || left.order - right.order })
    var labels = []
    for (var rank = 0; rank < count; rank++) labels.push(ranked[rank].label)
    return labels
  }

  function collectBarTargets() {
    var rows = []
    var bar = root.shell && root.shell.bar ? root.shell.bar : null
    if (!bar || !bar.clickTargets) return rows
    for (var index = 0; index < bar.clickTargets.length; index++) {
      var target = bar.clickTargets[index]
      if (!target || (typeof bar.moduleTargetClickable === "function"
          && !bar.moduleTargetClickable(target))) continue
      var window = null
      try {
        window = typeof bar.targetWindow === "function"
          ? bar.targetWindow(target) : (target.QsWindow ? target.QsWindow.window : null)
      } catch (error) { continue }
      if (!window || !window.screen || !window.contentItem) continue
      var point
      try {
        point = target.mapToItem(window.contentItem, 0, 0)
        if (typeof bar.windowScreenPoint === "function") point = bar.windowScreenPoint(point, window)
      } catch (error) { continue }
      var width = Number(target.width || 0), height = Number(target.height || 0)
      if (width <= 1 || height <= 1) continue
      var activationIndex = barActivations.length
      barActivations.push(target)
      rows.push({
        targetId: -(activationIndex + 1), targetKind: "bar", gridIndex: -1,
        targetMonitor: String(window.screen.name || ""), searchText: "",
        targetX: Number(point.x || 0), targetY: Number(point.y || 0),
        targetWidth: width, targetHeight: height
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
        targetId: Number(row.targetId), targetKind: String(row.targetKind || "semantic"),
        gridIndex: Number(row.gridIndex === undefined ? -1 : row.gridIndex),
        label: generateNewLabels ? labels[index] : String(row.label || ""),
        targetMonitor: String(row.targetMonitor || ""), searchText: String(row.searchText || ""),
        targetX: Number(row.targetX), targetY: Number(row.targetY),
        targetWidth: Number(row.targetWidth), targetHeight: Number(row.targetHeight)
      })
    }
  }

  function sortSpatially(rows) {
    rows.sort(function(left, right) {
      return screenRank(left.targetMonitor) - screenRank(right.targetMonitor)
        || Math.round(left.targetY / 8) - Math.round(right.targetY / 8)
        || left.targetX - right.targetX || left.targetY - right.targetY
    })
    return rows
  }

  function showGrid(rect) {
    gridRect = rect; typedPrefix = ""
    var rows = [], cellWidth = rect.width / 3, cellHeight = rect.height / 3
    for (var index = 0; index < 9; index++) {
      var column = index % 3, row = Math.floor(index / 3)
      rows.push({
        targetId: index, targetKind: "grid-cell", gridIndex: index,
        label: alphabet[index], targetMonitor: rect.monitor, searchText: "",
        targetX: rect.x + column * cellWidth, targetY: rect.y + row * cellHeight,
        targetWidth: cellWidth, targetHeight: cellHeight
      })
    }
    setTargetRows(rows, false)
  }

  function startGrid(index) {
    if (index < 0 || index >= fallbackWindows.length) return
    var fallback = fallbackWindows[index]
    gridMode = true; gridFallbackId = Number(fallback.id); gridDepth = 0; gridHistory = []
    showGrid({
      monitor: String(fallback.monitor || ""), x: Number(fallback.x), y: Number(fallback.y),
      width: Number(fallback.width), height: Number(fallback.height)
    })
  }

  function refineGrid(cellIndex) {
    if (!gridMode || !gridRect || cellIndex < 0 || cellIndex > 8) return
    var column = cellIndex % 3, row = Math.floor(cellIndex / 3)
    var next = {
      monitor: gridRect.monitor,
      x: gridRect.x + column * gridRect.width / 3,
      y: gridRect.y + row * gridRect.height / 3,
      width: gridRect.width / 3, height: gridRect.height / 3
    }
    if (gridDepth < 2) {
      var history = gridHistory.slice(); history.push(gridRect); gridHistory = history
      gridDepth++; showGrid(next); return
    }
    if (!helper.running) return
    activationPending = true; overlayVisible = false
    helper.write(JSON.stringify({
      type: "click", fallbackId: gridFallbackId,
      x: next.x + next.width / 2, y: next.y + next.height / 2
    }) + "\n")
  }

  function open(payloadJson) {
    if (root.opened) return
    resetState()
    var payload = {}
    try { payload = JSON.parse(String(payloadJson || "{}")) } catch (error) { payload = {} }
    interactionMode = ["hints", "search", "scroll"].indexOf(String(payload.mode || "hints")) >= 0
      ? String(payload.mode || "hints") : "hints"
    scanning = true
    helper.command = ["python3", helperPath(), "--mode", interactionMode]
    helper.running = true; scanTimeout.restart()
  }

  function close() {
    scanTimeout.stop(); dismissTimer.stop(); scanning = false; overlayVisible = false
    activationPending = false; barActivations = []; fallbackWindows = []; baseTargets = []
    gridMode = false; selectedScrollId = -1; targets.clear(); typedPrefix = ""; statusText = ""
    if (helper.running) { stoppingHelper = true; helper.running = false }
  }

  function dismiss() {
    close()
    if (root.shell && typeof root.shell.hide === "function") root.shell.hide(pluginId())
  }

  function toggle() { if (root.opened) dismiss(); else open("{}") }

  function showTransient(message) {
    scanning = false; overlayVisible = true; statusText = message
    if (helper.running) { stoppingHelper = true; helper.running = false }
    dismissTimer.restart()
  }

  function handleContext(payload) {
    inputMonitorName = String(payload.inputMonitor || inputMonitorName)
    overlayVisible = true; statusText = ""
  }

  function handleTargets(payload) {
    scanTimeout.stop(); scanning = false
    inputMonitorName = String(payload.inputMonitor || inputMonitorName)
    targets.clear(); barActivations = []
    fallbackWindows = Array.isArray(payload.fallbackWindows) ? payload.fallbackWindows : []
    var combined = []

    if (interactionMode === "search") {
      var searchRows = Array.isArray(payload.searchTargets) ? payload.searchTargets : []
      for (var searchIndex = 0; searchIndex < searchRows.length; searchIndex++) {
        var search = searchRows[searchIndex]
        combined.push({
          targetId: searchIndex, targetKind: "search", gridIndex: -1, label: "",
          targetMonitor: String(search.monitor || ""), searchText: String(search.text || ""),
          targetX: Number(search.x), targetY: Number(search.y),
          targetWidth: Number(search.width), targetHeight: Number(search.height)
        })
      }
      baseTargets = combined.slice(); setTargetRows(combined, false)
    } else if (interactionMode === "scroll") {
      var scrollRows = Array.isArray(payload.scrollTargets) ? payload.scrollTargets : []
      for (var scrollIndex = 0; scrollIndex < scrollRows.length; scrollIndex++) {
        var scroll = scrollRows[scrollIndex]
        combined.push({
          targetId: Number(scroll.id), targetKind: "scroll", gridIndex: -1,
          targetMonitor: String(scroll.monitor || ""), searchText: "",
          targetX: Number(scroll.x), targetY: Number(scroll.y),
          targetWidth: Number(scroll.width), targetHeight: Number(scroll.height)
        })
      }
      sortSpatially(combined); baseTargets = combined.slice(); setTargetRows(combined, true)
    } else {
      var rows = Array.isArray(payload.targets) ? payload.targets : []
      for (var index = 0; index < rows.length; index++) {
        var row = rows[index]
        combined.push({
          targetId: Number(row.id), targetKind: "semantic", gridIndex: -1,
          targetMonitor: String(row.monitor || ""), searchText: "",
          targetX: Number(row.x), targetY: Number(row.y),
          targetWidth: Number(row.width), targetHeight: Number(row.height)
        })
      }
      combined = combined.concat(collectBarTargets())
      for (var fallbackIndex = 0; fallbackIndex < fallbackWindows.length; fallbackIndex++) {
        var fallback = fallbackWindows[fallbackIndex]
        combined.push({
          targetId: -100000 - fallbackIndex, targetKind: "grid-start", gridIndex: fallbackIndex,
          targetMonitor: String(fallback.monitor || ""), searchText: "",
          targetX: Number(fallback.x) + Number(fallback.width) / 2,
          targetY: Number(fallback.y) + Number(fallback.height) / 2,
          targetWidth: 1, targetHeight: 1
        })
      }
      sortSpatially(combined); baseTargets = combined.slice(); setTargetRows(combined, true)
    }
    overlayVisible = true
    if (targets.count === 0) {
      statusText = interactionMode === "search" ? "No readable text found on the active screens"
        : interactionMode === "scroll" ? "No scrollable regions found on the active screens"
        : "No controls found on the active screens"
      dismissTimer.restart()
    } else statusText = ""
  }

  function handleBackendLine(line) {
    var text = String(line || "").trim()
    if (!text) return
    var payload
    try { payload = JSON.parse(text) }
    catch (error) { showTransient("OmaJump received invalid backend data"); return }
    if (payload.type === "context") handleContext(payload)
    else if (payload.type === "targets") handleTargets(payload)
    else if (payload.type === "activated") root.dismiss()
    else if (payload.type === "scrolled") activationPending = false
    else if (payload.type === "error") {
      activationPending = false
      showTransient(payload.message || "OmaJump could not inspect this application")
    }
  }

  function updatePrefix(nextPrefix) {
    typedPrefix = nextPrefix
    var exactId = null, exactKind = "", exactGridIndex = -1, remaining = 0
    for (var index = 0; index < targets.count; index++) {
      var row = targets.get(index)
      if (row.label.indexOf(typedPrefix) === 0) remaining++
      if (row.label === typedPrefix) {
        exactId = row.targetId; exactKind = row.targetKind; exactGridIndex = row.gridIndex
      }
    }
    if (exactId !== null) activate(exactId, exactKind, exactGridIndex)
    else if (remaining === 0) typedPrefix = ""
  }

  function typeCharacter(character) {
    if (activationPending || statusText !== "") return
    if (interactionMode === "search") {
      if (character.length === 1) typedPrefix += character
      return
    }
    if (interactionMode === "scroll" && selectedScrollId >= 0) {
      if (character === "i") requestScroll(-1)
      else if (character === "k") requestScroll(1)
      return
    }
    if (alphabet.indexOf(character) !== -1) updatePrefix(typedPrefix + character)
  }

  function backspace() {
    if (activationPending || statusText !== "") return
    if (interactionMode === "search") {
      if (typedPrefix.length > 0) typedPrefix = typedPrefix.substring(0, typedPrefix.length - 1)
      return
    }
    if (interactionMode === "scroll" && selectedScrollId >= 0) {
      selectedScrollId = -1; typedPrefix = ""; setTargetRows(baseTargets, true); return
    }
    if (typedPrefix.length > 0) { updatePrefix(typedPrefix.substring(0, typedPrefix.length - 1)); return }
    if (!gridMode) return
    if (gridDepth > 0 && gridHistory.length > 0) {
      var history = gridHistory.slice(), previous = history.pop()
      gridHistory = history; gridDepth--; showGrid(previous)
    } else {
      gridMode = false; gridRect = null; gridFallbackId = -1; setTargetRows(baseTargets, true)
    }
  }

  function selectScroll(targetId) {
    for (var index = 0; index < baseTargets.length; index++) {
      var row = baseTargets[index]
      if (Number(row.targetId) !== Number(targetId)) continue
      selectedScrollId = Number(targetId); typedPrefix = ""
      var selected = Object.assign({}, row); selected.targetKind = "scroll-selected"; selected.label = ""
      setTargetRows([selected], false); return
    }
  }

  function requestScroll(direction) {
    if (!helper.running || selectedScrollId < 0 || activationPending) return
    activationPending = true
    helper.write(JSON.stringify({ type: "scroll", id: selectedScrollId, direction: direction }) + "\n")
  }

  function activate(targetId, targetKind, gridIndex) {
    if (activationPending) return
    if (targetKind === "grid-start") { startGrid(gridIndex); return }
    if (targetKind === "grid-cell") { refineGrid(gridIndex); return }
    if (targetKind === "scroll") { selectScroll(targetId); return }
    if (targetKind === "bar") {
      var barTarget = barActivations[-targetId - 1]
      if (!barTarget || typeof barTarget.triggerPress !== "function") {
        showTransient("That bar control is no longer available"); return
      }
      activationPending = true
      try { barTarget.triggerPress(Qt.LeftButton); root.dismiss() }
      catch (error) { activationPending = false; showTransient("The bar rejected the action") }
      return
    }
    if (!helper.running) return
    activationPending = true; statusText = "Activating…"
    helper.write(JSON.stringify({ type: "activate", id: targetId }) + "\n")
  }

  ListModel { id: targets }

  Process {
    id: helper
    running: false
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.handleBackendLine(line) } }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var detail = String(text || "").trim()
        if (detail && !root.stoppingHelper) console.warn("OmaJump backend:", detail)
      }
    }
    onExited: function(exitCode, exitStatus) {
      if (root.stoppingHelper) { root.stoppingHelper = false; return }
      if (root.opened) root.showTransient("OmaJump backend stopped unexpectedly")
    }
  }

  Timer { id: scanTimeout; interval: 6000; repeat: false; onTriggered: root.showTransient("OmaJump timed out while inspecting the active screens") }
  Timer { id: dismissTimer; interval: 1700; repeat: false; onTriggered: root.dismiss() }

  Variants {
    model: Quickshell.screens
    delegate: Component {
      HintOverlay {
        required property var modelData
        opened: root.overlayVisible
        activeScreen: modelData
        captureKeyboard: String(modelData.name || "") === root.inputMonitorName
        targetModel: targets
        typedPrefix: root.typedPrefix
        statusText: root.statusText
        interactionMode: root.interactionMode
        scrollSelected: root.selectedScrollId >= 0
        loading: root.scanning
        badgeBackground: Color.menu.selectedBackground
        badgeForeground: Color.menu.selectedText
        matchedForeground: Qt.darker(Color.menu.selectedText, 1.8)
        badgeBorder: Color.menu.border
        statusBackground: Color.menu.background
        statusForeground: Color.menu.text
        fontFamily: Style.font.family
        badgeFontSize: Style.font.subtitle
        cornerRadius: Style.cornerRadius

        onCharacterTyped: function(character) { root.typeCharacter(character) }
        onBackspaceRequested: root.backspace()
        onCancelRequested: root.dismiss()
        onScrollRequested: function(direction) { root.requestScroll(direction) }
      }
    }
  }
}
