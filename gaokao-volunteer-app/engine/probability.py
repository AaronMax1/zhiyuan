"""
Admission probability model.

P(admit) = 1 - Φ((student_rank - μ_S) / σ_S)

where:
  μ_S = school's expected admission rank (from DataLoader.SchoolStats.mu_rank)
  σ_S = year-to-year std dev of admission rank (σ_rank)
  Φ   = Normal CDF

Interpretation:
  - student_rank < μ_S → student better than threshold → P > 50% (likely admit)
  - student_rank > μ_S → student worse than threshold → P < 50% (unlikely admit)
  - σ_S large → high year-to-year volatility → probability is more spread out

Admission tags:
  冲 (reach):  P in [0.10, 0.45)
  稳 (match):  P in [0.45, 0.75)
  保 (safety): P in [0.75, 1.00]
  (below 0.10 → not recommended; above 1.00 → impossible but clipped)
"""

import math
from engine.data_loader import SchoolStats

# ── Normal CDF (Abramowitz & Stegun approximation, max error 7.5×10⁻⁸) ────

def _norm_cdf(x: float) -> float:
    """Φ(x) — cumulative distribution of standard normal."""
    # Use erfc from math for accuracy
    return 0.5 * math.erfc(-x / math.sqrt(2))


# ── Core probability function ─────────────────────────────────────────────

def p_admit(student_rank: int, stats: SchoolStats) -> float:
    """
    Returns probability of admission given student's provincial rank
    and the school's pre-computed stats.

    student_rank: rank in province (1 = best, larger = worse)
    stats:        SchoolStats from DataLoader

    Returns float in [0, 1].
    """
    mu = stats.mu_rank
    sigma = stats.sigma_rank

    if sigma <= 0:
        # Degenerate case: treat as step function
        return 1.0 if student_rank <= mu else 0.0

    z = (student_rank - mu) / sigma
    return 1.0 - _norm_cdf(z)


def p_admit_raw(student_rank: int, mu_rank: float, sigma_rank: float) -> float:
    """Direct call without SchoolStats wrapper."""
    if sigma_rank <= 0:
        return 1.0 if student_rank <= mu_rank else 0.0
    z = (student_rank - mu_rank) / sigma_rank
    return 1.0 - _norm_cdf(z)


# ── Admission tags ────────────────────────────────────────────────────────

REACH_MIN  = 0.10
MATCH_MIN  = 0.45
SAFETY_MIN = 0.75

def admission_tag(prob: float) -> str:
    """Returns '冲', '稳', '保', or None (not recommended)."""
    if prob >= SAFETY_MIN:
        return '保'
    if prob >= MATCH_MIN:
        return '稳'
    if prob >= REACH_MIN:
        return '冲'
    return None   # below 10% — exclude from recommendations


def describe_probability(prob: float, is_year1_switch: bool = False) -> dict:
    """
    Returns a dict suitable for JSON output.
    Adds a low-confidence flag for Year-1 switch schools.
    """
    tag = admission_tag(prob)
    result = {
        'p_admit': round(prob, 3),
        'p_pct': f"{prob*100:.0f}%",
        'tag': tag,
        'recommended': tag is not None,
    }
    if is_year1_switch:
        result['note'] = '换制首年，预测区间较宽'
    return result
