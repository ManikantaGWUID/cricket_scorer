import os, random
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

from app.firebase_client import db
from app.models import (
    CreateMatch, BallEvent, Toss, SwitchInnings,
    SeedPlayers, SetSquads, SetElevens, SetOpeners,
    ChangeBowler, SetBatsmen, BallEventAuto,
    SetCaptains, TossCall, DraftPick
)
from app.scoring import (
    apply_ball_auto,
    update_batting_scorecard,
    update_bowling_scorecard,
    _ensure_batter,   # for seeding on openers/set_batsmen
)

load_dotenv()

APP_SECRET = os.getenv("SECRET_KEY", "change-me")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
def _fielding_side_xi(match_doc: dict, innings: int):
    xi = (match_doc.get("playing_eleven") or {})
    if innings == 1:
        # Team B fields while Team A bats (simple assumption)
        return xi.get("teamB", [])
    else:
        return xi.get("teamA", [])
# -------- Auth helpers --------
@app.get("/admin")
def admin_landing(request: Request):
    if request.session.get("auth") is True:
        return RedirectResponse(url="/admin/match/new", status_code=302)
    return RedirectResponse(url="/login", status_code=302)

DEF_SESSION_KEY = "auth"
def require_admin(request: Request):
    if request.session.get(DEF_SESSION_KEY) is not True:
        raise HTTPException(status_code=401, detail="Unauthorized")

