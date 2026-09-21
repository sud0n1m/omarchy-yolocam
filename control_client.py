"""Short-lived S3 integer controls; upstream schema/framing, bounded exchanges.

The helper serializes operations with its process lock. Sessions last less than
22 seconds, so they do not need the upstream 30-second heartbeat thread.
"""
import socket
import struct
import time

from yolocam import yolocam_pb2 as pb
from yolocam.params import PARAMS
from yolocam.protocol import pack_frame, unpack_frame


class ControlClient:
    def __init__(self, ip, timeout=1.5):
        self.ip = ip
        self.timeout = timeout
        self.sock = None
        self.sequence = 0
        self.last_request = 0.0
        self.deadline = 0.0

    def connect(self):
        self.sock = socket.create_connection((self.ip, 12345), timeout=self.timeout)

    def disconnect(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Camera response timed out.")
        self.sock.settimeout(remaining)

    def _read(self, size):
        data = bytearray()
        while len(data) < size:
            self._remaining()
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("Camera connection closed during a reply.")
            data.extend(chunk)
        return bytes(data)

    def _receive(self):
        outer = self._read(8)
        size = struct.unpack_from('<I', outer, 4)[0]
        if outer[0] != 0x08 or not 8 <= size <= 1024 * 1024:
            raise ValueError("Invalid camera frame header.")
        inner = self._read(size)
        if struct.unpack_from('>I', inner, 1)[0] + 8 != size:
            raise ValueError("Camera frame lengths disagree.")
        return pb.Message.FromString(unpack_frame(outer + inner))

    def _exchange(self, name, value=None):
        if self.sock is None:
            raise ConnectionError("Camera is not connected.")
        delay = 0.1 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        self.deadline = self.last_request + self.timeout
        self.sequence += 1
        request = pb.Message(type=pb.TYPE_REQUEST if value is None else pb.TYPE_SET,
                             id=PARAMS[name].cid, seq=self.sequence, timeout=3000)
        if value is not None:
            request.body.int_val = value
        try:
            self._remaining()
            self.sock.sendall(pack_frame(request.SerializeToString()))
            for _ in range(50):
                response = self._receive()
                if response.type == pb.TYPE_NOTIFY:
                    continue
                if (response.type, response.id, response.seq) == (pb.TYPE_RESPONSE, request.id, request.seq):
                    return response
            raise RuntimeError("Camera did not return a matching response.")
        except BaseException:
            # Never reuse a stream after timeout, truncation, or malformed data.
            self.disconnect()
            raise

    def get_param_raw(self, name):
        response = self._exchange(name)
        if response.resp_code != 200 or not response.HasField('body') or response.body.WhichOneof('value') != 'int_val':
            return None
        return response.body.int_val

    def set_param(self, name, raw):
        return self._exchange(name, int(raw)).resp_code == 200
