from typing import Optional, Literal, List
from pydantic import BaseModel, Field

class CreateMatch(BaseModel):
    teamA: str
    teamB: str
    overs_per_innings: int = Field(ge=1, le=50)

class Toss(BaseModel):
    winner: str
    decision: Literal['bat', 'bowl']

class SwitchInnings(BaseModel):
    to_innings: Literal[1, 2]

class BallEvent(BaseModel):
    innings: Literal[1, 2]
    over: int
    ball_in_over: int
    runs: int = 0
    extra: Optional[Literal['wide','no-ball','bye','leg-bye']] = None
    wicket_kind: Optional[str] = None
    player_out: Optional[str] = None
    commentary: Optional[str] = None

# Players pool + teams flow
class SeedPlayers(BaseModel):
    names: List[str]

class SetSquads(BaseModel):
    teamA: List[str]
    teamB: List[str]

class SetElevens(BaseModel):
    teamA: List[str]  # NOTE: can be < 11
    teamB: List[str]

class SetOpeners(BaseModel):
    innings: Literal[1, 2]
    striker: str
    non_striker: str
    bowler: str

class ChangeBowler(BaseModel):
    bowler: str

class SetBatsmen(BaseModel):
    striker: Optional[str] = None
    non_striker: Optional[str] = None

class BallEventAuto(BaseModel):
    innings: Literal[1, 2]
    runs: int = 0
    extra: Optional[Literal['wide','no-ball','bye','leg-bye']] = None
    wicket_kind: Optional[str] = None
    player_out: Optional[str] = None
    commentary: Optional[str] = None

# NEW: captains / toss / draft
class SetCaptains(BaseModel):
    teamA_captain: str
    teamB_captain: str

class TossCall(BaseModel):
    caller: Literal["teamA", "teamB"]
    call: Literal["heads", "tails"]

class DraftPick(BaseModel):
    team: Literal["teamA", "teamB"]
    player: str  # playerId/slug from players pool
