import Quickshell
import QtQuick

ShellRoot {
  id: root

  property string typedPrefix: ""

  ListModel {
    id: fixtures
    ListElement { targetId: 0; targetKind: "semantic"; label: "a"; targetX: 80; targetY: 90; targetWidth: 120; targetHeight: 34 }
    ListElement { targetId: 1; targetKind: "semantic"; label: "s"; targetX: 390; targetY: 180; targetWidth: 90; targetHeight: 36 }
    ListElement { targetId: 2; targetKind: "semantic"; label: "d"; targetX: 700; targetY: 330; targetWidth: 160; targetHeight: 44 }
    ListElement { targetId: 3; targetKind: "semantic"; label: "fa"; targetX: 260; targetY: 520; targetWidth: 240; targetHeight: 52 }
    ListElement { targetId: 4; targetKind: "semantic"; label: "fs"; targetX: 910; targetY: 620; targetWidth: 110; targetHeight: 32 }
  }

  HintOverlay {
    opened: true
    activeScreen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    targetModel: fixtures
    typedPrefix: root.typedPrefix
    statusText: ""
    loading: false

    onCharacterTyped: function(character) { root.typedPrefix += character }
    onBackspaceRequested: root.typedPrefix = root.typedPrefix.substring(0, root.typedPrefix.length - 1)
    onCancelRequested: Qt.quit()
  }

  Timer {
    interval: 350
    running: Quickshell.env("OMAJUMP_SMOKE_TEST") === "1"
    repeat: false
    onTriggered: Qt.quit()
  }
}
