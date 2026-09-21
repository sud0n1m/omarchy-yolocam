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

"""HSV-to-CLUT conversion for the YoloCam S3 HiSilicon ISP 3D color LUT.

The ISP uses a 17x17x17 3D LUT stored as 5508 uint32 entries in an interleaved
octant layout. Each uint32 packs three signed 10-bit RGB delta values.

Two modes produce CLUTs:
  Fast mode  -- a [24][12] grid of absolute (theta, r_norm) polar positions modified
                by a two-point drag with linear falloff
  Professional mode -- a [9][12][25] grid of delta/scale HSV adjustments from
                       per-point dragging

The HSV adjustment grid is 3 channels (hue/saturation/luma) each shaped [9][12][25],
representing 9 luma levels, 12 saturation columns, and 25 hue rows (24 sectors of
15 degrees, with row 24 wrapping to row 0).
"""

import colorsys
import copy
from typing import List, Tuple

CLUT_SIZE = 5508
GRID_DIM = 17
HUE_SECTORS = 24
SAT_COLUMNS = 12
LUMA_LEVELS = 9
FULL_R_VALUE = 1.17

LINE_CENTER = 0
LINE_LEFT = 1
LINE_RIGHT = 2

INTERVAL_THETA = 360.0 / HUE_SECTORS
INTERVAL_R_NORM = FULL_R_VALUE / (SAT_COLUMNS - 1)

# Per-octant base offsets in the interleaved 5508-entry array.
# Octant = (x&1) + ((y&1)<<1) + ((z&1)<<2). For dim=17 (9 even, 8 odd values per axis):
# octant 0: 9*9*9=729, octant 1: 8*9*9=648, ..., octant 7: 8*8*8=512
_OCTANT_SIZES = [729, 648, 648, 576, 648, 576, 576, 512]
_OCTANT_BASES = []
_base = 0
for _sz in _OCTANT_SIZES:
    _OCTANT_BASES.append(_base)
    _base += _sz


def identity_clut() -> List[int]:
    """Return a 5508-entry all-zeros CLUT (identity / no-op)."""
    return [0] * CLUT_SIZE


def pack_clut(rgb_deltas: List[List[List[List[float]]]]) -> List[int]:
    """Pack [3][17][17][17] float RGB deltas into 5508 interleaved uint32 entries.

    Each float is a fractional delta in [-1.0, ~1.0). Values are quantized to 10-bit
    signed integers (s0.9 fixed-point, divided by 512 on the ISP) and packed as
    (R << 20) | (G << 10) | B.
    """
    data = [0] * CLUT_SIZE
    counters = [0] * 8

    for x in range(GRID_DIM):

        for y in range(GRID_DIM):

            for z in range(GRID_DIM):
                octant = (x & 1) + ((y & 1) << 1) + ((z & 1) << 2)
                idx = _OCTANT_BASES[octant] + counters[octant]
                counters[octant] += 1

                r = _clamp_and_quantize(rgb_deltas[0][x][y][z])
                g = _clamp_and_quantize(rgb_deltas[1][x][y][z])
                b = _clamp_and_quantize(rgb_deltas[2][x][y][z])

                data[idx] = (r << 20) | (g << 10) | b

    return data


def unpack_clut(data: List[int]) -> List[List[List[List[float]]]]:
    """Unpack 5508 interleaved uint32 entries into [3][17][17][17] float RGB deltas."""
    if len(data) < CLUT_SIZE:
        raise ValueError(f"Expected {CLUT_SIZE} entries, got {len(data)}")

    result = [[[[0.0] * GRID_DIM for _ in range(GRID_DIM)] for _ in range(GRID_DIM)] for _ in range(3)]
    counters = [0] * 8

    for x in range(GRID_DIM):

        for y in range(GRID_DIM):

            for z in range(GRID_DIM):
                octant = (x & 1) + ((y & 1) << 1) + ((z & 1) << 2)
                idx = _OCTANT_BASES[octant] + counters[octant]
                counters[octant] += 1

                val = data[idx]
                b = val & 0x3FF
                g = (val >> 10) & 0x3FF
                r = (val >> 20) & 0x3FF

                r = (r - 1024) if (r & 0x200) else r
                g = (g - 1024) if (g & 0x200) else g
                b = (b - 1024) if (b & 0x200) else b

                result[0][x][y][z] = r / 512.0
                result[1][x][y][z] = g / 512.0
                result[2][x][y][z] = b / 512.0

    return result


