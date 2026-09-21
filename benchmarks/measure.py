#!/usr/bin/env python3
"""Hardware benchmark: real CLI wall time. --writes briefly changes EV/focus.
Always restores each setting in finally and checks its final readback.
Run with the plugin's venv Python. No camera addresses/serials are recorded.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('helper', type=Path)
parser.add_argument('--repetitions', type=int, default=5)
parser.add_argument('--writes', action='store_true')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()

def invoke(*command):
    start = time.perf_counter()
    p = subprocess.run([sys.executable, '-B', str(args.helper), *command], capture_output=True, text=True, timeout=27)
    elapsed = (time.perf_counter() - start) * 1000
    data = json.loads(p.stdout)
    if p.returncode or not data['ok']:
        raise RuntimeError(data.get('message', p.stderr))
    return elapsed, data

def values(data):
    return {c['id']: c['value'] for c in data['controls']}

report = {'repetitions': args.repetitions, 'clock': 'perf_counter, fresh CLI process, milliseconds', 'operations': {}}
_, original = invoke('status')
assert original['transport'] == 'full'
initial = values(original)
report['initial_values'] = {k: initial[k] for k in ('exposure-mode', 'exposure-ev', 'focus-mode')}
for operation in ('status', 'focus-mode', 'exposure-ev') if args.writes else ('status',):
    samples = []
    if operation != 'status':
        if operation == 'exposure-ev' and initial['exposure-mode'] != 0:
            continue
        control = next(c for c in original['controls'] if c['id'] == operation)
        target = next(o['value'] for o in control['options'] if o['value'] != initial[operation]) if control['options'] else initial[operation] + 1
        if target > control['maximum']:
            target = initial[operation] - 1
    try:
        for _ in range(args.repetitions):
            if operation == 'status':
                ms, result = invoke('status')
            else:
                ms, result = invoke('set', 'full', operation, str(target))
                assert values(result)[operation] == target
                _, restored = invoke('set', 'full', operation, str(initial[operation]))
                assert values(restored)[operation] == initial[operation]
            samples.append(round(ms, 2))
    finally:
        if operation != 'status':
            _, restored = invoke('set', 'full', operation, str(initial[operation]))
            assert values(restored)[operation] == initial[operation]
    report['operations'][operation] = {'samples_ms': samples, 'median_ms': round(statistics.median(samples), 2), 'min_ms': min(samples), 'max_ms': max(samples)}
_, final = invoke('status')
report['restored'] = all(values(final)[k] == v for k, v in report['initial_values'].items())
assert report['restored']
args.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
