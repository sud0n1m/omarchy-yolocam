# Copyright (C) 2026 Seth Hillbrand
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Thread-safe camera client for the YoloCam S3.

Provides a high-level interface for getting/setting camera parameters over the
TCP protobuf control protocol. Network I/O runs in a background thread.

Override the _on_*() hook methods in subclasses to integrate with event systems
(e.g. Qt signals, asyncio callbacks).
"""

import logging
import socket
import struct
import threading
import time
from typing import Any, Dict, Optional

from . import yolocam_pb2 as pb
from .params import (
    PARAMS, ParamDef, format_value, is_readable, is_writable,
    zoom_mult_to_raw, zoom_raw_to_mult,
    SHUTTER_TABLE, SHUTTER_REVERSE, APERTURE_TABLE,
)
from .protocol import (
    CAMERA_IP, CAMERA_PORT, DEFAULT_TIMEOUT, OUTER_HEADER_LEN, OUTER_START_FLAG,
    MIN_REQUEST_INTERVAL, HEARTBEAT_INTERVAL, RECONNECT_THRESHOLD, REQUEST_TIMEOUT_MS,
    pack_frame, unpack_frame,
)

log = logging.getLogger(__name__)


class YoloCamClient:
    """High-level, thread-safe camera client.

    All network I/O is serialized through an internal lock. The heartbeat runs
    on a daemon thread. Override the _on_*() hook methods to receive state
    change notifications.
    """

    def __init__(
        self,
        ip: str = CAMERA_IP,
        port: int = CAMERA_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._seq = 0
        self._lock = threading.Lock()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()
        self._last_request_time = 0.0
        self._connection_state = "disconnected"

    @property
    def connection_state(self) -> str:
        return self._connection_state

    # -- Hook methods for subclass override --

    def _on_connected(self) -> None:
        """Called after successful connection."""
        pass

    def _on_disconnected(self) -> None:
        """Called after disconnection."""
        pass

    def _on_error(self, message: str) -> None:
        """Called when an error occurs."""
        pass

    def _on_param_changed(self, name: str, value: Any) -> None:
        """Called when a parameter value is read or set successfully."""
        pass

    def _on_recording_state_changed(self, recording: bool) -> None:
        """Called when recording state changes."""
        pass

    def _on_recording_duration_changed(self, duration: int) -> None:
        """Called when recording duration updates via NOTIFY."""
        pass

    # -- Connection management --

    def connect(self) -> None:
        """Open TCP connection to the camera and start heartbeat."""
        self._connection_state = "connecting"

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            self._sock = sock
            self._connection_state = "connected"
            log.info("Connected to camera at %s:%d", self.ip, self.port)
            self._on_connected()
            self._start_heartbeat()
        except (socket.error, OSError) as exc:
            self._connection_state = "disconnected"
            msg = f"Connection failed: {exc}"
            log.error(msg)
            self._on_error(msg)
            raise

    def disconnect(self) -> None:
        """Close the connection and stop heartbeat."""
        self._heartbeat_stop.set()

        # Force-shutdown the socket to unblock any thread stuck in recv().
        # Must happen BEFORE acquiring _lock since the thread holding it
        # may be blocked waiting on the socket.
        sock = self._sock

        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)
            self._heartbeat_thread = None

        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass

                self._sock = None

        self._connection_state = "disconnected"
        log.info("Disconnected from camera")
        self._on_disconnected()

    # Aliases for backward compatibility
    open = connect
    close = disconnect

    def reopen(self) -> None:
        """Drop and re-establish the connection."""
        log.info("Reconnecting to camera...")

        try:
            self.disconnect()
        except Exception:
            pass

        time.sleep(0.5)
        self.connect()

    # -- Parameter access --

    def get_param(self, name: str) -> Optional[str]:
        """Read a parameter and return its formatted display value.

        Returns None if the parameter is set-only or the camera did not respond.
        """
        pdef = PARAMS.get(name)

        if pdef is None:
            self._on_error(f"Unknown parameter: {name}")
            return None

        if not is_readable(pdef):
            return None

        with self._lock:
            try:
                resp = self._request(pdef.cid)
            except socket.timeout:
                log.warning("Timeout reading %s", name)
                return None
            except Exception as exc:
                self._on_error(f"Error reading {name}: {exc}")
                return None

        if resp is None:
            return None

        raw = self._extract_int_value(resp)

        if raw is not None:
            formatted = format_value(name, raw)
            self._on_param_changed(name, raw)
            return formatted

        # Non-integer response types (version_info, exposure_params, etc.)
        return self._format_complex_response(resp, name)

    def get_param_raw(self, name: str) -> Optional[int]:
        """Read a parameter and return the raw integer value."""
        pdef = PARAMS.get(name)

        if pdef is None or not is_readable(pdef):
            return None

        with self._lock:
            try:
                resp = self._request(pdef.cid)
            except Exception:
                return None

        if resp is None:
            return None

        return self._extract_int_value(resp)

    def set_param(self, name: str, value: Any) -> bool:
        """Set a parameter. Returns True on success (resp_code 200)."""
        pdef = PARAMS.get(name)

        if pdef is None:
            self._on_error(f"Unknown parameter: {name}")
            return False

        if not is_writable(pdef):
            self._on_error(f"Parameter '{name}' is read-only")
            return False

        if name == "zoom":
            raw = zoom_mult_to_raw(float(value))
        else:
            raw = int(value)

        with self._lock:
            try:
                resp = self._set_int(pdef.cid, raw)
            except Exception as exc:
                self._on_error(f"Error setting {name}: {exc}")
                return False

        if resp is None:
            return False

        success = resp.resp_code == 200

        if success:
            self._on_param_changed(name, raw)

        return success

    def get_all_params(self) -> Dict[str, Any]:
        """Read all readable parameters, reconnecting after consecutive timeouts."""
        results: Dict[str, Any] = {}
        consecutive_timeouts = 0

        for name, pdef in PARAMS.items():
            if not is_readable(pdef):
                continue

            with self._lock:
                try:
                    resp = self._request(pdef.cid, recv_timeout=3.0)
                    raw = self._extract_int_value(resp) if resp else None
                    results[name] = raw
                    consecutive_timeouts = 0
                except socket.timeout:
                    results[name] = None
                    consecutive_timeouts += 1

                    if consecutive_timeouts >= RECONNECT_THRESHOLD:
                        self._reconnect_socket()
                        consecutive_timeouts = 0
                except Exception as exc:
                    log.warning("Error reading %s: %s", name, exc)
                    results[name] = None

        return results

    # -- Compound commands --

    def set_wb_offset(self, red: int, blue: int) -> bool:
        """Set white balance offset using compound WhiteBalanceOffset message."""
        with self._lock:
            self._throttle()
            msg = pb.Message()
            msg.type = pb.TYPE_SET
            msg.seq = self._next_seq()
            msg.timeout = REQUEST_TIMEOUT_MS
            msg.id = pb.CID_WHITE_BALANCE_OFFSET
            msg.body.white_balance_offset.offset_r = red
            msg.body.white_balance_offset.offset_b = blue

            try:
                self._send_message(msg)
                resp = self._recv_message()
                return resp is not None and resp.resp_code == 200
            except Exception as exc:
                self._on_error(f"Error setting WB offset: {exc}")
                return False

    def start_recording(self) -> bool:
        """Start recording to SD card."""
        with self._lock:
            try:
                resp = self._set_int(pb.CID_RECORDING_START, 1)
            except Exception as exc:
                self._on_error(f"Error starting recording: {exc}")
                return False

        success = resp is not None and resp.resp_code == 200

        if success:
            self._on_recording_state_changed(True)

        return success

    def stop_recording(self) -> bool:
        """Stop recording."""
        with self._lock:
            try:
                resp = self._set_int(pb.CID_RECORDING_STOP, 1)
            except Exception as exc:
                self._on_error(f"Error stopping recording: {exc}")
                return False

        success = resp is not None and resp.resp_code == 200

        if success:
            self._on_recording_state_changed(False)

        return success

    def set_ai_tracking(self, mode: int) -> bool:
        """Set AI tracking mode (0=Portrait, 1=Thing, 2=Off)."""
        with self._lock:
            self._throttle()
            msg = pb.Message()
            msg.type = pb.TYPE_SET
            msg.seq = self._next_seq()
            msg.timeout = REQUEST_TIMEOUT_MS
            msg.id = pb.CID_AI_TRACKING_CTRL
            msg.body.ai_tracking_ctrl.mode = mode

            try:
                self._send_message(msg)
                resp = self._recv_message()
                return resp is not None and resp.resp_code == 200
            except Exception as exc:
                self._on_error(f"Error setting AI tracking: {exc}")
                return False

    def set_clut_enabled(self, enabled: bool) -> bool:
        """Enable or disable color LUT."""
        with self._lock:
            try:
                resp = self._set_int(pb.CID_ENABLE_CLUT, 1 if enabled else 0)
            except Exception as exc:
                self._on_error(f"Error setting CLUT: {exc}")
                return False

        return resp is not None and resp.resp_code == 200

    def set_clut_attr(self, value: int) -> bool:
        """Set CLUT attribute/intensity (0-255)."""
        with self._lock:
            try:
                resp = self._set_int(pb.CID_CLUT_ATTR, value)
            except Exception as exc:
                self._on_error(f"Error setting CLUT attr: {exc}")
                return False

        return resp is not None and resp.resp_code == 200

    def send_clut(self, clut_data: list, hsv_data: str = "{}",
                  extra: str = "", updataflag: str = "0") -> bool:
        """Send a 5508-entry CLUT to the camera.

        clut_data: list of 5508 uint32 values (packed 10-bit RGB deltas)
        hsv_data: JSON string of HSV grid state for round-trip fidelity
        extra: mode identifier ("pro", "origin", or JSON drag history)
        updataflag: "0" for user edit, "1" for preset
        """
        with self._lock:
            self._throttle()
            msg = pb.Message()
            msg.type = pb.TYPE_SET
            msg.seq = self._next_seq()
            msg.timeout = REQUEST_TIMEOUT_MS
            msg.id = pb.CID_CLUT_FILE
            msg.body.tuning_clut_file.clut_size = len(clut_data)
            msg.body.tuning_clut_file.clut_data.extend(clut_data)
            msg.body.tuning_clut_file.hsv_data = hsv_data
            msg.body.tuning_clut_file.extra = extra
            msg.body.tuning_clut_file.updataflag = updataflag

            try:
                self._send_message(msg)
                resp = self._recv_message()
                return resp is not None and resp.resp_code == 200
            except Exception as exc:
                self._on_error(f"Error sending CLUT: {exc}")
                return False

    def reset_clut(self) -> bool:
        """Send an identity (all-zero) CLUT to reset color grading."""
        return self.send_clut([0] * 5508)

    def set_tuning(self, brightness: int, curve: float, color_temp: int) -> bool:
        """Set image tuning parameters."""
        with self._lock:
            self._throttle()
            msg = pb.Message()
            msg.type = pb.TYPE_SET
            msg.seq = self._next_seq()
            msg.timeout = REQUEST_TIMEOUT_MS
            msg.id = pb.CID_TUNING
            msg.body.tuning_param.brightness = brightness
            msg.body.tuning_param.curve = curve
            msg.body.tuning_param.color_temp = color_temp

            try:
                self._send_message(msg)
                resp = self._recv_message()
                return resp is not None and resp.resp_code == 200
            except Exception as exc:
                self._on_error(f"Error setting tuning: {exc}")
                return False

    def format_sd_card(self) -> bool:
        """Format the SD card."""
        with self._lock:
            try:
                resp = self._set_int(pb.CID_DISK_FORMAT, 1)
            except Exception as exc:
                self._on_error(f"Error formatting SD card: {exc}")
                return False

        return resp is not None and resp.resp_code == 200

    def set_exposure(
        self,
        iso: Optional[int] = None,
        shutter_us: Optional[int] = None,
        aperture: Optional[int] = None,
    ) -> Optional[pb.Message]:
        """Send manual exposure parameters as a compound message."""
        with self._lock:
            self._throttle()
            msg = pb.Message()
            msg.type = pb.TYPE_SET
            msg.seq = self._next_seq()
            msg.timeout = REQUEST_TIMEOUT_MS
            msg.id = pb.CID_EXPOSURE_MODE

            params = pb.ManualExposureParams()

            if iso is not None:
                params.iso = iso

            if shutter_us is not None:
                params.shutter = shutter_us

            if aperture is not None:
                params.aperture = aperture

            msg.body.exposure_params.CopyFrom(params)
            self._send_message(msg)
            return self._recv_message()

    # -- Raw protocol access --

    def request(self, command_id: int, recv_timeout: Optional[float] = None) -> Optional[pb.Message]:
        """Send a raw REQUEST and return the parsed response."""
        with self._lock:
            return self._request(command_id, recv_timeout)

    def set_int(self, command_id: int, value: int) -> Optional[pb.Message]:
        """Send a raw SET command with an integer value."""
        with self._lock:
            return self._set_int(command_id, value)

    def start_heartbeat(self) -> None:
        """Start the heartbeat thread (if not already running)."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._start_heartbeat()

    def stop_heartbeat(self) -> None:
        """Stop the heartbeat thread."""
        self._stop_heartbeat()

    # -- Internal protocol methods (must be called with self._lock held) --

    def _throttle(self) -> None:
        """Enforce minimum delay between requests to avoid overwhelming the camera."""
        elapsed = time.monotonic() - self._last_request_time

        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

        self._last_request_time = time.monotonic()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _send_message(self, msg: pb.Message) -> None:
        if self._sock is None:
            raise ConnectionError("Not connected to camera")

        payload = msg.SerializeToString()
        frame = pack_frame(payload)
        self._sock.sendall(frame)

    _MAX_NOTIFY_DRAIN = 50

    def _recv_message(self) -> Optional[pb.Message]:
        """Read the next non-NOTIFY message, dispatching any NOTIFYs encountered.

        Caps the number of consecutive NOTIFYs drained to _MAX_NOTIFY_DRAIN to
        prevent holding the lock indefinitely when the camera is streaming
        high-frequency notifications (e.g. face bounding boxes at 30fps).
        """
        for _ in range(self._MAX_NOTIFY_DRAIN):
            msg = self._recv_one_message()

            if msg is None:
                return None

            if msg.type == pb.TYPE_NOTIFY:
                self._handle_notify(msg)
                continue

            return msg

        log.warning("Exceeded NOTIFY drain limit (%d), giving up on response", self._MAX_NOTIFY_DRAIN)
        return None

    def _recv_one_message(self) -> Optional[pb.Message]:
        if self._sock is None:
            return None

        outer = self._recv_exact(OUTER_HEADER_LEN)

        if not outer:
            return None

        if outer[0] != OUTER_START_FLAG:
            raise ValueError(f"Bad outer start flag: 0x{outer[0]:02x}")

        inner_size = struct.unpack_from('<I', outer, 4)[0]

        if inner_size <= 0 or inner_size > 1024 * 1024:
            raise ValueError(f"Invalid inner_size: {inner_size}")

        inner = self._recv_exact(inner_size)

        if not inner:
            return None

        full_frame = outer + inner
        proto_data = unpack_frame(full_frame)
        resp = pb.Message()
        resp.ParseFromString(proto_data)
        return resp

    _SILENT_NOTIFY_CIDS = frozenset({
        pb.CID_FACE_BOUNDING_BOXES,
        pb.CID_STREAM_HEARTBEAT,
    })

    def _handle_notify(self, msg: pb.Message) -> None:
        """Dispatch an unsolicited NOTIFY message from the camera."""
        if msg.id == pb.CID_DURATION_CHANGED:
            duration = self._extract_int_value(msg)

            if duration is not None:
                self._on_recording_duration_changed(duration)
            else:
                log.debug("NOTIFY CID_DURATION_CHANGED with no int_val")

        elif msg.id == pb.CID_RECORDING_FINISHED:
            self._on_recording_state_changed(False)

        elif msg.id not in self._SILENT_NOTIFY_CIDS:
            log.debug("Unhandled NOTIFY CID %d", msg.id)

    def _recv_exact(self, n: int) -> Optional[bytes]:
        if self._sock is None:
            return None

        buf = bytearray()

        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))

            if not chunk:
                return None

            buf.extend(chunk)

        return bytes(buf)

    def _flush_recv_buffer(self) -> None:
        if self._sock is None:
            return

        self._sock.setblocking(False)

        try:
            while True:
                try:
                    data = self._sock.recv(4096)

                    if not data:
                        break
                except BlockingIOError:
                    break
        finally:
            self._sock.setblocking(True)
            self._sock.settimeout(self.timeout)

    def _request(self, command_id: int, recv_timeout: Optional[float] = None) -> Optional[pb.Message]:
        self._throttle()

        msg = pb.Message()
        msg.type = pb.TYPE_REQUEST
        msg.seq = self._next_seq()
        msg.timeout = REQUEST_TIMEOUT_MS
        msg.id = command_id

        if recv_timeout is not None and self._sock is not None:
            self._sock.settimeout(recv_timeout)

        try:
            self._send_message(msg)
            return self._recv_message()
        except socket.timeout:
            self._flush_recv_buffer()
            raise
        finally:
            if recv_timeout is not None and self._sock is not None:
                self._sock.settimeout(self.timeout)

    def _set_int(self, command_id: int, value: int) -> Optional[pb.Message]:
        self._throttle()

        msg = pb.Message()
        msg.type = pb.TYPE_SET
        msg.seq = self._next_seq()
        msg.timeout = REQUEST_TIMEOUT_MS
        msg.id = command_id
        msg.body.int_val = value
        self._send_message(msg)
        return self._recv_message()

    def _reconnect_socket(self) -> None:
        """Low-level socket reconnect (no lock, no heartbeat management)."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

        time.sleep(0.5)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.ip, self.port))
        self._sock = sock
        log.info("Socket reconnected")

    # -- Heartbeat --

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()

        def heartbeat_loop() -> None:
            while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL):
                try:
                    with self._lock:
                        self._set_int(pb.CID_STREAM_HEARTBEAT, 1)
                except Exception:
                    break

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True, name="yolocam-heartbeat")
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()

        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)
            self._heartbeat_thread = None

    # -- Response parsing helpers --

    @staticmethod
    def _extract_int_value(resp: pb.Message) -> Optional[int]:
        if not resp.HasField("body"):
            return None

        body = resp.body
        which = body.WhichOneof("value")

        if which == "int_val":
            return body.int_val

        return None

    def _format_complex_response(self, resp: pb.Message, name: str) -> Optional[str]:
        """Format non-integer response bodies into display strings."""
        if not resp.HasField("body"):
            return None

        body = resp.body
        which = body.WhichOneof("value")

        if which == "version_info":
            vi = body.version_info
            return f"{vi.sys_version} (S/N: {vi.serial_num})"

        if which == "exposure_params":
            ep = body.exposure_params
            parts = [f"ISO {ep.iso}"]

            if ep.shutter in SHUTTER_REVERSE:
                parts.append(f"1/{SHUTTER_REVERSE[ep.shutter]}s")
            else:
                parts.append(f"{ep.shutter}us")

            if ep.aperture in APERTURE_TABLE:
                parts.append(APERTURE_TABLE[ep.aperture])

            return ", ".join(parts)

        if which == "range":
            return f"{body.range.min}-{body.range.max}"

        if which == "lens_detail":
            ld = body.lens_detail
            return f"ID:{ld.lens_id} {APERTURE_TABLE.get(ld.aperture, '')} focal:{ld.focal}"

        if which == "disk_info":
            di = body.disk_info
            return f"{di.available_size}/{di.total_size}MB"

        if which == "lens_version_info":
            lvi = body.lens_version_info
            return f"{lvi.current_version}"

        if which == "video_status":
            return str(body.video_status.status)

        if which == "stream_param":
            sp = body.stream_param

            if sp.HasField("venc_param"):
                v = sp.venc_param
                return f"{v.width}x{v.height}@{v.rate}fps"

            return "stream config"

        if which == "str_val":
            return body.str_val

        return str(which)
