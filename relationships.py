# relationships.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import json
import re

# -----------------------------
# Helpers
# -----------------------------
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def norm_name(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s

def make_id(person_type: str, display_name: str) -> str:
    return f"{person_type}:{norm_name(display_name)}"

# -----------------------------
# Stats containers
# -----------------------------
@dataclass
class StatLine:
    starts: int
    wins: int
    win_pct: float
    roi: Optional[float] = None

@dataclass
class BoostResult:
    boost_prob: float       # probability units (0.01 = +1pp)
    boost_points: float     # percentage points (+1.0 = +1pp)
    confidence: float       # 0..1
    used_starts: int
    reason: str

# -----------------------------
# Loading caches
# -----------------------------
def load_json_cache(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

def _cache_key(
    left_id: str,
    right_id: str,
    window: str,
    track_code: str = "",
    surface: str = "",
    dist: str = "",
    cls: str = "",
) -> str:
    return "|".join([left_id, right_id, window, track_code or "", surface or "", dist or "", cls or ""])

def lookup_rel(
    cache: Dict[str, Any],
    left_id: str,
    right_id: str,
    window: str,
    *,
    track_code: str = "",
    surface: str = "",
    dist: str = "",
    cls: str = "",
) -> Optional[StatLine]:
    # specific -> fallback
    keys = [
        _cache_key(left_id, right_id, window, track_code, surface, dist, cls),
        _cache_key(left_id, right_id, window, track_code, surface, dist, ""),
        _cache_key(left_id, right_id, window, track_code, surface, "", ""),
        _cache_key(left_id, right_id, window, track_code, "", "", ""),
        _cache_key(left_id, right_id, window, "", "", "", ""),
    ]
    for k in keys:
        v = cache.get(k)
        if not v:
            continue
        starts = int(v.get("starts", 0))
        wins = int(v.get("wins", 0))
        win_pct = float(v.get("win_pct", 0.0))
        roi = v.get("roi", None)
        roi = float(roi) if roi is not None else None
        return StatLine(starts=starts, wins=wins, win_pct=win_pct, roi=roi)
    return None

# -----------------------------
# Relationship math (safe + shrinkage)
# -----------------------------
def confidence_from_starts(starts: int, min_starts: int, cap_starts: int = 200) -> float:
    if starts < min_starts:
        return 0.0
    return clamp((starts - min_starts) / (cap_starts - min_starts), 0.0, 1.0)

def bayes_shrink_win_pct(rel: StatLine, baseline_win_pct: float, prior_starts: int = 80) -> float:
    if rel.starts <= 0:
        return baseline_win_pct
    rel_rate = rel.wins / rel.starts
    return ((rel_rate * rel.starts) + (baseline_win_pct * prior_starts)) / (rel.starts + prior_starts)

def relationship_boost(
    rel: Optional[StatLine],
    baseline_win_pct: float,
    *,
    min_starts: int,
    prior_starts: int,
    weight: float,
    max_boost_pp: float,
    label: str
) -> BoostResult:
    if rel is None or rel.starts <= 0:
        return BoostResult(0.0, 0.0, 0.0, 0, f"{label}: no data")

    conf = confidence_from_starts(rel.starts, min_starts=min_starts)
    if conf == 0.0:
        return BoostResult(0.0, 0.0, 0.0, rel.starts, f"{label}: sample<{min_starts} (ignored)")

    shrunk_rel = bayes_shrink_win_pct(rel, baseline_win_pct, prior_starts=prior_starts)
    raw_delta = (shrunk_rel - baseline_win_pct)  # prob units
    weighted_pp = clamp(raw_delta * weight * 100.0, -max_boost_pp, max_boost_pp)

    sign = "positive" if weighted_pp > 0 else "negative" if weighted_pp < 0 else "neutral"
    reason = f"{label}: {sign} vs baseline (starts={rel.starts}, win%={rel.win_pct:.1%})"

    return BoostResult(
        boost_prob=weighted_pp / 100.0,
        boost_points=weighted_pp,
        confidence=conf,
        used_starts=rel.starts,
        reason=reason
    )

def apply_boosts(base_prob: float, tj: BoostResult, to: BoostResult, max_total_boost_pp: float = 5.0) -> float:
    total_pp = clamp(tj.boost_points + to.boost_points, -max_total_boost_pp, max_total_boost_pp)
    return clamp(base_prob + total_pp / 100.0, 0.001, 0.999)

# -----------------------------
# Track weighting presets
# -----------------------------
TRACK_REL_WEIGHTS = {
    "FG":  (0.60, 0.50),
    "OP":  (0.70, 0.40),
    "CD":  (0.70, 0.40),
    "GP":  (0.75, 0.35),
    "SAR": (0.65, 0.45),
    "KEE": (0.65, 0.45),
    "AQU": (0.60, 0.50),
    "TP":  (0.55, 0.55),
}

def get_track_weights(track_code: str) -> Tuple[float, float]:
    return TRACK_REL_WEIGHTS.get((track_code or "").upper(), (0.65, 0.45))

def fair_odds_from_prob(prob: float) -> str:
    # keep your current string style like "9/2" later; for now just decimal-ish fraction string
    prob = clamp(prob, 0.001, 0.999)
    o = (1.0 / prob) - 1.0
    # simple rounding
    return f"{o:.2f}-1"
