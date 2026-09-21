const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(__dirname + '/State.js', 'utf8'), context);
const merge = (s, r) => JSON.parse(JSON.stringify(context.merge(s, r)));
const snapshot = {
  transport: 'full', connected: true, previewModes: [{id: '720'}], previewMode: '720',
  controls: [
    {id: 'focus-mode', value: 3, available: true, writable: true},
    {id: 'exposure-mode', value: 0, available: true, writable: true},
    {id: 'exposure-ev', value: 8, available: true, writable: true, requires: ['exposure-mode', 0]},
    {id: 'exposure-iso', value: 500, available: true, writable: false, requires: ['exposure-mode', 1]},
  ]
};
test('a verified delta preserves unrelated controls and preview preferences', () => {
  const result = merge(snapshot, {partial: true, transport: 'full', controls: [{...snapshot.controls[0], value: 4}]});
  assert.equal(result.controls.length, 4);
  assert.equal(result.controls[0].value, 4);
  assert.deepEqual(result.controls.slice(1), snapshot.controls.slice(1));
  assert.deepEqual(result.previewModes, snapshot.previewModes);
  assert.equal(result.previewMode, '720');
  assert.equal(snapshot.controls[0].value, 3);
});
test('a fresh mode read also gates cached dependent controls', () => {
  const result = merge(snapshot, {partial: true, transport: 'full', controls: [{...snapshot.controls[1], value: 1}]});
  assert.equal(result.controls[2].writable, false);
  assert.equal(result.controls[3].writable, true);
});
test('an unavailable controlling mode disables dependents', () => {
  const result = merge(snapshot, {partial: true, transport: 'full', controls: [{...snapshot.controls[1], value: null, available: false}]});
  assert.equal(result.controls[2].writable, false);
  assert.equal(result.controls[3].writable, false);
});
test('transport mismatch cannot merge incompatible control ranges', () => {
  assert.throws(() => merge(snapshot, {partial: true, transport: 'usb', controls: []}), /transport changed/);
});
test('full refresh or error replaces stale controls', () => {
  const failed = {ok: false, connected: false, controls: []};
  assert.deepEqual(merge(snapshot, failed), failed);
  const usb = {transport: 'usb', controls: [{id: 'zoom_absolute', value: 50}]};
  assert.deepEqual(merge(snapshot, usb), usb);
});
test('preview preference response preserves current controls', () => {
  const result = merge(snapshot, {previewModes: [{id: '4k'}], previewMode: '4k'});
  assert.deepEqual(result.controls, snapshot.controls);
  assert.equal(result.previewMode, '4k');
});