def make_hsv_grid() -> Tuple[List, List, List]:
    """Return identity HSV adjustment grids (hue=0, sat=1.0, luma=0).

    Returns (hue_grid, sat_grid, luma_grid) each shaped [9][12][25].
    """
    hue_grid = [[[0.0] * (HUE_SECTORS + 1) for _ in range(SAT_COLUMNS)] for _ in range(LUMA_LEVELS)]
    sat_grid = [[[1.0] * (HUE_SECTORS + 1) for _ in range(SAT_COLUMNS)] for _ in range(LUMA_LEVELS)]
    luma_grid = [[[0.0] * (HUE_SECTORS + 1) for _ in range(SAT_COLUMNS)] for _ in range(LUMA_LEVELS)]
    return hue_grid, sat_grid, luma_grid


def hsv_grid_to_clut(
    hue_grid: List[List[List[float]]],
    sat_grid: List[List[List[float]]],
    luma_grid: List[List[List[float]]],
) -> List[int]:
    """Convert [9][12][25] HSV adjustment grids into a 5508-entry uint32 CLUT.

    hue_grid values are degree offsets, sat_grid values are scale factors
    (0=desaturate, 1=no change, >1=boost), luma_grid values are brightness offsets.
    """
    rgb_deltas = [[[[0.0] * GRID_DIM for _ in range(GRID_DIM)] for _ in range(GRID_DIM)] for _ in range(3)]

    for ri in range(GRID_DIM):

        for gi in range(GRID_DIM):

            for bi in range(GRID_DIM):
                r_norm = ri / 16.0
                g_norm = gi / 16.0
                b_norm = bi / 16.0

                h, s, v = _rgb_to_hsv(r_norm, g_norm, b_norm)

                hue_adj, sat_adj, luma_adj = _lookup_hsv_adjustment(h, s, v, hue_grid, sat_grid, luma_grid)

                h_new = (h + hue_adj / 360.0) % 1.0
                s_new = max(0.0, min(1.0, s * sat_adj))
                v_new = max(0.0, min(1.0, v + luma_adj))

                r_new, g_new, b_new = colorsys.hsv_to_rgb(h_new, s_new, v_new)

                rgb_deltas[0][ri][gi][bi] = r_new - r_norm
                rgb_deltas[1][ri][gi][bi] = g_new - g_norm
                rgb_deltas[2][ri][gi][bi] = b_new - b_norm

    return pack_clut(rgb_deltas)


def _clamp_and_quantize(value: float) -> int:
    """Quantize a float delta to a 10-bit two's complement unsigned representation.

    The ISP interprets 10-bit values as s0.9 fixed-point (divide by 512), giving
    a range of [-1.0, 0.998]. We multiply by 512 to match that encoding.
    """
    quantized = round(value * 512)
    quantized = max(-512, min(511, quantized))

    if quantized < 0:
        quantized += 1024

    return quantized


def _rgb_to_hsv(r: float, g: float, b: float) -> Tuple[float, float, float]:
    """Convert RGB [0,1] to HSV where H is [0,1), S is [0,1], V is [0,1]."""
    return colorsys.rgb_to_hsv(r, g, b)


def _lookup_hsv_adjustment(
    h: float, s: float, v: float,
    hue_grid: List[List[List[float]]],
    sat_grid: List[List[List[float]]],
    luma_grid: List[List[List[float]]],
) -> Tuple[float, float, float]:
    """Trilinearly interpolate HSV adjustments from the 9x12x25 grids."""
    hue_pos = h * HUE_SECTORS
    sat_pos = s * (SAT_COLUMNS - 1) * (1.0 / FULL_R_VALUE) if FULL_R_VALUE > 0 else 0.0
    luma_pos = v * (LUMA_LEVELS - 1)

    hue_idx = int(hue_pos)
    hue_frac = hue_pos - hue_idx
    hue_idx = hue_idx % HUE_SECTORS

    sat_idx = min(int(sat_pos), SAT_COLUMNS - 2)
    sat_frac = max(0.0, min(1.0, sat_pos - sat_idx))

    luma_idx = min(int(luma_pos), LUMA_LEVELS - 2)
    luma_frac = max(0.0, min(1.0, luma_pos - luma_idx))

    hue_idx_next = (hue_idx + 1) % HUE_SECTORS

    hue_adj = _trilinear(hue_grid, luma_idx, sat_idx, hue_idx, hue_idx_next, luma_frac, sat_frac, hue_frac)
    sat_adj = _trilinear(sat_grid, luma_idx, sat_idx, hue_idx, hue_idx_next, luma_frac, sat_frac, hue_frac)
    luma_adj = _trilinear(luma_grid, luma_idx, sat_idx, hue_idx, hue_idx_next, luma_frac, sat_frac, hue_frac)

    return hue_adj, sat_adj, luma_adj


