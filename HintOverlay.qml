import Quickshell
import Quickshell.Wayland
import QtQuick

PanelWindow {
  id: root

  property bool opened: false
  property var activeScreen: null
  property string screenName: activeScreen ? String(activeScreen.name || "") : ""
  property bool captureKeyboard: false
  property var targetModel: null
  property string typedPrefix: ""
  property string statusText: ""
  property string interactionMode: "hints"
  property bool scrollSelected: false
  property bool loading: false

  property color badgeBackground: "#e6d85c"
  property color badgeForeground: "#171717"
  property color matchedForeground: "#777777"
  property color badgeBorder: "#fff3a0"
  property color statusBackground: "#e61e1e1e"
  property color statusForeground: "#f4f4f4"
  property string fontFamily: "monospace"
  property int badgeFontSize: 13
  property int cornerRadius: 4

  signal characterTyped(string character)
  signal backspaceRequested()
  signal cancelRequested()
  signal scrollRequested(int direction)

  screen: root.activeScreen
  visible: root.opened
  anchors { top: true; bottom: true; left: true; right: true }
  color: "transparent"
  exclusionMode: ExclusionMode.Ignore
  WlrLayershell.namespace: "omajump-" + root.screenName
  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.keyboardFocus: root.opened && root.captureKeyboard
    ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

  onOpenedChanged: {
    if (opened && captureKeyboard) Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }
  onCaptureKeyboardChanged: {
    if (opened && captureKeyboard) Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  MouseArea { anchors.fill: parent; onClicked: root.cancelRequested() }

  Item {
    id: keyCatcher
    anchors.fill: parent
    focus: root.captureKeyboard

    Keys.priority: Keys.BeforeItem
    Keys.onPressed: function(event) {
      if (!root.captureKeyboard) return
      if (event.key === Qt.Key_Escape) {
        root.cancelRequested(); event.accepted = true
      } else if (event.key === Qt.Key_Backspace) {
        root.backspaceRequested(); event.accepted = true
      } else if (root.interactionMode === "scroll" && root.scrollSelected
          && (event.key === Qt.Key_Up || event.key === Qt.Key_Down)) {
        root.scrollRequested(event.key === Qt.Key_Up ? -1 : 1); event.accepted = true
      } else if (event.text && event.text.length === 1) {
        root.characterTyped(event.text.toLowerCase()); event.accepted = true
      }
    }
  }

  Repeater {
    model: root.targetModel

    delegate: Item {
      id: targetItem
      required property string label
      required property string targetKind
      required property string targetMonitor
      required property string searchText
      required property real targetX
      required property real targetY
      required property real targetWidth
      required property real targetHeight

      readonly property bool isSearch: targetKind === "search"
      readonly property bool isRegion: targetKind === "scroll" || targetKind === "scroll-selected"
      readonly property bool isGrid: targetKind === "grid-cell"
      readonly property bool queryMatches: root.typedPrefix.length >= 3
        && searchText.toLowerCase().indexOf(root.typedPrefix.toLowerCase()) !== -1
      visible: root.opened && !root.loading && root.statusText === ""
        && targetMonitor === root.screenName
        && (isSearch ? queryMatches : (isRegion || label.indexOf(root.typedPrefix) === 0))
      anchors.fill: parent

      Rectangle {
        visible: targetItem.isSearch || targetItem.isRegion || targetItem.isGrid
        x: Math.round(targetItem.targetX)
        y: Math.round(targetItem.targetY)
        width: Math.max(1, Math.round(targetItem.targetWidth))
        height: Math.max(1, Math.round(targetItem.targetHeight))
        radius: root.cornerRadius
        color: targetItem.isSearch ? Qt.rgba(1.0, 0.85, 0.15, 0.24)
          : targetItem.targetKind === "scroll-selected" ? Qt.rgba(0.25, 0.75, 1.0, 0.24)
          : "transparent"
        border.width: targetItem.targetKind === "scroll-selected" ? 3 : 2
        border.color: targetItem.isSearch ? root.badgeBackground
          : targetItem.targetKind === "scroll-selected" ? "#61c8ff" : root.badgeBorder
        opacity: targetItem.isGrid ? 0.55 : 0.9
      }

      Rectangle {
        id: badge
        visible: !targetItem.isSearch && targetItem.targetKind !== "scroll-selected"
        width: Math.max(labelRow.implicitWidth + 10, root.badgeFontSize + 10)
        height: Math.max(labelRow.implicitHeight + 6, root.badgeFontSize + 8)
        x: Math.round(Math.max(2, Math.min(root.width - width - 2,
          targetItem.isGrid ? targetItem.targetX + (targetItem.targetWidth - width) / 2
                            : targetItem.targetX + 2)))
        y: Math.round(Math.max(2, Math.min(root.height - height - 2,
          targetItem.isGrid ? targetItem.targetY + (targetItem.targetHeight - height) / 2
                            : targetItem.targetY + 2)))
        radius: root.cornerRadius
        color: root.badgeBackground
        border.width: 1
        border.color: root.badgeBorder

        Row {
          id: labelRow
          anchors.centerIn: parent

          Text {
            text: targetItem.label.substring(0, Math.min(root.typedPrefix.length, targetItem.label.length))
            color: root.matchedForeground
            font.family: root.fontFamily
            font.pixelSize: root.badgeFontSize
            font.bold: true
          }
          Text {
            text: targetItem.label.substring(Math.min(root.typedPrefix.length, targetItem.label.length))
            color: root.badgeForeground
            font.family: root.fontFamily
            font.pixelSize: root.badgeFontSize
            font.bold: true
          }
        }
      }
    }
  }

  Rectangle {
    visible: root.captureKeyboard && (root.loading || root.statusText !== "")
    anchors.centerIn: parent
    width: Math.min(root.width - 32, Math.max(220, statusLabel.implicitWidth + 32))
    height: Math.max(54, statusLabel.implicitHeight + 24)
    radius: root.cornerRadius
    color: root.statusBackground
    border.width: 1
    border.color: root.badgeBorder

    Text {
      id: statusLabel
      anchors.centerIn: parent
      width: Math.min(implicitWidth, parent.width - 24)
      text: root.statusText || "Finding controls on all screens…"
      color: root.statusForeground
      font.family: root.fontFamily
      font.pixelSize: root.badgeFontSize
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.Wrap
    }
  }

  Rectangle {
    visible: root.opened && root.captureKeyboard && !root.loading && root.statusText === ""
      && (root.interactionMode === "search" || root.interactionMode === "scroll")
    anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 18 }
    width: Math.min(root.width - 32, promptLabel.implicitWidth + 28)
    height: promptLabel.implicitHeight + 18
    radius: root.cornerRadius
    color: root.statusBackground
    border.width: 1
    border.color: root.badgeBorder

    Text {
      id: promptLabel
      anchors.centerIn: parent
      text: root.interactionMode === "search"
        ? (root.typedPrefix.length < 3
          ? "Search: " + root.typedPrefix + "  (type at least 3 characters)"
          : "Search: " + root.typedPrefix)
        : (root.scrollSelected
          ? "Scroll with ↑/↓ or i/k · Backspace chooses another region"
          : "Choose a scrollable region")
      color: root.statusForeground
      font.family: root.fontFamily
      font.pixelSize: root.badgeFontSize
    }
  }
}
