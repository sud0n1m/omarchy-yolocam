import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

Panel {
  id: root
  moduleName: "sudonim.yolocam"
  ipcTarget: "yolocam"
  manageIpc: false
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  readonly property string helper: decodeURIComponent(String(Qt.resolvedUrl("camera.py")).replace(/^file:\/\//, ""))
  readonly property string python: Quickshell.env("HOME") + "/.local/share/omarchy-yolocam/venv/bin/python"
  property var snapshot: ({connected: false, controls: []})
  property string message: "Open to read camera settings."
  property bool failed: false
  readonly property bool busy: worker.running
  readonly property color secondary: Util.alpha(Color.foreground, 0.7)
  readonly property var modes: snapshot.previewModes || []
  readonly property var selectedMode: modes.filter(function(m) { return m.id === root.snapshot.previewMode; })[0] || ({})

  function resolutions() {
    var sizes = [];
    modes.forEach(function(m) { var size = m.width + "x" + m.height; if (sizes.indexOf(size) < 0) sizes.push(size); });
    return sizes;
  }
  function selectResolution(size) {
    var choices = modes.filter(function(m) { return m.width + "x" + m.height === size; });
    var next = choices.filter(function(m) { return m.fps === root.selectedMode.fps; })[0]
      || choices.filter(function(m) { return m.fps === 30; })[0] || choices[0];
    if (next) run(["preview-mode", next.id]);
  }

  function run(args) {
    if (busy) return "Busy";
    message = args[0] === "set" ? "Applying setting…" : "Reading camera…";
    worker.command = [python, "-B", helper].concat(args);
    worker.running = true;
    return "Requested";
  }
  function setValue(control, value) { run(["set", snapshot.transport, control.id, String(Math.round(value))]); }
  function display(control, value) {
    if (value < control.minimum || value > control.maximum) return "Unknown";
    if (control.id === "zoom" || control.id === "zoom_absolute") return ((value - 50) / 16 + 1).toFixed(2) + "×";
    if (control.id === "wb-temp" || control.id === "white_balance_temperature") return Math.round(value) + " K";
    return String(Math.round(value));
  }
  onOpenedChanged: if (opened) { run(["status"]); Qt.callLater(function() { neutral.forceActiveFocus(); }); }
  IpcHandler {
    target: "yolocam"
    function open(): void { root.open(); }
    function close(): void { root.close(); }
    function toggle(): void { root.toggle(); }
    function refresh(): string { return root.run(["status"]); }
    function previewMode(mode: string): string { return root.run(["preview-mode", mode]); }
    function state(): string { return JSON.stringify({snapshot: root.snapshot, message: root.message, busy: root.busy, failed: root.failed}); }
  }
  Process {
    id: worker
    stdout: StdioCollector { id: output }
    stderr: StdioCollector { }
    onExited: function(code) {
      try {
        var result = JSON.parse(output.text);
        root.failed = !result.ok;
        root.message = result.message || "Camera ready.";
        if (result.controls !== undefined) root.snapshot = result;
        else if (result.previewModes !== undefined) root.snapshot = Object.assign({}, root.snapshot, {previewModes: result.previewModes, previewMode: result.previewMode});
      } catch (e) {
        root.failed = true;
        root.snapshot = {connected: false, controls: []};
        root.message = "Camera helper could not start. Run install.sh in the YoloCam plugin folder.";
      }
    }
  }
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰄀"
    onPressed: root.toggle()
  }
  KeyboardPanel {
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: neutral
    contentWidth: fittedContentWidth(Style.space(390))
    contentHeight: fittedContentHeight(content.implicitHeight, Style.space(680))
    FocusScope {
      anchors.fill: parent
      Item { id: neutral; focus: true }
      Keys.onEscapePressed: root.close()
      Controls.ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
        Column {
          id: content
          width: scroll.availableWidth
          spacing: Style.space(16)
          Column {
            width: parent.width
            spacing: Style.space(5)
            Label { text: "YoloCam S3"; font.pixelSize: Style.font.title; font.bold: true }
            Label {
              width: parent.width
              text: root.busy ? "CONNECTING…" : !root.snapshot.connected ? "DISCONNECTED" : root.snapshot.transport === "full" ? "FULL CAMERA CONTROLS" : "USB CAMERA CONTROLS"
              font.pixelSize: Style.font.caption; color: root.secondary
            }
          }
          Label { width: parent.width; text: root.message; wrapMode: Text.WordWrap; color: root.failed ? Color.accent : root.secondary }
          Row {
            width: parent.width
            spacing: Style.space(8)
            Action { width: (parent.width - parent.spacing) / 2; text: "Refresh"; enabled: !root.busy; onClicked: root.run(["status"]) }
            Action { width: (parent.width - parent.spacing) / 2; text: "Open preview"; enabled: !root.busy && root.snapshot.connected; onClicked: root.run(["preview"]) }
          }
          Column {
            width: parent.width
            spacing: Style.space(8)
            visible: root.modes.length > 0
            enabled: !root.busy
            Label { text: "Preview resolution"; font.bold: true }
            Flow {
              width: parent.width
              spacing: Style.space(5)
              Repeater {
                model: root.resolutions()
                Action {
                  required property string modelData
                  text: modelData === "3840x2160" ? "4K" : modelData === "1920x1080" ? "1080p" : modelData === "1280x720" ? "720p" : modelData
                  selected: root.selectedMode.width + "x" + root.selectedMode.height === modelData
                  onClicked: root.selectResolution(modelData)
                }
              }
            }
            Flow {
              width: parent.width
              spacing: Style.space(5)
              Repeater {
                model: root.modes.filter(function(m) { return m.width === root.selectedMode.width && m.height === root.selectedMode.height; })
                Action {
                  required property var modelData
                  text: modelData.fps + " fps"
                  selected: modelData.id === root.snapshot.previewMode
                  onClicked: root.run(["preview-mode", modelData.id])
                }
              }
            }
            Label {
              width: parent.width
              text: "Applies when you open a new preview. Zoom, OBS and browser calls choose their own capture resolution."
              wrapMode: Text.WordWrap; font.pixelSize: Style.font.caption; color: root.secondary
            }
          }
          Repeater {
            model: root.snapshot.controls || []
            Column {
              id: control
              required property var modelData
              width: content.width
              spacing: Style.space(7)
              enabled: !root.busy && modelData.writable
              opacity: enabled ? 1 : 0.45
              Row {
                width: parent.width
                Label { width: parent.width * 0.68; text: control.modelData.label; font.bold: true }
                Label {
                  width: parent.width * 0.32
                  visible: control.modelData.kind === "slider"
                  text: root.display(control.modelData, slider.dragging ? slider.liveValue : control.modelData.value)
                  horizontalAlignment: Text.AlignRight; color: root.secondary
                }
              }
              Flow {
                width: parent.width
                spacing: Style.space(5)
                visible: control.modelData.kind === "choice"
                Repeater {
                  model: control.modelData.options
                  Action {
                    required property var modelData
                    text: modelData.label
                    selected: modelData.value === control.modelData.value
                    onClicked: root.setValue(control.modelData, modelData.value)
                  }
                }
              }
              FocusScope {
                width: parent.width
                height: visible ? slider.implicitHeight : 0
                visible: control.modelData.kind === "slider"
                activeFocusOnTab: visible && enabled
                Keys.onLeftPressed: root.setValue(control.modelData, Math.max(control.modelData.minimum, control.modelData.value - control.modelData.step))
                Keys.onRightPressed: root.setValue(control.modelData, Math.min(control.modelData.maximum, control.modelData.value + control.modelData.step))
                Rectangle { anchors.fill: parent; color: "transparent"; border.width: 1; border.color: parent.activeFocus ? Color.accent : "transparent" }
                PanelSlider {
                  id: slider
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(6); anchors.rightMargin: Style.space(6)
                  bar: root.bar
                  minimum: control.modelData.minimum
                  maximum: control.modelData.maximum
                  value: control.modelData.value
                  integer: true
                  step: control.modelData.step
                  onReleased: function(v) { root.setValue(control.modelData, v); }
                }
              }
            }
          }
          Label {
            width: parent.width
            text: "Changes apply to the camera and affect video apps. Preview needs the camera to be free; close Zoom or other camera apps first."
            wrapMode: Text.WordWrap
            font.pixelSize: Style.font.caption; color: root.secondary
          }
        }
      }
    }
  }
  component Label: Text {
    font.family: root.bar ? root.bar.fontFamily : Style.font.family
    font.pixelSize: Style.font.body
    color: Color.foreground
  }
  component Action: Button {
    fontSize: Style.font.caption
    fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
    foreground: Color.foreground
    bordered: true
    focusable: true
    horizontalPadding: Style.space(10)
    opacity: enabled ? 1 : 0.5
  }
}
