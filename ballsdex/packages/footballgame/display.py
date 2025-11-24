from typing import TYPE_CHECKING

import discord

from ballsdex.packages.footballgame.game_user import GameUser

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


def _get_prefix_emote(player: GameUser) -> str:
    if player.cancelled:
        return "\N{NO ENTRY SIGN}"
    elif player.won:
        return "\N{TROPHY}"
    elif player.locked:
        return "\N{WHITE HEAVY CHECK MARK}"
    else:
        return ""


def _get_player_name(player: GameUser, is_admin: bool = False) -> str:
    if is_admin:
        blacklisted = "\N{NO MOBILE PHONES} " if player.blacklisted else ""
        return f"{blacklisted}{_get_prefix_emote(player)} {player.user.name} ({player.user.id})"
    else:
        return f"{_get_prefix_emote(player)} {player.user.name}"


def _build_team_display(player: GameUser, bot: "BallsDexBot") -> str:
    """Build a formatted display of the team organized by position."""
    lines = []
    
    positions = ["GK", "DF", "MF", "FW"]
    position_caps = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
    position_names = {
        "GK": "Goalkeeper",
        "DF": "Defender",
        "MF": "Midfielder",
        "FW": "Forward"
    }
    
    for pos in positions:
        players = player.team.get(pos, [])
        cap = position_caps[pos]
        current = len(players)
        
        if players:
            lines.append(f"**{pos} ({current}/{cap})**")
            for p in players:
                emoji = bot.get_emoji(p.countryball.emoji_id) if hasattr(p.countryball, 'emoji_id') else ""
                name = p.countryball.country if hasattr(p, 'countryball') else f"Player {p.pk}"
                stats = f"ATK: {p.attack} | HP: {p.health}"
                if player.locked:
                    lines.append(f"• *{emoji} {name} | {stats}*")
                else:
                    lines.append(f"• {emoji} {name} | {stats}")
        else:
            lines.append(f"**{pos} ({current}/{cap})**")
            lines.append("• *Empty*")
    
    if player.bets:
        lines.append(f"\n**BET**")
        for bet_ball in player.bets:
            emoji = bot.get_emoji(bet_ball.countryball.emoji_id) if hasattr(bet_ball.countryball, 'emoji_id') else ""
            name = bet_ball.countryball.country if hasattr(bet_ball, 'countryball') else f"Player {bet_ball.pk}"
            bet_id = f"#{bet_ball.pk}" if hasattr(bet_ball, 'pk') else ""
            stats = f"ATK: {bet_ball.attack} | HP: {bet_ball.health}"
            lines.append(f"• {emoji} {bet_id} {name} {stats}")
    
    result = "\n".join(lines)
    
    if player.cancelled:
        result = f"~~{result}~~"
    
    return result if result else "*Empty team*"


def fill_game_embed_fields(
    embed: discord.Embed,
    bot: "BallsDexBot",
    player1: GameUser,
    player2: GameUser,
    is_admin: bool = False,
):
    """
    Fill the fields of an embed with the team compositions.

    Parameters
    ----------
    embed: discord.Embed
        The embed being updated. Its fields are cleared.
    bot: BallsDexBot
        The bot object, used for getting emojis.
    player1: GameUser
        The player that initiated the game, displayed on the left side.
    player2: GameUser
        The player that was invited to the game, displayed on the right side.
    is_admin: bool
        Whether admin information should be shown.
    """
    embed.clear_fields()

    player1_display = _build_team_display(player1, bot)
    player2_display = _build_team_display(player2, bot)

    # Truncate if too long (Discord field limit is 1024 characters)
    if len(player1_display) > 1024:
        player1_display = player1_display[:1020] + "..."
    if len(player2_display) > 1024:
        player2_display = player2_display[:1020] + "..."

    embed.add_field(
        name=_get_player_name(player1, is_admin),
        value=player1_display,
        inline=True,
    )
    embed.add_field(
        name=_get_player_name(player2, is_admin),
        value=player2_display,
        inline=True,
    )
