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

"""Parameter definitions and value conversion tables for the YoloCam S3."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import yolocam_pb2 as pb

# S3 ISO values (sent as actual values, not indices)
ISO_VALUES: List[int] = [
    100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250,
    1600, 2000, 2500, 3200, 4000, 5000, 6400, 8000, 9600, 12800,
]

# Shutter speed denominator -> microseconds (protocol uses microseconds)
SHUTTER_TABLE: Dict[int, int] = {
    8000: 125,   6400: 156,   5000: 200,   4000: 250,
    3200: 312,   2500: 400,   2000: 500,   1600: 625,
    1250: 800,   1000: 1000,  800: 1250,   640: 1562,
    500: 2000,   400: 2500,   320: 3125,   250: 4000,
    200: 5000,   160: 6250,   125: 8000,   100: 10000,
    80: 12500,   60: 16666,   50: 20000,   30: 33333,
    25: 40000,
}
SHUTTER_REVERSE: Dict[int, int] = {v: k for k, v in SHUTTER_TABLE.items()}

# Aperture index -> f-stop label
APERTURE_TABLE: Dict[int, str] = {
    0: "F1.0",   1: "F1.1",   2: "F1.3",   3: "F1.4",
    4: "F1.6",   5: "F1.7",   6: "F1.8",   7: "F2.0",
    8: "F2.2",   9: "F2.5",   10: "F2.8",  11: "F3.2",
    12: "F3.6",  13: "F4.0",  14: "F4.5",  15: "F5.0",
    16: "F5.7",  17: "F6.3",  18: "F7.1",  19: "F8.0",
    20: "F9.0",  21: "F10.1", 22: "F11.3", 23: "F12.7",
    24: "F14.3", 25: "F16.0", 26: "F18.0", 27: "F20.2",
    28: "F22.6", 29: "F25.4", 30: "F28.5", 31: "F32.0",
}

# Zoom raw value <-> multiplier conversion
ZOOM_RAW_MIN = 50
ZOOM_RAW_MAX = 98
ZOOM_MULT_MIN = 1.0
ZOOM_MULT_MAX = 4.0


def zoom_raw_to_mult(raw: int) -> float:
    """Convert raw zoom value (50-98) to multiplier (1.0x-4.0x)."""
    return 0.0625 * raw - 2.125


def zoom_mult_to_raw(mult: float) -> int:
    """Convert zoom multiplier (1.0x-4.0x) to raw value (50-98)."""
    return round((mult + 2.125) / 0.0625)


def sharpness_to_wire(value: int) -> int:
    """Convert display sharpness (-10 to 30) to wire format.

    Values -1 to -10 are sent as 61 to 70 respectively. Values 0-30 are sent as-is.
    """
    if value < 0:
        return 60 - value

    return value


def sharpness_from_wire(raw: int) -> int:
    """Convert wire format to display sharpness (-10 to 30).

    Raw 61-70 maps back to -1 to -10 respectively. Values 0-30 are returned as-is.
    """
    if 61 <= raw <= 70:
        return 60 - raw

    return raw


def format_shutter(us: int) -> str:
    """Format shutter microseconds as a fraction string (e.g. '1/1000s')."""
    if us in SHUTTER_REVERSE:
        return f"1/{SHUTTER_REVERSE[us]}s"

    return f"{us}us"


def format_aperture(index: int) -> str:
    """Format aperture index as f-stop string."""
    return APERTURE_TABLE.get(index, f"idx:{index}")


def format_zoom(raw: int) -> str:
    """Format raw zoom value as multiplier string."""
    return f"{zoom_raw_to_mult(raw):.1f}x"


def find_nearest_aperture(f_stop: float) -> int:
    """Find the aperture index closest to the given f-stop value."""
    best_idx = 0
    best_diff = float('inf')

    for idx, label in APERTURE_TABLE.items():
        f_val = float(label[1:])

        if abs(f_val - f_stop) < best_diff:
            best_diff = abs(f_val - f_stop)
            best_idx = idx

    return best_idx


@dataclass
class ParamDef:
    name: str
    cid: int
    description: str
    control_type: str
    min_val: Optional[int] = None
    max_val: Optional[int] = None
    default: Optional[int] = None
    value_map: Optional[Dict[int, str]] = field(default=None)
    category: str = ""


# Control types:
#   "slider"   - continuous numeric range
#   "toggle"   - 0/1 on/off
#   "segment"  - small set of discrete choices (2-5 options)
#   "dropdown" - larger set of choices
#   "readonly" - read-only info
#   "zoom"     - zoom multiplier (special conversion)
#   "setonly"   - write-only, no read

PARAMS: Dict[str, ParamDef] = {
    # Image quality
    "brightness": ParamDef(
        "brightness", pb.CID_BRIGHTNESS, "Image brightness",
        "slider", 0, 100, 50, category="Image Quality",
    ),
    "sharpness": ParamDef(
        "sharpness", pb.CID_SHARPNESS, "Image sharpness",
        "slider", -10, 30, 5, category="Image Quality",
    ),
    "contrast": ParamDef(
        "contrast", pb.CID_CONTRAST, "Image contrast",
        "slider", 0, 100, 50, category="Image Quality",
    ),
    "saturation": ParamDef(
        "saturation", pb.CID_SATURATION, "Image saturation",
        "slider", 0, 100, 50, category="Image Quality",
    ),
    "hue": ParamDef(
        "hue", pb.CID_HUE, "Image hue",
        "slider", 0, 100, 50, category="Image Quality",
    ),
    "gamma": ParamDef(
        "gamma", pb.CID_GAMMA, "Image gamma",
        "slider", 0, 100, 50, category="Image Quality",
    ),

    # Zoom
    "zoom": ParamDef(
        "zoom", pb.CID_ZOOM, "Digital zoom",
        "zoom", ZOOM_RAW_MIN, ZOOM_RAW_MAX, ZOOM_RAW_MIN, category="Zoom",
    ),
    "zoom-speed": ParamDef(
        "zoom-speed", pb.CID_ZOOM_SPEED, "Zoom speed",
        "setonly", 1, 5, 3, category="Zoom",
    ),

    # Exposure
    "exposure-mode": ParamDef(
        "exposure-mode", pb.CID_EXPOSURE_MODE, "Exposure mode",
        "segment", 0, 1, 0, {0: "Auto", 1: "Manual"}, category="Exposure",
    ),
    "exposure-ev": ParamDef(
        "exposure-ev", pb.CID_EXPOSURE_EV, "EV compensation (8=0EV)",
        "slider", 0, 16, 8, category="Exposure",
    ),
    "exposure-iso": ParamDef(
        "exposure-iso", pb.CID_EXPOSURE_ISO, "ISO",
        "dropdown", 100, 12800, 400,
        {v: str(v) for v in ISO_VALUES},
        category="Exposure",
    ),
    "exposure-time": ParamDef(
        "exposure-time", pb.CID_EXPOSURE_TIME, "Shutter speed",
        "dropdown", 125, 40000, 16666,
        {us: f"1/{denom}" for denom, us in SHUTTER_TABLE.items()},
        category="Exposure",
    ),
    "exposure-aperture": ParamDef(
        "exposure-aperture", pb.CID_EXPOSURE_APERTURE, "Aperture",
        "dropdown", 0, 31, 10, APERTURE_TABLE, category="Exposure",
    ),
    "exposure-lock": ParamDef(
        "exposure-lock", pb.CID_EXPOSURE_LOCK, "Lock exposure",
        "toggle", 0, 1, 0, category="Exposure",
    ),
    "anti-flicker": ParamDef(
        "anti-flicker", pb.CID_ANTI_FLICKER, "Anti-flicker",
        "segment", 0, 2, 0, {0: "Off", 1: "50Hz", 2: "60Hz"}, category="Exposure",
    ),
    "dynamic-range": ParamDef(
        "dynamic-range", pb.CID_DYNAMIC_RANGE, "Dynamic range",
        "slider", 0, 100, 50, category="Exposure",
    ),
    "hdr": ParamDef(
        "hdr", pb.CID_ENABLE_HDR, "HDR",
        "toggle", 0, 1, 0, category="Exposure",
    ),

    # White balance
    "wb-mode": ParamDef(
        "wb-mode", pb.CID_WHITE_BALANCE_MODE, "WB mode",
        "segment", 0, 1, 1, {0: "Manual", 1: "Auto"}, category="White Balance",
    ),
    "wb-temp": ParamDef(
        "wb-temp", pb.CID_WHITE_BALANCE_TEMPERATURE, "Color temperature",
        "slider", 2000, 10000, 5500, category="White Balance",
    ),
    "wb-temp-shift": ParamDef(
        "wb-temp-shift", pb.CID_WHITE_BALANCE_TEMPERATURE_SHIFT, "WB temp shift",
        "slider", 0, 128, 64, category="White Balance",
    ),
    "wb-lock": ParamDef(
        "wb-lock", pb.CID_WHITE_BALANCE_LOCK, "Lock WB",
        "toggle", 0, 1, 0, category="White Balance",
    ),

    # Focus
    "focus-mode": ParamDef(
        "focus-mode", pb.CID_FOCUS_MODE, "Focus mode",
        "segment", 1, 4, 1, {1: "AFS", 2: "Manual", 3: "AFC", 4: "Face"},
        category="Focus",
    ),
    "focus-level": ParamDef(
        "focus-level", pb.CID_FOCUS_RECT_LEVEL, "Focus area size",
        "segment", 0, 2, 0, {0: "Large", 1: "Medium", 2: "Small"},
        category="Focus",
    ),
    "focus-caf": ParamDef(
        "focus-caf", pb.CID_ENABLE_CAF, "Continuous AF",
        "toggle", 0, 1, 0, category="Focus",
    ),
    "focus-trigger": ParamDef(
        "focus-trigger", pb.CID_TRIGGER_SPEED, "AF trigger speed",
        "slider", category="Focus",
    ),
    "focus-manual": ParamDef(
        "focus-manual", pb.CID_MANU_FOCUS_CODE, "Manual focus step",
        "setonly", category="Focus",
    ),
    "face-roi": ParamDef(
        "face-roi", pb.CID_ENABLE_FACE_ROI, "Face-detect ROI",
        "setonly", 0, 1, 0, category="Focus",
    ),

    # Video format
    "resolution": ParamDef(
        "resolution", pb.CID_RESOLUTION, "Resolution setting",
        "dropdown", category="Video",
    ),
    "frame-rate": ParamDef(
        "frame-rate", pb.CID_FRAME_RATE, "Frame rate",
        "dropdown", value_map={25: "25", 30: "30", 50: "50", 60: "60"},
        category="Video",
    ),
    "scene": ParamDef(
        "scene", pb.CID_SCENE, "Scene preset",
        "setonly", category="Video",
    ),

    # Lens correction
    "distortion": ParamDef(
        "distortion", pb.CID_ENABLE_DISTORTION, "Distortion correction",
        "toggle", 0, 1, 0, category="Lens Correction",
    ),
    "shading": ParamDef(
        "shading", pb.CID_ENABLE_SHADING, "Shading correction",
        "toggle", 0, 1, 0, category="Lens Correction",
    ),

    # HDMI
    "hdmi-format": ParamDef(
        "hdmi-format", pb.CID_HDMI_OUTPUT_FORMAT, "HDMI output format",
        "dropdown", 0, 3, 0, {0: "Auto", 1: "4K", 2: "1080p", 3: "720p"},
        category="HDMI",
    ),
    "hdmi-color": ParamDef(
        "hdmi-color", pb.CID_HDMI_COLOR_SPACE, "HDMI color space",
        "dropdown", 0, 2, 0, {0: "Auto", 1: "RGB", 2: "YUV422"},
        category="HDMI",
    ),

    # Tuning / color
    "tuning": ParamDef(
        "tuning", pb.CID_TUNING, "Image tuning parameters",
        "setonly", category="Color / Tuning",
    ),
    "clut": ParamDef(
        "clut", pb.CID_ENABLE_CLUT, "Color LUT enable",
        "toggle", 0, 1, 0, category="Color / Tuning",
    ),
    "clut-attr": ParamDef(
        "clut-attr", pb.CID_CLUT_ATTR, "Color LUT intensity",
        "slider", 0, 255, 0, category="Color / Tuning",
    ),

    # Audio
    "au-volume": ParamDef(
        "au-volume", pb.CID_AU_VOLUME, "Audio volume",
        "slider", 0, 100, 50, category="Audio",
    ),
    "au-denoise": ParamDef(
        "au-denoise", pb.CID_AU_DENOISE, "Noise reduction",
        "dropdown", 0, 3, 2, {0: "Close", 1: "Weak", 2: "Middle", 3: "Strong"},
        category="Audio",
    ),
    "au-gain": ParamDef(
        "au-gain", pb.CID_AU_AUTO_GAIN, "Auto gain",
        "toggle", 0, 1, 0, category="Audio",
    ),
    "au-mic-type": ParamDef(
        "au-mic-type", pb.CID_AU_MIC_TYPE, "Mic type",
        "setonly", 0, 1, 0, {0: "Line", 1: "Mic"}, category="Audio",
    ),
    "au-reverb": ParamDef(
        "au-reverb", pb.CID_AU_REVERB, "Reverb level",
        "dropdown", 0, 3, 0, {0: "Close", 1: "Weak", 2: "Middle", 3: "Strong"},
        category="Audio",
    ),
    "au-delay": ParamDef(
        "au-delay", pb.CID_AU_DELAY, "Audio delay (ms)",
        "slider", 0, 400, 0, category="Audio",
    ),

    # Indicator LED
    "indicator": ParamDef(
        "indicator", pb.CID_INDICATOR_CTL, "Indicator LED control",
        "setonly", category="LED / Indicator",
    ),

    # AI tracking
    "ai-tracking": ParamDef(
        "ai-tracking", pb.CID_AI_TRACKING_CTRL, "AI tracking mode",
        "segment", 0, 2, 2, {0: "Portrait", 1: "Thing", 2: "Off"},
        category="AI Features",
    ),

    # Streaming
    "stream-cfg": ParamDef(
        "stream-cfg", pb.CID_STREAM_CFG, "Stream configuration",
        "setonly", category="Streaming",
    ),
    "uvc": ParamDef(
        "uvc", pb.CID_ENABLE_UVC, "UVC output enable",
        "setonly", 0, 1, 0, category="Streaming",
    ),

    # Device info (read-only)
    "version": ParamDef(
        "version", pb.CID_VERSION_INFO, "Camera firmware version info",
        "readonly", category="Device Info",
    ),
    "usb-version": ParamDef(
        "usb-version", pb.CID_USB_VERSION, "USB protocol version",
        "readonly", category="Device Info",
    ),
    "lens-status": ParamDef(
        "lens-status", pb.CID_LENS_STATUS, "Lens connection status",
        "readonly", category="Device Info",
    ),
    "lens-detail": ParamDef(
        "lens-detail", pb.CID_GET_LENS_DETAIL, "Lens detail info",
        "readonly", category="Device Info",
    ),
    "lens-version": ParamDef(
        "lens-version", pb.CID_LENS_VERSION, "Lens firmware version",
        "readonly", category="Device Info",
    ),
    "video-status": ParamDef(
        "video-status", pb.CID_VIDEO_STATUS_CHANGED, "Video pipeline status",
        "readonly", category="Device Info",
    ),
    "au-version": ParamDef(
        "au-version", pb.CID_AU_VERSION, "Audio module version",
        "readonly", category="Device Info",
    ),
    "disk-info": ParamDef(
        "disk-info", pb.CID_DISK_INFO, "SD card info",
        "readonly", category="Device Info",
    ),
    "lowpower": ParamDef(
        "lowpower", pb.CID_LOWPOWER_STATUS, "Low power status",
        "readonly", category="Device Info",
    ),
    "aperture-range": ParamDef(
        "aperture-range", pb.CID_RANGE_OF_APERTURE, "Aperture range for lens",
        "readonly", category="Device Info",
    ),
    "exp-time-range": ParamDef(
        "exp-time-range", pb.CID_RANGE_OF_EXP_TIME, "Exposure time range",
        "readonly", category="Device Info",
    ),
}

# Grouped by category for UI panel layout
PARAM_CATEGORIES: Dict[str, List[str]] = {}
for _name, _pdef in PARAMS.items():
    PARAM_CATEGORIES.setdefault(_pdef.category, []).append(_name)


def is_readable(param: ParamDef) -> bool:
    return param.control_type not in ("setonly",)


def is_writable(param: ParamDef) -> bool:
    return param.control_type not in ("readonly",)


def format_value(name: str, raw_value: int) -> str:
    """Format a raw protocol value into a human-readable display string."""
    pdef = PARAMS.get(name)

    if pdef is None:
        return str(raw_value)

    if name == "zoom":
        return format_zoom(raw_value)

    if name == "exposure-time":
        return format_shutter(raw_value)

    if name == "exposure-aperture":
        return format_aperture(raw_value)

    if pdef.value_map and raw_value in pdef.value_map:
        return pdef.value_map[raw_value]

    return str(raw_value)