# -------- Pages --------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("viewer.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        request.session[DEF_SESSION_KEY] = True
        return RedirectResponse(url="/admin/match/new", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

# -------- Admin multi-page --------
@app.get("/admin/match/new", response_class=HTMLResponse)
def match_new_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse("match_new.html", {"request": request})

@app.get("/admin/match/{matchId}/teams", response_class=HTMLResponse)
def match_teams_page(matchId: str, request: Request):
    require_admin(request)
    return templates.TemplateResponse("match_teams.html", {"request": request, "matchId": matchId})

@app.get("/admin/match/{matchId}/toss", response_class=HTMLResponse)
def match_toss_page(matchId: str, request: Request):
    require_admin(request)
    return templates.TemplateResponse("match_toss.html", {"request": request, "matchId": matchId})

@app.get("/admin/match/{matchId}/scoring", response_class=HTMLResponse)
def match_scoring_page(matchId: str, request: Request):
    require_admin(request)
    return templates.TemplateResponse("match_scoring.html", {"request": request, "matchId": matchId})

# -------- API: matches basic --------
@app.post("/api/matches")
def create_match(req: CreateMatch, request: Request):
    require_admin(request)
    now = datetime.utcnow()
    doc = {
        "status": "scheduled",
        "teamA": req.teamA.strip(),
        "teamB": req.teamB.strip(),
        "overs_per_innings": req.overs_per_innings,
        "toss": {"winner": None, "decision": None},
        "current_innings": 1,
        "score": {
            "1": {"runs": 0, "wickets": 0, "balls": 0, "overs": "0.0"},
            "2": {"runs": 0, "wickets": 0, "balls": 0, "overs": "0.0"}
        },
        "target": None,
        "created_at": now,
        "updated_at": now,
    }
    ref = db.collection("matches").add(doc)[1]
    return {"matchId": ref.id}

@app.get("/api/matches/{matchId}")
def get_match(matchId: str):
    snap = db.collection("matches").document(matchId).get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    d = snap.to_dict()
    d["id"] = snap.id
    return d

@app.post("/api/matches/{matchId}/toss")
def set_toss(matchId: str, req: Toss, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    match_ref.update({"toss": {"winner": req.winner, "decision": req.decision}, "status": "live", "updated_at": datetime.utcnow()})
    return {"ok": True}

@app.post("/api/matches/{matchId}/ball")
def record_ball(matchId: str, ev: BallEvent, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    snap = match_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Match not found")
    data = snap.to_dict()

    innings_key = str(ev.innings)
    score = data["score"][innings_key]

    score = apply_ball(score, ev)

    event_doc = {
        "type": "ball",
        "innings": ev.innings,
        "over": ev.over,
        "ball_in_over": ev.ball_in_over,
        "runs": ev.runs,
        "extra": ev.extra,
        "wicket": None if not ev.wicket_kind else {"kind": ev.wicket_kind, "player_out": ev.player_out},
        "commentary": ev.commentary,
        "ts": datetime.utcnow()
    }
    match_ref.collection("events").add(event_doc)

    data["score"][innings_key] = score
    data["updated_at"] = datetime.utcnow()
    match_ref.update({"score": data["score"], "updated_at": data["updated_at"]})
    return {"ok": True, "score": score}

@app.post("/api/matches/{matchId}/switch_innings")
def switch_innings(matchId: str, req: SwitchInnings, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    snap = match_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Match not found")
    data = snap.to_dict()

    if req.to_innings == 2 and data.get("target") is None:
        target = data["score"]["1"]["runs"] + 1
    else:
        target = data.get("target")

    match_ref.update({
        "current_innings": req.to_innings,
        "target": target,
        "updated_at": datetime.utcnow()
    })

    match_ref.collection("events").add({
        "type": "innings_switch",
        "innings": req.to_innings,
        "ts": datetime.utcnow()
    })

    return {"ok": True, "current_innings": req.to_innings, "target": target}

@app.post("/api/matches/{matchId}/complete")
def complete_match(matchId: str, request: Request):
    require_admin(request)
    db.collection("matches").document(matchId).update({"status": "completed", "updated_at": datetime.utcnow()})
    return {"ok": True}

# -------- Players / Squads / XI / Openers / Ball Auto --------
@app.get("/api/players")
def list_players():
    docs = db.collection("players").stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]

@app.post("/api/players/seed")
def seed_players(req: SeedPlayers, request: Request):
    require_admin(request)
    import re
    batch = db.batch()
    for name in req.names:
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
        if not slug: 
            continue
        ref = db.collection("players").document(slug)
        batch.set(ref, {"name": name.strip()})
    batch.commit()
    return {"ok": True}

@app.post("/api/matches/{matchId}/squads")
def set_squads(matchId: str, req: SetSquads, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    if not match_ref.get().exists:
        raise HTTPException(404, "Match not found")
    match_ref.update({
        "squads": {"teamA": req.teamA, "teamB": req.teamB},
        "updated_at": datetime.utcnow()
    })
    return {"ok": True}

@app.post("/api/matches/{matchId}/elevens")
def set_elevens(matchId: str, req: SetElevens, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    if not match_ref.get().exists:
        raise HTTPException(404, "Match not found")
    # Allow less than 11 too
    match_ref.update({
        "playing_eleven": {"teamA": req.teamA, "teamB": req.teamB},
        "updated_at": datetime.utcnow()
    })
    return {"ok": True}


@app.post("/api/matches/{matchId}/openers")
def set_openers(matchId: str, req: SetOpeners, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    snap = match_ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    data = snap.to_dict()

    current = {
        "striker": req.striker,
        "non_striker": req.non_striker,
        "bowler": req.bowler,
        "over": 0 if req.innings == 1 else data.get("current", {}).get("over", 0),
        "ball_in_over": 0
    }

    # seed scorecards entries so they show up before first ball
    scorecards = data.get("scorecards") or {"1": {}, "2": {}}
    _ensure_batter(scorecards, str(req.innings), req.striker)
    _ensure_batter(scorecards, str(req.innings), req.non_striker)

    match_ref.update({
        "current": current,
        "current_innings": req.innings,
        "status": "live",
        "scorecards": scorecards,
        "updated_at": datetime.utcnow()
    })
    return {"ok": True, "current": current}

@app.post("/api/matches/{matchId}/change_bowler")
def change_bowler(matchId: str, req: ChangeBowler, request: Request):
    require_admin(request)
    ref = db.collection("matches").document(matchId)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    cur = snap.to_dict().get("current", {})
    cur['bowler'] = req.bowler
    ref.update({"current": cur, "updated_at": datetime.utcnow()})
    return {"ok": True}

@app.post("/api/matches/{matchId}/set_batsmen")
def set_batsmen(matchId: str, req: SetBatsmen, request: Request):
    require_admin(request)
    ref = db.collection("matches").document(matchId)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    data = snap.to_dict()
    cur = data.get("current", {})
    if req.striker:
        cur['striker'] = req.striker
    if req.non_striker:
        cur['non_striker'] = req.non_striker

    # seed batting rows for whoever was set
    scorecards = data.get("scorecards") or {"1": {}, "2": {}}
    _ensure_batter(scorecards, str(data.get("current_innings", 1)), req.striker)
    _ensure_batter(scorecards, str(data.get("current_innings", 1)), req.non_striker)

    ref.update({"current": cur, "scorecards": scorecards, "updated_at": datetime.utcnow()})
    return {"ok": True}
@app.post("/api/matches/{matchId}/ball_auto")
def record_ball_auto(matchId: str, ev: BallEventAuto, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    snap = match_ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    data = snap.to_dict()

    innings_key = str(ev.innings)
    score = data['score'][innings_key]
    current = data.get('current') or {}
    scorecards = data.get('scorecards') or {"1": {}, "2": {}}

    # 1) Team totals + strike
    score, current, over_ended_team = apply_ball_auto(score, current, ev)

    # 2) Batting (striker) + wicket marking
    striker_id = current.get('striker')
    scorecards = update_batting_scorecard(scorecards, innings_key, striker_id, ev)

    # 3) Bowling (current bowler) with proper runs/wickets + detect if *bowler* completed an over
    legal = (ev.extra not in {"wide", "no-ball"})
    bowler_id = current.get("bowler")
    scorecards, over_ended_bowler = update_bowling_scorecard(scorecards, innings_key, bowler_id, ev, legal)

    # agree whether over ended (either calc should match)
    over_ended = over_ended_team or over_ended_bowler

    # 4) Persist event
    event_doc = {
        "type": "ball",
        "innings": ev.innings,
        "runs": ev.runs,
        "extra": ev.extra,
        "wicket": None if not ev.wicket_kind else {"kind": ev.wicket_kind, "player_out": ev.player_out},
        "striker": striker_id,
        "bowler": bowler_id,
        "commentary": ev.commentary,
        "ts": datetime.utcnow()
    }
    match_ref.collection("events").add(event_doc)

    # 5) Save match doc
    data['score'][innings_key] = score

    # if over ended: clear current.bowler (force select next) and expose a hint
    if over_ended:
        current['last_bowler'] = bowler_id
        current['bowler'] = None  # front-end must set next via change_bowler

    match_ref.update({
        "score": data['score'],
        "current": current,
        "scorecards": scorecards,
        "updated_at": datetime.utcnow()
    })

    return {
        "ok": True,
        "score": score,
        "current": current,
        "over_ended": over_ended,
        "scorecards": {
            "batting": scorecards[innings_key].get("batting", {}),
            "bowling": scorecards[innings_key].get("bowling", {})
        }
    }

# -------- Helper lists --------
@app.get("/api/matches/{matchId}/dismissed")
def list_dismissed(matchId: str):
    events = db.collection("matches").document(matchId).collection("events").where("wicket", "!=", None).stream()
    out = []
    for e in events:
        w = e.to_dict().get("wicket")
        if w and w.get("player_out"):
            out.append(w["player_out"])
    return sorted(set(out))

@app.get("/api/matches/{matchId}/available_batters")
def available_batters(matchId: str, innings: int):
    mref = db.collection("matches").document(matchId)
    msnap = mref.get()
    if not msnap.exists:
        raise HTTPException(404, "Match not found")
    m = msnap.to_dict()

    xi = (m.get("playing_eleven") or {})
    # Simple assumption: innings 1 uses teamA XI; innings 2 uses teamB XI
    batting_side = "teamA" if innings == 1 else "teamB"
    batting = xi.get(batting_side, [])

    cur = m.get("current") or {}
    striker = cur.get("striker")
    non_striker = cur.get("non_striker")

    events = mref.collection("events").where("wicket", "!=", None).where("innings", "==", innings).stream()
    dismissed = set()
    for e in events:
        w = e.to_dict().get("wicket")
        if w and w.get("player_out"):
            dismissed.add(w["player_out"])

    skip = set([p for p in [striker, non_striker] if p])
    avail = [p for p in batting if p not in dismissed and p not in skip]
    return avail
# ---------- Captains ----------
@app.post("/api/matches/{matchId}/captains")
def set_captains(matchId: str, req: SetCaptains, request: Request):
    require_admin(request)
    ref = db.collection("matches").document(matchId)
    if not ref.get().exists:
        raise HTTPException(404, "Match not found")
    ref.update({
        "captains": {"teamA": req.teamA_captain, "teamB": req.teamB_captain},
        "updated_at": datetime.utcnow()
    })
    return {"ok": True}

# ---------- Coin Toss (random) ----------
@app.post("/api/matches/{matchId}/toss_coin")
def toss_coin(matchId: str, req: TossCall, request: Request):
    require_admin(request)
    ref = db.collection("matches").document(matchId)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")

    # Flip a fair coin
    result = random.choice(["heads", "tails"])
    caller = req.caller  # "teamA" or "teamB"
    call = req.call

    winner = caller if result == call else ("teamB" if caller == "teamA" else "teamA")

    # Winner gets first pick in draft
    draft = {"teamA": [], "teamB": [], "next": winner, "done": False}
    ref.update({
        "toss": {"caller": caller, "call": call, "result": result, "winner": winner},
        "draft": draft,
        "updated_at": datetime.utcnow()
    })
    return {"ok": True, "result": result, "winner": winner}

# ---------- Draft: state ----------
@app.get("/api/matches/{matchId}/draft/state")
def draft_state(matchId: str):
    ref = db.collection("matches").document(matchId)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    m = snap.to_dict()

    # players pool
    players = [{"id": d.id, **d.to_dict()} for d in db.collection("players").stream()]

    draft = m.get("draft") or {"teamA": [], "teamB": [], "next": "teamA", "done": False}
    picked = set(draft.get("teamA", []) + draft.get("teamB", []))
    available = [p for p in players if p["id"] not in picked]

    return {
        "draft": draft,
        "available": available,
        "teamA_picks": draft.get("teamA", []),
        "teamB_picks": draft.get("teamB", []),
        "playing_eleven": m.get("playing_eleven") or {"teamA": [], "teamB": []}
    }

# ---------- Draft: pick one player (alternating) ----------
@app.post("/api/matches/{matchId}/draft/pick")
def draft_pick(matchId: str, req: DraftPick, request: Request):
    require_admin(request)
    ref = db.collection("matches").document(matchId)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    m = snap.to_dict()

    draft = m.get("draft")
    if not draft:
        raise HTTPException(400, "Draft not initialized. Run toss first.")

    if draft.get("done"):
        raise HTTPException(400, "Draft already marked done.")

    # enforce turn
    if draft.get("next") != req.team:
        raise HTTPException(400, f"It is {draft.get('next')}'s turn")

    # ensure player exists and not already picked
    if not db.collection("players").document(req.player).get().exists:
        raise HTTPException(400, "Player not found in pool")
    if req.player in (draft.get("teamA", []) + draft.get("teamB", [])):
        raise HTTPException(400, "Player already picked")

    # append
    draft[req.team] = draft.get(req.team, []) + [req.player]
    # set next team
    draft["next"] = "teamB" if req.team == "teamA" else "teamA"

    # Update playing_eleven to match draft (no strict size; can be < 11)
    playing = m.get("playing_eleven") or {"teamA": [], "teamB": []}
    playing[req.team] = draft[req.team]

    ref.update({
        "draft": draft,
        "playing_eleven": playing,
        "updated_at": datetime.utcnow()
    })
    return {"ok": True, "draft": draft, "playing_eleven": playing}

# ---------- Draft: finish (optional, can be < 11) ----------
@app.post("/api/matches/{matchId}/draft/finish")
def draft_finish(matchId: str, request: Request):
    require_admin(request)
    ref = db.collection("matches").document(matchId)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    m = snap.to_dict()
    draft = m.get("draft") or {}
    draft["done"] = True
    ref.update({"draft": draft, "updated_at": datetime.utcnow()})
    return {"ok": True, "draft": draft}


@app.get("/api/matches/{matchId}/scorecard")
def get_scorecard(matchId: str, innings: int = 1):
    snap = db.collection("matches").document(matchId).get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    m = snap.to_dict()
    sc = (m.get("scorecards") or {}).get(str(innings), {"batting": {}})
    return sc
@app.post("/api/matches/{matchId}/ball_auto")
def record_ball_auto(matchId: str, ev: BallEventAuto, request: Request):
    require_admin(request)
    match_ref = db.collection("matches").document(matchId)
    snap = match_ref.get()
    if not snap.exists:
        raise HTTPException(404, "Match not found")
    data = snap.to_dict()

    innings_key = str(ev.innings)
    score = data['score'][innings_key]
    current = data.get('current') or {}
    scorecards = data.get('scorecards') or {"1": {}, "2": {}}

    # 1) Team totals + strike
    score, current, over_ended_team = apply_ball_auto(score, current, ev)

    # 2) Batting (striker) + wicket marking
    striker_id = current.get('striker')
    scorecards = update_batting_scorecard(scorecards, innings_key, striker_id, ev)

    # 3) Bowling (current bowler) with proper runs/wickets + detect if *bowler* completed an over
    legal = (ev.extra not in {"wide", "no-ball"})
    bowler_id = current.get("bowler")
    scorecards, over_ended_bowler = update_bowling_scorecard(scorecards, innings_key, bowler_id, ev, legal)

    # agree whether over ended (either calc should match)
    over_ended = over_ended_team or over_ended_bowler

    # 4) Persist event
    event_doc = {
        "type": "ball",
        "innings": ev.innings,
        "runs": ev.runs,
        "extra": ev.extra,
        "wicket": None if not ev.wicket_kind else {"kind": ev.wicket_kind, "player_out": ev.player_out},
        "striker": striker_id,
        "bowler": bowler_id,
        "commentary": ev.commentary,
        "ts": datetime.utcnow()
    }
    match_ref.collection("events").add(event_doc)

    # 5) Save match doc
    data['score'][innings_key] = score

    # if over ended: clear current.bowler (force select next) and expose a hint
    if over_ended:
        current['last_bowler'] = bowler_id
        current['bowler'] = None  # front-end must set next via change_bowler

    match_ref.update({
        "score": data['score'],
        "current": current,
        "scorecards": scorecards,
        "updated_at": datetime.utcnow()
    })

    return {
        "ok": True,
        "score": score,
        "current": current,
        "over_ended": over_ended,
        "scorecards": {
            "batting": scorecards[innings_key].get("batting", {}),
            "bowling": scorecards[innings_key].get("bowling", {})
        }
    }
# --- Scorecard readers (ADD THESE in app/main.py) ---

from fastapi import HTTPException

@app.get("/api/matches/{matchId}/scorecard/batting")
def get_batting_card(matchId: str, innings: int = 1):
    snap = db.collection("matches").document(matchId).get()
    if not snap.exists:  # <— property, no parentheses
        raise HTTPException(status_code=404, detail="Match not found")
    match = snap.to_dict()
    scorecards = match.get("scorecards") or {}
    sc_innings = scorecards.get(str(innings)) or {}
    batting = sc_innings.get("batting") or {}
    return batting


@app.get("/api/matches/{matchId}/scorecard/bowling")
def get_bowling_card(matchId: str, innings: int = 1):
    snap = db.collection("matches").document(matchId).get()
    if not snap.exists:  # <— property, no parentheses
        raise HTTPException(status_code=404, detail="Match not found")
    match = snap.to_dict()
    scorecards = match.get("scorecards") or {}
    sc_innings = scorecards.get(str(innings)) or {}
    bowling = sc_innings.get("bowling") or {}
    return bowling