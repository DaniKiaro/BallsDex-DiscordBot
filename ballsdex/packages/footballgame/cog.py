from collections import defaultdict
from typing import TYPE_CHECKING, cast

import discord
from cachetools import TTLCache
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from ballsdex.core.models import BallInstance, Player
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.transformers import (
    BallInstanceTransform,
    SpecialEnabledTransform,
    TradeCommandType,
)
from ballsdex.packages.footballgame.game_user import GameUser
from ballsdex.packages.footballgame.menu import GameMenu
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


@app_commands.guild_only()
class FootballGame(commands.GroupCog, group_name="game"):
    """
    Challenge other players to football matches where teams compete and winner takes all.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.games: TTLCache[int, dict[int, list[GameMenu]]] = TTLCache(maxsize=999999, ttl=1800)

    def get_game(
        self,
        interaction: discord.Interaction["BallsDexBot"] | None = None,
        *,
        channel: discord.TextChannel | None = None,
        user: discord.User | discord.Member = MISSING,
    ) -> tuple[GameMenu, GameUser] | tuple[None, None]:
        """
        Find an ongoing game for the given interaction.

        Parameters
        ----------
        interaction: discord.Interaction["BallsDexBot"]
            The current interaction, used for getting the guild, channel and author.

        Returns
        -------
        tuple[GameMenu, GameUser] | tuple[None, None]
            A tuple with the `GameMenu` and `GameUser` if found, else `None`.
        """
        guild: discord.Guild
        if interaction:
            guild = cast(discord.Guild, interaction.guild)
            channel = cast(discord.TextChannel, interaction.channel)
            user = interaction.user
        elif channel:
            guild = channel.guild
        else:
            raise TypeError("Missing interaction or channel")

        if guild.id not in self.games:
            self.games[guild.id] = defaultdict(list)
        if channel.id not in self.games[guild.id]:
            return (None, None)
        to_remove: list[GameMenu] = []
        for game in self.games[guild.id][channel.id]:
            if (
                game.current_view.is_finished()
                or game.player1.cancelled
                or game.player2.cancelled
            ):
                to_remove.append(game)
                continue
            try:
                player = game._get_player(user)
            except RuntimeError:
                continue
            else:
                break
        else:
            for game in to_remove:
                self.games[guild.id][channel.id].remove(game)
            return (None, None)

        for game in to_remove:
            self.games[guild.id][channel.id].remove(game)
        return (game, player)

    @app_commands.command()
    async def start(self, interaction: discord.Interaction["BallsDexBot"], user: discord.User):
        """
        Start a football match with another player.

        Parameters
        ----------
        user: discord.User
            The user you want to challenge to a match
        """
        if user.bot:
            await interaction.response.send_message("You cannot play with bots.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "You cannot play with yourself.", ephemeral=True
            )
            return
        player1, _ = await Player.get_or_create(discord_id=interaction.user.id)
        player2, _ = await Player.get_or_create(discord_id=user.id)
        blocked = await player1.is_blocked(player2)
        if blocked:
            await interaction.response.send_message(
                "You cannot start a game with a user that you have blocked.", ephemeral=True
            )
            return
        blocked2 = await player2.is_blocked(player1)
        if blocked2:
            await interaction.response.send_message(
                "You cannot start a game with a user that has blocked you.", ephemeral=True
            )
            return

        game1, player1_obj = self.get_game(interaction)
        game2, player2_obj = self.get_game(channel=interaction.channel, user=user)
        if game1 or player1_obj:
            await interaction.response.send_message(
                "You already have an ongoing game.", ephemeral=True
            )
            return
        if game2 or player2_obj:
            await interaction.response.send_message(
                "The user you are trying to challenge is already in a game.", ephemeral=True
            )
            return

        if player2.discord_id in self.bot.blacklist:
            await interaction.response.send_message(
                "You cannot play with a blacklisted user.", ephemeral=True
            )
            return

        menu = GameMenu(
            self, interaction, GameUser(interaction.user, player1), GameUser(user, player2)
        )
        self.games[interaction.guild.id][interaction.channel.id].append(menu)
        await menu.start()
        await interaction.response.send_message("Game started!", ephemeral=True)

    @app_commands.command(extras={"game": TradeCommandType.PICK})
    async def add(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        position: str,
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Add a player to your team in a specific position.

        Parameters
        ----------
        position: str
            The position (GK, DF, MF, FW)
        countryball: BallInstance
            The player you want to add to your team
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not countryball:
            return
        
        position = position.upper()
        if position not in ["GK", "DF", "MF", "FW"]:
            await interaction.response.send_message(
                "Invalid position! Use GK, DF, MF, or FW.", ephemeral=True
            )
            return
            
        if not countryball.is_tradeable:
            await interaction.response.send_message(
                f"You cannot use this {settings.collectible_name}.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if countryball.favorite:
            view = ConfirmChoiceView(
                interaction,
                accept_message=f"{settings.collectible_name.title()} added to team.",
                cancel_message="This request has been cancelled.",
            )
            await interaction.followup.send(
                f"This {settings.collectible_name} is a favorite, "
                "are you sure you want to use it?",
                view=view,
                ephemeral=True,
            )
            await view.wait()
            if not view.value:
                return

        game, player = self.get_game(interaction)
        if not game or not player:
            await interaction.followup.send("You do not have an ongoing game.", ephemeral=True)
            return
        if player.locked:
            await interaction.followup.send(
                "You have locked your team, it cannot be edited! "
                "You can click the cancel button to stop the game instead.",
                ephemeral=True,
            )
            return
        
        for pos_players in player.team.values():
            if countryball in pos_players:
                await interaction.followup.send(
                    f"You already have this {settings.collectible_name} in your team.",
                    ephemeral=True,
                )
                return
                
        if await countryball.is_locked():
            await interaction.followup.send(
                f"This {settings.collectible_name} is currently in an active trade or donation, "
                "please try again later.",
                ephemeral=True,
            )
            return
        
        position_caps = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
        current_count = len(player.team[position])
        max_capacity = position_caps[position]
        
        if current_count >= max_capacity:
            await interaction.followup.send(
                f"Your {position} is full! Maximum capacity: {max_capacity}. "
                f"Current: {current_count}/{max_capacity}",
                ephemeral=True
            )
            return

        await countryball.lock_for_trade()
        player.team[position].append(countryball)
        await interaction.followup.send(
            f"{countryball.countryball.country} added to {position} ({current_count + 1}/{max_capacity}).", 
            ephemeral=True
        )

    @add.autocomplete("position")
    async def position_autocomplete(
        self, interaction: discord.Interaction["BallsDexBot"], current: str
    ) -> list[app_commands.Choice[str]]:
        positions = ["GK", "DF", "MF", "FW"]
        return [
            app_commands.Choice(name=pos, value=pos)
            for pos in positions
            if current.upper() in pos
        ]

    @app_commands.command(extras={"game": TradeCommandType.REMOVE})
    async def remove(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Remove a player from your team.

        Parameters
        ----------
        countryball: BallInstance
            The player you want to remove from your team
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not countryball:
            return

        game, player = self.get_game(interaction)
        if not game or not player:
            await interaction.response.send_message(
                "You do not have an ongoing game.", ephemeral=True
            )
            return
        if player.locked:
            await interaction.response.send_message(
                "You have locked your team, it cannot be edited! "
                "You can click the cancel button to stop the game instead.",
                ephemeral=True,
            )
            return
        
        found = False
        for position, pos_players in player.team.items():
            if countryball in pos_players:
                pos_players.remove(countryball)
                await countryball.unlock()
                found = True
                break
        
        if not found:
            await interaction.response.send_message(
                f"That {settings.collectible_name} is not in your team.", ephemeral=True
            )
            return
        
        if countryball in player.bets:
            player.bets.remove(countryball)
            
        await interaction.response.send_message(
            f"{countryball.countryball.country} removed from team.", ephemeral=True
        )

    @app_commands.command(extras={"game": TradeCommandType.PICK})
    async def bet(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Bet a ball from your inventory - winner takes all betted balls!

        Parameters
        ----------
        countryball: BallInstance
            The ball you want to bet
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not countryball:
            return

        game, player = self.get_game(interaction)
        if not game or not player:
            await interaction.response.send_message(
                "You do not have an ongoing game.", ephemeral=True
            )
            return
        if player.locked:
            await interaction.response.send_message(
                "You have locked your team, it cannot be edited! "
                "You can click the cancel button to stop the game instead.",
                ephemeral=True,
            )
            return
        
        if not countryball.is_tradeable:
            await interaction.response.send_message(
                f"You cannot bet this {settings.collectible_name}.", ephemeral=True
            )
            return
        
        if countryball in player.bets:
            await interaction.response.send_message(
                f"You have already bet this {settings.collectible_name}!",
                ephemeral=True,
            )
            return
        
        in_team = any(countryball in pos_list for pos_list in player.team.values())
        is_locked = await countryball.is_locked()
        
        if is_locked and not in_team:
            await interaction.response.send_message(
                f"This {settings.collectible_name} is currently locked in another trade. "
                "Please try again later.",
                ephemeral=True,
            )
            return
        
        if not in_team and not is_locked:
            await countryball.lock_for_trade()
        
        player.bets.append(countryball)
        await interaction.response.send_message(
            f"✅ {countryball.countryball.country} added to your bet! Winner takes all betted balls!",
            ephemeral=True,
        )

    @app_commands.command()
    async def cancel(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        Cancel your current game.
        """
        game, player = self.get_game(interaction)
        if not game or not player:
            await interaction.response.send_message(
                "You do not have an ongoing game.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        
        view = ConfirmChoiceView(
            interaction,
            accept_message="Cancelling the game...",
            cancel_message="This request has been cancelled.",
        )
        await interaction.followup.send(
            "Are you sure you want to cancel this game?", view=view, ephemeral=True
        )
        await view.wait()
        if not view.value:
            return

        await game.user_cancel(player)
        await interaction.followup.send("Game has been cancelled.", ephemeral=True)


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(FootballGame(bot))
