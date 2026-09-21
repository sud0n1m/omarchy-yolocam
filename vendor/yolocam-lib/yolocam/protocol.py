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

"""Wire format for the YoloCam S3 SocketPacketPolicy dual-envelope protocol.

Outer (8 bytes):  [0x08] [client_type] [0x00] [0x00] [LE32 inner_size]
Inner (6 bytes):  [0xA5] [BE32 proto_size] [XOR header_checksum]
Payload (N bytes): serialized protobuf
Trailer (2 bytes): [XOR data_checksum] [0x5A]
"""

import struct
from typing import Union

OUTER_START_FLAG = 0x08
CLIENT_TYPE = 0x01
INNER_START_FLAG = 0xA5
INNER_END_FLAG = 0x5A
OUTER_HEADER_LEN = 8

CAMERA_IP = "192.168.126.10"
CAMERA_PORT = 12345
DEFAULT_TIMEOUT = 5.0
REQUEST_TIMEOUT_MS = 3000
MIN_REQUEST_INTERVAL = 0.1
HEARTBEAT_INTERVAL = 30.0
RECONNECT_THRESHOLD = 2


def xor_checksum(data: Union[bytes, bytearray]) -> int:
    result = 0

    for b in data:
        result ^= b

    return result


def pack_frame(payload: bytes) -> bytes:
    """Pack a serialized protobuf into the SocketPacketPolicy dual-envelope wire format."""
    proto_size = len(payload)
    inner_size = proto_size + 8

    outer = bytes([OUTER_START_FLAG, CLIENT_TYPE, 0x00, 0x00]) + struct.pack('<I', inner_size)

    inner_header = bytes([INNER_START_FLAG]) + struct.pack('>I', proto_size)
    header_checksum = xor_checksum(inner_header)

    data_checksum = xor_checksum(payload)

    return outer + inner_header + bytes([header_checksum]) + payload + bytes([data_checksum, INNER_END_FLAG])


def unpack_frame(data: Union[bytes, bytearray]) -> bytes:
    """Unpack a complete dual-envelope frame, returning the protobuf payload.

    Validates outer header, inner header checksum, data checksum, and end flag.
    """
    if len(data) < OUTER_HEADER_LEN + 8:
        raise ValueError(f"Frame too short: {len(data)} bytes")

    if data[0] != OUTER_START_FLAG:
        raise ValueError(f"Invalid outer start flag: 0x{data[0]:02x}")

    inner_size = struct.unpack_from('<I', data, 4)[0]

    if len(data) < OUTER_HEADER_LEN + inner_size:
        raise ValueError(f"Incomplete frame: have {len(data)}, need {OUTER_HEADER_LEN + inner_size}")

    inner = data[OUTER_HEADER_LEN:]

    if inner[0] != INNER_START_FLAG:
        raise ValueError(f"Invalid inner start flag: 0x{inner[0]:02x}")

    proto_size = struct.unpack_from('>I', inner, 1)[0]

    expected_hdr_xor = xor_checksum(inner[0:5])

    if inner[5] != expected_hdr_xor:
        raise ValueError(f"Header checksum mismatch: got 0x{inner[5]:02x}, expected 0x{expected_hdr_xor:02x}")

    payload = inner[6:6 + proto_size]

    expected_data_xor = xor_checksum(payload)

    if inner[6 + proto_size] != expected_data_xor:
        raise ValueError(
            f"Data checksum mismatch: got 0x{inner[6 + proto_size]:02x}, expected 0x{expected_data_xor:02x}"
        )

    if inner[6 + proto_size + 1] != INNER_END_FLAG:
        raise ValueError(f"Invalid end flag: 0x{inner[6 + proto_size + 1]:02x}")

    return bytes(payload)
