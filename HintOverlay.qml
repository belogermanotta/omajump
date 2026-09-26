import Quickshell
import Quickshell.Wayland
import QtQuick

PanelWindow {
  id: root

  property bool opened: false
  property var activeScreen: null
  property var targetModel: null
  property string typedPrefix: ""
  property string statusText: ""
  property bool loading: false

  property color badgeBackground: "#e6d85c"
  property color badgeForeground: "#171717"
  property color badgeBorder: "#fff3a0"
  property color statusBackground: "#e61e1e1e"
  property color statusForeground: "#f4f4f4"
  property string fontFamily: "monospace"
  property int badgeFontSize: 13
  property int cornerRadius: 4

  signal characterTyped(string character)
  signal backspaceRequested()
  signal cancelRequested()

  screen: root.activeScreen
  visible: root.opened
  anchors {
    top: true
    bottom: true
    left: true
    right: true
  }
  color: "transparent"
  exclusionMode: ExclusionMode.Ignore
  WlrLayershell.namespace: "omajump"
  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.keyboardFocus: root.opened
    ? WlrKeyboardFocus.Exclusive
    : WlrKeyboardFocus.None

  onOpenedChanged: {
    if (opened) Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  MouseArea {
    anchors.fill: parent
    onClicked: root.cancelRequested()
  }

  Item {
    id: keyCatcher
    anchors.fill: parent
    focus: true

    Keys.priority: Keys.BeforeItem
    Keys.onPressed: function(event) {
      if (event.key === Qt.Key_Escape) {
        root.cancelRequested()
        event.accepted = true
      } else if (event.key === Qt.Key_Backspace) {
        root.backspaceRequested()
        event.accepted = true
      } else if (event.text && event.text.length === 1) {
        root.characterTyped(event.text.toLowerCase())
        event.accepted = true
      }
    }
  }

  Repeater {
    model: root.targetModel

    delegate: Rectangle {
      id: badge
      required property string label
      required property string targetKind
      required property real targetX
      required property real targetY
      required property real targetWidth
      required property real targetHeight

      visible: root.opened
        && !root.loading
        && root.statusText === ""
        && label.indexOf(root.typedPrefix) === 0
      width: Math.max(labelText.implicitWidth + 10, root.badgeFontSize + 10)
      height: Math.max(labelText.implicitHeight + 6, root.badgeFontSize + 8)
      x: Math.round(Math.max(2, Math.min(root.width - width - 2,
        targetKind === "grid-cell" ? targetX + (targetWidth - width) / 2 : targetX + 2)))
      y: Math.round(Math.max(2, Math.min(root.height - height - 2,
        targetKind === "grid-cell" ? targetY + (targetHeight - height) / 2 : targetY + 2)))
      radius: root.cornerRadius
      color: root.badgeBackground
      border.width: 1
      border.color: root.badgeBorder

      Rectangle {
        visible: badge.targetKind === "grid-cell"
        x: -badge.x + badge.targetX
        y: -badge.y + badge.targetY
        width: badge.targetWidth
        height: badge.targetHeight
        color: "transparent"
        border.width: 1
        border.color: root.badgeBorder
        opacity: 0.45
      }

      Text {
        id: labelText
        anchors.centerIn: parent
        text: badge.label
        textFormat: Text.PlainText
        color: root.badgeForeground
        font.family: root.fontFamily
        font.pixelSize: root.badgeFontSize
        font.bold: true
      }
    }
  }

  Rectangle {
    visible: root.loading || root.statusText !== ""
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
      text: root.statusText || "Finding controls…"
      textFormat: Text.PlainText
      color: root.statusForeground
      font.family: root.fontFamily
      font.pixelSize: root.badgeFontSize
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.Wrap
    }
  }
}
