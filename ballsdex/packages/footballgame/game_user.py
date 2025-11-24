from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ballsdex.core.models import BlacklistedID

if TYPE_CHECKING:
    import discord

    from ballsdex.core.bot import BallsDexBot
    from ballsdex.core.models import BallInstance, Player


@dataclass(slots=True)
class GameUser:
    user: "discord.User | discord.Member"
    player: "Player"
    team: dict[str, list["BallInstance"]] = field(
        default_factory=lambda: {"GK": [], "DF": [], "MF": [], "FW": []}
    )
    bets: list["BallInstance"] = field(default_factory=list)
    locked: bool = False
    cancelled: bool = False
    won: bool = False
    blacklisted: bool | None = None

    def get_all_players(self) -> list["BallInstance"]:
        """Get all players from all positions."""
        all_players = []
        for position_players in self.team.values():
            all_players.extend(position_players)
        return all_players

    def get_team_strength(self) -> float:
        """Calculate team strength based on attack and health stats."""
        total_strength = 0.0
        all_players = self.get_all_players()
        
        if not all_players:
            return 0.0
        
        for player in all_players:
            total_strength += player.attack + player.health
        
        return total_strength / len(all_players)

    def has_minimum_team(self) -> bool:
        """Check if team has at least one player per position."""
        return all(len(players) >= 1 for players in self.team.values())
