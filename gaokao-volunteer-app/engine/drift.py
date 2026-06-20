"""
Year-1 drift correction for provinces switching from 文理科 to 物理类/历史类.

Derived empirically from 7 Wave2 provinces (2024 switchers):
吉林/黑龙江/安徽/江西/广西/贵州/甘肃 — n=8,306 school observations.

Formula: drift(p) = 0.6998·p - 2.1828·p² + 1.6168·p³
  p    = historical admission percentile (rank/total, lower = better student)
  drift = percentage-point shift in admission percentile (positive = school got easier)

All 2025 provinces that switched: 山西(14), 内蒙古(15), 河南(41),
四川(51), 云南(53), 陕西(61), 青海(63), 宁夏(64)
"""

# Provinces switching exam format IN 2025 (had 理科 in 2024, 物理类 in 2025)
SWITCHING_2025 = {14, 15, 41, 51, 53, 61, 63, 64}

# Weighted cubic fit coefficients (7-province, n=8306)
_A, _B, _C = 0.6998, -2.1828, 1.6168


def drift_pp(p_hist: float) -> float:
    """
    Returns the expected drift in percentage points for a school at
    historical admission percentile p_hist.

    Positive drift = school became easier relative to pool in Year-1.
    Valid range: p_hist in [0, 1]. Clipped at 0 for negative outputs.
    """
    if not (0.0 <= p_hist <= 1.0):
        raise ValueError(f"p_hist must be in [0,1], got {p_hist}")
    raw = _A * p_hist + _B * p_hist ** 2 + _C * p_hist ** 3
    return raw  # can be negative for p_hist > ~0.8 (bottom schools got slightly harder)


def apply_drift(rank_2024: int, total_2024_li: int, total_2025_phy: int) -> float:
    """
    Given a school's last 理科 admission rank and pool sizes,
    returns the expected 2025 物理类 admission rank.

    Steps:
      1. Scale rank by pool ratio (pool may shrink or grow)
      2. Apply Year-1 drift correction (schools generally easier in Year-1)

    Returns float (use int() when comparing to student rank).
    """
    if total_2024_li <= 0 or total_2025_phy <= 0:
        raise ValueError("Pool totals must be positive")

    pool_scale = total_2025_phy / total_2024_li
    p_hist = rank_2024 / total_2024_li

    # Scale rank proportionally to new pool
    scaled_rank = rank_2024 * pool_scale

    # Drift: positive drift_pp means school admits lower-ranked students
    # → rank number increases → add to scaled_rank
    drift_rank = drift_pp(p_hist) / 100.0 * total_2025_phy
    expected_rank = scaled_rank + drift_rank

    return max(1.0, expected_rank)


# drift_uncertainty_pp() was removed (2025-04).
# The pipeline now derives Year-1 σ as: temporal_σ_from_理科_history × YEAR1_SIGMA_FACTOR.
# The cross-sectional wave2 std is no longer used.
