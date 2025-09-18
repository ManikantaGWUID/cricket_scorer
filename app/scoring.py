from typing import Dict, Tuple, Optional

EXTRAS_NO_LEGAL_BALL = {"wide", "no-ball"}      # not a legal delivery
EXTRAS_LEGAL_NO_BAT  = {"bye", "leg-bye"}       # legal ball, runs not to batter

def balls_to_overs(balls: int) -> str:
    return f"{balls // 6}.{balls % 6}"

def is_legal_ball(extra: Optional[str]) -> bool:
    return extra not in EXTRAS_NO_LEGAL_BALL

# ---------- BATSMEN HELPERS ----------

def _ensure_batter(scorecards: Dict, innings_key: str, player_id: Optional[str]):
    if not player_id:
        return
    sc = scorecards.setdefault(innings_key, {}).setdefault("batting", {})
    if player_id not in sc:
        sc[player_id] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0, "out": False, "how_out": None}

def update_batting_scorecard(scorecards: Dict, innings_key: str, striker_id: Optional[str], ev) -> Dict:
    """Accumulates batter stats. Handles:
       - legal balls +1 to striker balls
       - runs to bat except bye/leg-bye
       - wides: no ball faced; no runs to bat
       - no-ball: +bat runs without ball faced
       - 4/6 counting
       - dismissal marks whichever player_id in ev.player_out
    """
    sc_inn = scorecards.setdefault(innings_key, {}).setdefault("batting", {})
    extra = ev.extra
    runs = ev.runs or 0
    legal = is_legal_ball(extra)

    # make sure striker row exists if known
    if striker_id:
        _ensure_batter(scorecards, innings_key, striker_id)

    if extra in EXTRAS_NO_LEGAL_BALL:
        # wide / no-ball: no ball faced
        if extra == "no-ball" and runs > 0 and striker_id:
            sc_inn[striker_id]["runs"] += runs
            if runs == 4: sc_inn[striker_id]["fours"] += 1
            if runs == 6: sc_inn[striker_id]["sixes"] += 1
    else:
        # legal ball
        if striker_id:
            sc_inn[striker_id]["balls"] += 1
        if extra in EXTRAS_LEGAL_NO_BAT:
            pass  # team runs only
        else:
            if striker_id:
                sc_inn[striker_id]["runs"] += runs
                if runs == 4: sc_inn[striker_id]["fours"] += 1
                if runs == 6: sc_inn[striker_id]["sixes"] += 1

    # Mark dismissal row even if it’s the non-striker
    if getattr(ev, "wicket_kind", None) and getattr(ev, "player_out", None):
        out_id = ev.player_out
        _ensure_batter(scorecards, innings_key, out_id)
        sc_inn[out_id]["out"] = True
        sc_inn[out_id]["how_out"] = ev.wicket_kind

    return scorecards

# ---------- BOWLER HELPERS ----------

def _ensure_bowler(scorecards: Dict, innings_key: str, bowler_id: Optional[str]):
    if not bowler_id:
        return
    bowl = scorecards.setdefault(innings_key, {}).setdefault("bowling", {})
    if bowler_id not in bowl:
        bowl[bowler_id] = {"balls": 0, "overs": "0.0", "runs": 0, "wickets": 0, "maidens": 0, "current_over_runs": 0}

def _bowler_runs_for_ball(ev) -> int:
    """Charge to bowler:
       - wide: 1
       - no-ball: 1 + bat runs (if any)
       - bye/leg-bye: 0
       - normal: ev.runs
    """
    extra = ev.extra
    r = ev.runs or 0
    if extra == "wide":
        return 1
    if extra == "no-ball":
        return 1 + max(0, r)
    if extra in EXTRAS_LEGAL_NO_BAT:
        return 0
    return r

def _is_bowler_wicket(kind: Optional[str]) -> bool:
    if not kind:
        return False
    # count everything except pure runout as a bowler wicket (simple rule)
    return kind.lower() not in {"runout", "run-out", "run out"}

def update_bowling_scorecard(scorecards: Dict, innings_key: str, bowler_id: Optional[str], ev, legal: bool) -> Tuple[Dict, bool]:
    """Update bowler figures and return (scorecards, over_ended_now?)."""
    over_ended_now = False
    if not bowler_id:
        return scorecards, over_ended_now

    bowl = scorecards.setdefault(innings_key, {}).setdefault("bowling", {})
    _ensure_bowler(scorecards, innings_key, bowler_id)
    row = bowl[bowler_id]

    # charge runs for this ball
    bruns = _bowler_runs_for_ball(ev)
    row["runs"] += bruns
    row["current_over_runs"] += bruns

    # legal delivery increments balls
    if legal:
        row["balls"] += 1

    # wicket (if credited to bowler)
    if _is_bowler_wicket(getattr(ev, "wicket_kind", None)):
        row["wickets"] += 1

    # overs string
    row["overs"] = balls_to_overs(row["balls"])

    # If legal and just finished an over, check for maiden
    if legal and (row["balls"] % 6 == 0):
        if row["current_over_runs"] == 0:
            row["maidens"] += 1
        row["current_over_runs"] = 0
        over_ended_now = True

    return scorecards, over_ended_now

# ---------- TEAM TOTALS + STRIKE ----------

def apply_ball_auto(score: Dict, current: Dict, ev) -> Tuple[Dict, Dict, bool]:
    """Updates team totals and strike/over/ball counters. Returns (score, current, over_ended)."""
    runs = ev.runs or 0
    extra = ev.extra
    legal = is_legal_ball(extra)

    # team runs
    if extra in EXTRAS_NO_LEGAL_BALL:
        score['runs'] += max(1, runs)
    else:
        score['runs'] += runs

    # wicket increments team wickets
    if getattr(ev, 'wicket_kind', None):
        score['wickets'] += 1

    over_ended = False
    if legal:
        score['balls'] += 1
        current['ball_in_over'] = (current.get('ball_in_over', 0) + 1)
        # strike swap on odd runs (includes byes/leg-byes)
        if runs % 2 == 1:
            current['striker'], current['non_striker'] = current['non_striker'], current['striker']
        if current['ball_in_over'] >= 6:
            current['ball_in_over'] = 0
            current['over'] = current.get('over', 0) + 1
            over_ended = True
            # swap strike at over end
            current['striker'], current['non_striker'] = current['non_striker'], current['striker']

    score['overs'] = balls_to_overs(score['balls'])
    return score, current, over_ended