def _trilinear(
    grid: List[List[List[float]]],
    li: int, si: int, hi: int, hi_next: int,
    lf: float, sf: float, hf: float,
) -> float:
    """Trilinear interpolation across luma, saturation, and hue axes."""
    def sample(l: int, s: int, h: int) -> float:
        return grid[l][s][h]

    c000 = sample(li, si, hi)
    c001 = sample(li, si, hi_next)
    c010 = sample(li, si + 1, hi)
    c011 = sample(li, si + 1, hi_next)
    c100 = sample(li + 1, si, hi)
    c101 = sample(li + 1, si, hi_next)
    c110 = sample(li + 1, si + 1, hi)
    c111 = sample(li + 1, si + 1, hi_next)

    c00 = c000 + (c001 - c000) * hf
    c01 = c010 + (c011 - c010) * hf
    c10 = c100 + (c101 - c100) * hf
    c11 = c110 + (c111 - c110) * hf

    c0 = c00 + (c01 - c00) * sf
    c1 = c10 + (c11 - c10) * sf

    return c0 + (c1 - c0) * lf


# ---------------------------------------------------------------------------
# Fast mode: two-point color remapping via absolute polar grid
# ---------------------------------------------------------------------------

def init_fast_grid() -> List[List[List[float]]]:
    """Return identity Fast mode grid [24][12] of [theta_deg, r_norm]."""
    grid = []

    for h in range(HUE_SECTORS):
        row = []

        for s in range(SAT_COLUMNS):
            theta = h * INTERVAL_THETA
            r_norm = s * INTERVAL_R_NORM
            row.append([round(theta, 3), round(r_norm, 3)])

        grid.append(row)

    return grid


def init_luma_grid() -> List[List[float]]:
    """Return all-zeros luma grid [24][12]."""
    return [[0.0] * SAT_COLUMNS for _ in range(HUE_SECTORS)]


def calc_rows_data(
    line_num: int, col_num: int,
    dest_theta: float, dest_r_norm: float,
    grid: List[List[List[float]]],
) -> None:
    """Apply a two-point color shift to the Fast mode grid (in place).

    Computes the delta between the source grid point's origin position and the
    destination, then propagates the shift across the center hue row and both
    neighbors with linear falloff.

    line_num, col_num: source grid indices
    dest_theta: destination angle in degrees [0, 360)
    dest_r_norm: destination normalized radius (clamped to FULL_R_VALUE)
    grid: [24][12] of [theta, r_norm], modified in place
    """
    dest_r_norm = min(dest_r_norm, 1.0)

    origin_theta = line_num * INTERVAL_THETA
    origin_r_norm = col_num * INTERVAL_R_NORM

    theta_delta = dest_theta - origin_theta

    if theta_delta > 180:
        theta_delta -= 360

    if theta_delta < -180:
        theta_delta += 360

    r_delta = dest_r_norm - origin_r_norm

    _pull_line(line_num, col_num, r_delta, theta_delta, LINE_CENTER, grid)
    _pull_line(line_num, col_num, r_delta, theta_delta, LINE_LEFT, grid)
    _pull_line(line_num, col_num, r_delta, theta_delta, LINE_RIGHT, grid)


def _pull_line(
    line_num: int, col_num: int,
    r_delta: float, theta_delta: float,
    direction: int,
    grid: List[List[List[float]]],
) -> None:
    """Propagate a shift across one hue row with linear falloff.

    For each saturation column (skipping center), the shift decays by 11%/step
    for theta and 12%/step for radius based on distance from the source column.
    The outermost column receives no radial shift. LEFT/RIGHT rows get half shift.
    """
    row = line_num

    if direction == LINE_LEFT:
        row = (line_num - 1) % HUE_SECTORS
    elif direction == LINE_RIGHT:
        row = (line_num + 1) % HUE_SECTORS

    for col in range(1, SAT_COLUMNS):
        dist = abs(col - col_num)

        if dist >= 9:
            dist = 9

        theta_shift = theta_delta * (1.0 - 0.11 * dist)
        r_shift = 0.0

        if col < SAT_COLUMNS - 1:
            r_shift = r_delta * (1.0 - 0.12 * dist)

        if direction != LINE_CENTER:
            theta_shift /= 2.0
            r_shift /= 2.0

        base_theta = row * INTERVAL_THETA
        base_r_norm = col * INTERVAL_R_NORM

        grid[row][col] = [
            round(base_theta + theta_shift, 3),
            round(max(0.0, base_r_norm + r_shift), 3),
        ]


