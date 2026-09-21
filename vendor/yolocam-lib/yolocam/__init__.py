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

"""YoloCam S3 control protocol library.

Provides wire format encoding, parameter definitions, and a thread-safe TCP
client for the YoloCam S3 camera's protobuf control protocol.
"""

from .client import YoloCamClient
from .params import (
    PARAMS, ParamDef, PARAM_CATEGORIES,
    ISO_VALUES, SHUTTER_TABLE, SHUTTER_REVERSE, APERTURE_TABLE,
    ZOOM_RAW_MIN, ZOOM_RAW_MAX, ZOOM_MULT_MIN, ZOOM_MULT_MAX,
    zoom_raw_to_mult, zoom_mult_to_raw,
    sharpness_to_wire, sharpness_from_wire,
    format_value, format_shutter, format_aperture, format_zoom,
    find_nearest_aperture,
    is_readable, is_writable,
)
from .color import rgb_to_cct
from .protocol import (
    CAMERA_IP, CAMERA_PORT, DEFAULT_TIMEOUT,
    pack_frame, unpack_frame, xor_checksum,
)

__all__ = [
    "YoloCamClient",
    "PARAMS",
    "ParamDef",
    "PARAM_CATEGORIES",
    "ISO_VALUES",
    "SHUTTER_TABLE",
    "SHUTTER_REVERSE",
    "APERTURE_TABLE",
    "ZOOM_RAW_MIN",
    "ZOOM_RAW_MAX",
    "ZOOM_MULT_MIN",
    "ZOOM_MULT_MAX",
    "zoom_raw_to_mult",
    "zoom_mult_to_raw",
    "sharpness_to_wire",
    "sharpness_from_wire",
    "format_value",
    "format_shutter",
    "format_aperture",
    "format_zoom",
    "find_nearest_aperture",
    "is_readable",
    "is_writable",
    "CAMERA_IP",
    "CAMERA_PORT",
    "DEFAULT_TIMEOUT",
    "pack_frame",
    "unpack_frame",
    "xor_checksum",
    "rgb_to_cct",
]
