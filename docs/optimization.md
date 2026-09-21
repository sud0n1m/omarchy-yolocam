# Optimization plan and measurements

## Plan

1. Establish a five-run baseline using fresh helper processes on the same S3:
   full refresh, focus-mode change, and exposure-compensation change. Verify
   every write and restore the original setting after every sample.
2. Replace whole-camera reads around a write with a fresh read of the setting
   and its controlling mode, a write, and verified readback. Mode changes also
   refresh their dependent controls. Merge these partial results into the panel;
   retain full refresh and USB fallback. Preserve the 100 ms request interval.
3. Use a small integer-control transport over the existing upstream protobuf
   schema and parameter definitions. Check response identity and status, enforce
   a deadline across fragmented replies, validate frame lengths, and close a
   failed connection. Keep unsupported individual controls from hiding working
   controls. Surface early mpv failures.
4. Cover the changed behavior with fault-injection tests, rerun the same hardware
   benchmark, check mode gating and preview formats, then install and inspect
   the real Omarchy panel. Snapshot the installed files into the restore repo.

No protobuf, mpv, NetworkManager, or V4L2 rewrite is planned: the measured cost
is dominated by redundant paced camera requests. Full refresh still needs all
15 values and is expected to remain around 1.5 seconds. There is no persistent
cache or daemon and no change to USB network configuration.

## Reproduction

From the plugin checkout, using its installed venv:

```sh
~/.local/share/omarchy-yolocam/venv/bin/python benchmarks/measure.py ./camera.py --writes --repetitions 5 --output benchmarks/after.json
```

`--writes` briefly changes focus mode and EV; run while the camera is idle.
The script restores and verifies original settings, including on failures.
Results measure complete CLI process wall time, including Python startup,
device discovery and readback, but not QML rendering. Raw samples are retained.

## Results — 2026-09-21

Same connected S3, same host and Python 3.13 / protobuf 7.36.2 environment;
five samples per operation, original values restored after each write.
Baseline: public version 0.2.1 (`386de2a`). Optimized: version 0.3.0.

| Operation | Before median | After median | Latency reduction | Camera requests before → after |
| --- | ---: | ---: | ---: | ---: |
| Focus mode | 3089.67 ms | 430.27 ms | 86.1% | 31 → 3 |
| Exposure compensation | 3090.32 ms | 489.42 ms | 84.2% | 31 → 5 |
| Full refresh | 1489.53 ms | 1489.76 ms | -0.0% | 15 → 15 |

The 0.23 ms refresh difference is noise, not a regression or gain. Focus changes
were 430–479 ms; EV changes were 480–498 ms in this run. These are small-sample
local results, not a promise for all firmware or USB links. Request counts are
established by the call paths and verified in tests. No pacing was removed.
Raw measurements: [before](../benchmarks/before.json),
[after](../benchmarks/after.json).

## Reliability verification

- Real camera responses matched type, command ID and sequence number. The new
  transport rejects unrelated replies, error bodies, malformed lengths and
  checksums; a timeout or broken frame closes the socket before any reuse.
- A single 1.5-second deadline covers the entire reply, including fragments and
  notifications. A separate 22-second operation deadline cannot be swallowed
  by normal exception handling. No heartbeat thread is needed for these short
  sessions (the upstream heartbeat interval is 30 seconds).
- A valid response that marks one control unavailable disables that control;
  it no longer discards all native controls. Missing mode values disable the
  controls that depend on them. Transport failures still use USB fallback on
  refresh, and never silently change transports for a write.
- Partial updates preserve unrelated settings and preview preferences. A fresh
  mode read recalculates dependent enablement, and changing a mode reads back
  its dependent values. Values changed externally still require Refresh.
- Preview launch observes the first 600 ms and reports early mpv errors. It
  does not monitor errors occurring later in the preview session.

Fault injection covers fragmented replies, stale/wrong replies, error status,
missing bodies, checksum/size failures, truncated connections, slow fragments,
notification floods, timeouts, readback mismatch, unavailable controls, and
mode restrictions. The JavaScript state merger has separate tests for partial
updates, transport changes, dependent enablement, and preview preservation.

Hardware regression: automatic/manual exposure correctly changed ISO/shutter/EV
availability; manual white balance enabled temperature. Original modes, USB
settings and preview preference were verified unchanged. Two frames decoded at
720p60, 1080p60 and 4K30 ([raw result](../benchmarks/hardware.json)).

The installed Omarchy panel loaded the new helper and state merger, displayed
all 15 native controls and all 10 advertised MJPEG modes, and kept ISO/shutter
and white-balance temperature disabled in their automatic modes. The panel was
visually inspected. The six state-merger tests exercise write-result merging;
interactive button writes were benchmarked through the same helper CLI, not
through automated desktop clicks.

Validation: 29 Python tests and 6 JavaScript tests pass; plugin manifest validation,
installer syntax and diff whitespace checks pass. Upstream vendor files are
unchanged. No hardware disconnect or firmware fault was induced; those paths
are tested with simulated socket failures.