def fast_grid_to_clut(
    grid: List[List[List[float]]],
    luma: List[List[float]] = None,
) -> List[int]:
    """Convert a Fast mode [24][12] absolute-position grid to a 5508-entry CLUT.

    grid[h][s] = [theta_deg, r_norm]  (absolute polar positions)
    luma[h][s] = additive luma adjustment (optional, defaults to zeros)

    Replicates the behavior of the native algClutLib Hsv2Clut_array function.
    """
    if luma is None:
        luma = init_luma_grid()

    grid_25 = grid + [grid[0]]
    luma_25 = luma + [luma[0]]

    hue_lut = [[[0.0] * (HUE_SECTORS + 1) for _ in range(SAT_COLUMNS)] for _ in range(LUMA_LEVELS)]
    sat_lut = [[[0.0] * (HUE_SECTORS + 1) for _ in range(SAT_COLUMNS)] for _ in range(LUMA_LEVELS)]
    luma_lut = [[[0.0] * (HUE_SECTORS + 1) for _ in range(SAT_COLUMNS)] for _ in range(LUMA_LEVELS)]

    for l in range(LUMA_LEVELS):

        for s in range(SAT_COLUMNS):

            for h in range(HUE_SECTORS + 1):
                theta, r_norm = grid_25[h][s]

                if h == HUE_SECTORS:
                    hue_lut[l][s][h] = 360.0
                    luma_lut[l][s][h] = luma_25[0][s]
                elif s > 0:
                    hue_lut[l][s][h] = theta
                    luma_lut[l][s][h] = luma_25[h][s]
                else:
                    hue_lut[l][s][h] = h * INTERVAL_THETA
                    luma_lut[l][s][h] = luma_25[h][s]

                sat_lut[l][s][h] = r_norm

    rgb_deltas = [[[[0.0] * GRID_DIM for _ in range(GRID_DIM)] for _ in range(GRID_DIM)] for _ in range(3)]

    for ri in range(GRID_DIM):

        for gi in range(GRID_DIM):

            for bi in range(GRID_DIM):
                r = ri / 16.0
                g = gi / 16.0
                b = bi / 16.0

                h, s, v = colorsys.rgb_to_hsv(r, g, b)

                new_hue, new_r, l_adj = _lookup_absolute(h, s, v, hue_lut, sat_lut, luma_lut)

                new_h = (new_hue / 360.0) % 1.0
                new_s = max(0.0, min(1.0, new_r / FULL_R_VALUE))
                new_v = max(0.0, min(1.0, v + l_adj))

                r_new, g_new, b_new = colorsys.hsv_to_rgb(new_h, new_s, new_v)

                rgb_deltas[0][ri][gi][bi] = r_new - r
                rgb_deltas[1][ri][gi][bi] = g_new - g
                rgb_deltas[2][ri][gi][bi] = b_new - b

    return pack_clut(rgb_deltas)


def _lookup_absolute(
    h: float, s: float, v: float,
    hue_lut: List[List[List[float]]],
    sat_lut: List[List[List[float]]],
    luma_lut: List[List[List[float]]],
) -> Tuple[float, float, float]:
    """Trilinearly interpolate absolute-position HSV grids.

    Uses sat_col = s * (SAT_COLUMNS-1) mapping (not divided by FULL_R_VALUE)
    since the grid stores absolute r_norm values in [0, FULL_R_VALUE].
    """
    hue_pos = h * HUE_SECTORS
    sat_pos = s * (SAT_COLUMNS - 1)
    luma_pos = v * (LUMA_LEVELS - 1)

    h_idx = min(int(hue_pos), HUE_SECTORS - 1)
    h_frac = hue_pos - h_idx
    h_next = h_idx + 1

    s_idx = min(int(sat_pos), SAT_COLUMNS - 2)
    s_frac = max(0.0, min(1.0, sat_pos - s_idx))

    l_idx = min(int(luma_pos), LUMA_LEVELS - 2)
    l_frac = max(0.0, min(1.0, luma_pos - l_idx))

    hue_val = _trilinear(hue_lut, l_idx, s_idx, h_idx, h_next, l_frac, s_frac, h_frac)
    sat_val = _trilinear(sat_lut, l_idx, s_idx, h_idx, h_next, l_frac, s_frac, h_frac)
    luma_val = _trilinear(luma_lut, l_idx, s_idx, h_idx, h_next, l_frac, s_frac, h_frac)

    return hue_val, sat_val, luma_val
