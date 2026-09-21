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

"""RGB to correlated color temperature conversion via McCamy's approximation."""


# sRGB to CIE XYZ (D65 illuminant) matrix
_SRGB_TO_XYZ = [
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
]


def _linearize(c: int) -> float:
    """Inverse sRGB companding for an 8-bit component."""
    s = c / 255.0

    if s <= 0.04045:
        return s / 12.92

    return ((s + 0.055) / 1.055) ** 2.4


def rgb_to_cct(r: int, g: int, b: int) -> int:
    """Convert an sRGB color to correlated color temperature in Kelvin.

    Uses sRGB linearization, then CIE XYZ conversion, then McCamy's
    cubic approximation from xy chromaticity coordinates.
    Result is clamped to the camera's 2000-10000 K range.
    """
    lr, lg, lb = _linearize(r), _linearize(g), _linearize(b)

    X = _SRGB_TO_XYZ[0][0] * lr + _SRGB_TO_XYZ[0][1] * lg + _SRGB_TO_XYZ[0][2] * lb
    Y = _SRGB_TO_XYZ[1][0] * lr + _SRGB_TO_XYZ[1][1] * lg + _SRGB_TO_XYZ[1][2] * lb
    Z = _SRGB_TO_XYZ[2][0] * lr + _SRGB_TO_XYZ[2][1] * lg + _SRGB_TO_XYZ[2][2] * lb

    total = X + Y + Z

    if total < 1e-10:
        return 6500

    x = X / total
    y = Y / total

    # Guard against degenerate chromaticity near the singularity
    denom = 0.1858 - y

    if abs(denom) < 1e-10:
        return 6500

    n = (x - 0.3320) / denom
    cct = 449.0 * n**3 + 3525.0 * n**2 + 6823.3 * n + 5520.33

    return max(2000, min(10000, int(round(cct))))
