"""S3 regression checks; temporarily toggles modes and restores them."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camera
helper = Path(camera.__file__)
def call(*args):
    p = subprocess.run([sys.executable, '-B', str(helper), *args], capture_output=True, text=True, timeout=27)
    data = json.loads(p.stdout)
    assert p.returncode == 0 and data['ok'], data.get('message')
    return data

def controls(data):
    return {c['id']: c for c in data['controls']}

initial = controls(call('status'))
dev = camera.device()
usb_before = camera.uvc_controls(dev)
preference_before = camera.preference_path().read_bytes() if camera.preference_path().exists() else None
report = {}
try:
    manual = controls(call('set', 'full', 'exposure-mode', '1'))
    assert manual['exposure-iso']['writable'] and manual['exposure-time']['writable']
    assert not manual['exposure-ev']['writable']
    automatic = controls(call('set', 'full', 'exposure-mode', '0'))
    assert not automatic['exposure-iso']['writable'] and not automatic['exposure-time']['writable']
    assert automatic['exposure-ev']['writable']
    report['exposure_mode_gating'] = True
    manual_wb = controls(call('set', 'full', 'wb-mode', '0'))
    assert manual_wb['wb-temp']['writable']
    report['white_balance_gating'] = True
finally:
    call('set', 'full', 'wb-mode', str(initial['wb-mode']['value']))
    call('set', 'full', 'exposure-mode', str(initial['exposure-mode']['value']))
final = controls(call('status'))
report['modes_restored'] = all(final[n]['value'] == initial[n]['value'] for n in ('wb-mode', 'exposure-mode', 'focus-mode', 'exposure-ev'))
assert report['modes_restored']
report['usb_settings_unchanged'] = camera.uvc_controls(dev) == usb_before
assert report['usb_settings_unchanged']
report['preview_frames'] = {}
for mode in camera.preview_modes(dev):
    if mode['id'] not in ('1280x720@60', '1920x1080@60', '3840x2160@30'):
        continue
    cmd = camera.preview_command(dev, mode)
    cmd[1:1] = ['--vo=null', '--ao=null', '--frames=2']
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
    assert p.returncode == 0 and 'VO: [null]' in p.stdout, p.stdout + p.stderr
    report['preview_frames'][mode['id']] = 2
report['preview_preference_unchanged'] = (camera.preference_path().read_bytes() if camera.preference_path().exists() else None) == preference_before
assert report['preview_preference_unchanged']
path = Path(__file__).with_name('hardware.json')
path.write_text(json.dumps(report, indent=2) + '\n')
print(path.read_text())
