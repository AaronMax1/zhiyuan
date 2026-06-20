"""
Monte Carlo simulation of 平行志愿 cascade outcomes.

Purpose: PRESENTATION only (not optimization).
Exchange argument proves sort-by-utility is already optimal.
This module shows the student the distribution of likely outcomes.

Usage:
    sim = simulate(recs, n=10000, seed=42)
    print(sim.summary())
"""

import random
from collections import Counter
from dataclasses import dataclass
from typing import Optional
from engine.recommend import Recommendation


@dataclass
class SimulationResult:
    n_trials: int
    # Outcome distribution by tier
    tier_counts: dict          # tier → count of trials landing there
    tier_probs: dict           # tier → probability
    no_admission_count: int    # trials where no school admitted
    no_admission_prob: float

    # Outcome distribution by tag
    tag_counts: dict           # '冲'/'稳'/'保' → count
    tag_probs: dict

    # Expected slot (1-indexed) where student gets admitted
    avg_slot: float
    p50_slot: int              # median slot
    p90_slot: int              # 90th percentile slot

    def summary(self) -> str:
        lines = ["=== 平行志愿 结果模拟 ==="]
        lines.append(f"模拟次数: {self.n_trials:,}")
        lines.append("")
        lines.append("按学校层次:")
        for tier in ['985', '211', '双一流', '本科', '专科']:
            prob = self.tier_probs.get(tier, 0)
            if prob > 0.001:
                bar = '█' * int(prob * 30)
                lines.append(f"  {tier:<6} {prob*100:>5.1f}%  {bar}")
        if self.no_admission_prob > 0.001:
            lines.append(f"  未录取   {self.no_admission_prob*100:>5.1f}%")
        lines.append("")
        lines.append("按录取难度:")
        for tag in ['冲', '稳', '保']:
            prob = self.tag_probs.get(tag, 0)
            if prob > 0.001:
                lines.append(f"  {tag}志愿  {prob*100:>5.1f}%")
        lines.append("")
        lines.append(f"中位录取志愿序号: 第{self.p50_slot}个")
        lines.append(f"90%概率在前{self.p90_slot}个志愿内录取")
        return '\n'.join(lines)


def simulate(
    recs: list,           # List[Recommendation], in order (slot 1, 2, ...)
    n: int = 10_000,
    seed: Optional[int] = 42,
) -> SimulationResult:
    """
    Simulate n trials of the 平行志愿 cascade.

    Each trial:
      - For each school in order, draw Bernoulli(p_i)
      - First success = admitted school
      - If all fail = no admission
    """
    if seed is not None:
        random.seed(seed)

    tier_counts = Counter()
    tag_counts = Counter()
    slots = []
    no_admission = 0

    for _ in range(n):
        admitted = False
        for slot_idx, rec in enumerate(recs):
            if random.random() < rec.p:
                tier_counts[rec.tier] += 1
                tag_counts[rec.tag] += 1
                slots.append(slot_idx + 1)
                admitted = True
                break
        if not admitted:
            no_admission += 1

    total = n
    tier_probs = {t: c / total for t, c in tier_counts.items()}
    tag_probs  = {t: c / total for t, c in tag_counts.items()}

    slots_sorted = sorted(slots)
    avg_slot = sum(slots) / len(slots) if slots else len(recs)
    # Percentiles over ALL n trials (not just admitted ones).
    # Trials with no admission are implicitly ordered last (slot = ∞).
    # p50/p90 index into the full population so that a high no-admission rate
    # is correctly reflected: if the Kth percentile falls beyond len(slots_sorted),
    # the student at that percentile has no admission → return len(recs) as sentinel.
    p50_idx  = int(n * 0.50)
    p50_slot = slots_sorted[p50_idx] if p50_idx < len(slots_sorted) else len(recs)
    p90_idx  = int(n * 0.90)
    p90_slot = slots_sorted[p90_idx] if p90_idx < len(slots_sorted) else len(recs)

    return SimulationResult(
        n_trials=n,
        tier_counts=dict(tier_counts),
        tier_probs=tier_probs,
        no_admission_count=no_admission,
        no_admission_prob=no_admission / total,
        tag_counts=dict(tag_counts),
        tag_probs=tag_probs,
        avg_slot=avg_slot,
        p50_slot=p50_slot,
        p90_slot=p90_slot,
    )


