// Shared by the QML panel and the state-merge tests.
function merge(snapshot, result) {
  if (result.partial && result.controls !== undefined) {
    if (snapshot.transport !== result.transport)
      throw new Error("Camera transport changed; refresh required.");
    var updates = {};
    result.controls.forEach(function(c) { updates[c.id] = c; });
    var controls = snapshot.controls.map(function(c) { return updates[c.id] || c; });
    var values = {};
    controls.forEach(function(c) { values[c.id] = c.value; });
    controls = controls.map(function(c) {
      return c.requires ? Object.assign({}, c, {writable: c.available && values[c.requires[0]] === c.requires[1]}) : c;
    });
    return Object.assign({}, snapshot, result, {controls: controls});
  }
  if (result.controls !== undefined) return result;
  if (result.previewModes !== undefined)
    return Object.assign({}, snapshot, {previewModes: result.previewModes, previewMode: result.previewMode});
  return snapshot;
}
