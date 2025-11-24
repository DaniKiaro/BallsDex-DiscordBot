from __future__ import annotations

import asyncio
import logging
import random
import secrets
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

import discord
from discord.ui import Button, View, button
from discord.utils import format_dt, utcnow

from ballsdex.core.models import BallInstance, Player
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.packages.footballgame.display import fill_game_embed_fields
from ballsdex.packages.footballgame.game_user import GameUser
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot
    from ballsdex.packages.footballgame.cog import FootballGame as FootballGameCog

log = logging.getLogger("ballsdex.packages.footballgame.menu")


class GameView(View):
    def __init__(self, game: GameMenu):
        super().__init__(timeout=60 * 30)
        self.game = game

    async def interaction_check(self, interaction: discord.Interaction["BallsDexBot"], /) -> bool:
        try:
            self.game._get_player(interaction.user)
        except RuntimeError:
            await interaction.response.send_message(
                "You are not allowed to interact with this game.", ephemeral=True
            )
            return False
        else:
            return True

    @button(label="Lock team", emoji="\N{WHITE HEAVY CHECK MARK}", style=discord.ButtonStyle.primary)
    async def lock(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        player = self.game._get_player(interaction.user)
        if player.locked:
            await interaction.response.send_message(
                "You have already locked your team!", ephemeral=True
            )
            return
        
        if not player.has_minimum_team():
            await interaction.response.send_message(
                "You need at least one player in each position (GK, DF, MF, FW) before locking!",
                ephemeral=True
            )
            return
            
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self.game.lock(player)
        if self.game.player1.locked and self.game.player2.locked:
            await interaction.followup.send(
                "Your team has been locked. The game will start automatically!",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Your team has been locked. Waiting for the other player to lock their team...",
                ephemeral=True,
            )

    @button(label="Reset", emoji="\N{DASH SYMBOL}", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
        player = self.game._get_player(interaction.user)
        await interaction.response.defer(thinking=True, ephemeral=True)

        if player.locked:
            await interaction.followup.send(
                "You have locked your team, it cannot be edited! "
                "You can click the cancel button to stop the game instead.",
                ephemeral=True,
            )
            return

        view = ConfirmChoiceView(
            interaction,
            accept_message="Clearing your team...",
            cancel_message="This request has been cancelled.",
        )
        await interaction.followup.send(
            "Are you sure you want to clear your team?", view=view, ephemeral=True
        )
        await view.wait()
        if not view.value:
            return

        if player.locked:
            await interaction.followup.send(
                "You have locked your team, it cannot be edited!",
                ephemeral=True,
            )
            return

        for position_players in player.team.values():
            for ball in position_players:
                await ball.unlock()
            position_players.clear()
        
        player.bets.clear()

        await interaction.followup.send("Team cleared.", ephemeral=True)

    @button(
        label="Cancel game",
        emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
        style=discord.ButtonStyle.danger,
    )
    async def cancel(self, interaction: discord.Interaction["BallsDexBot"], button: Button):
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

        await self.game.user_cancel(self.game._get_player(interaction.user))
        await interaction.followup.send("Game has been cancelled.", ephemeral=True)


class GameMenu:
    def __init__(
        self,
        cog: FootballGameCog,
        interaction: discord.Interaction["BallsDexBot"],
        player1: GameUser,
        player2: GameUser,
    ):
        self.cog = cog
        self.bot = interaction.client
        self.channel: discord.TextChannel = cast(discord.TextChannel, interaction.channel)
        self.player1 = player1
        self.player2 = player2
        self.embed = discord.Embed()
        self.task: asyncio.Task | None = None
        self.current_view: GameView = GameView(self)
        self.message: discord.Message
        self.match_events: list[str] = []

    def _get_player(self, user: discord.User | discord.Member) -> GameUser:
        if user.id == self.player1.user.id:
            return self.player1
        elif user.id == self.player2.user.id:
            return self.player2
        raise RuntimeError(f"User with ID {user.id} cannot be found in the game")

    def _generate_embed(self):
        add_command = self.cog.add.extras.get("mention", "`/game add`")
        remove_command = self.cog.remove.extras.get("mention", "`/game remove`")
        bet_command = self.cog.bet.extras.get("mention", "`/game bet`")

        self.embed.title = "⚽ ChampFut Game"
        self.embed.color = discord.Colour.green()
        self.embed.description = (
            f"Build your team using {add_command} and {remove_command}.\n"
            "**You need at least one player in each position: GK, DF, MF, FW**\n"
            f"**Optionally use {bet_command} to wager balls from your inventory!**\n"
            "Once ready, click the lock button to confirm your team.\n\n"
            "**When both players lock, the match will simulate automatically (~1 minute).**\n"
            "**The stronger team has a 55% chance to win important events!**\n"
            "**Winner takes ONLY the betted balls from both players!**\n\n"
            f"*This game will timeout {format_dt(utcnow() + timedelta(minutes=30), style='R')}.*"
        )
        self.embed.set_footer(
            text="This message is updated every 15 seconds."
        )

    async def update_message_loop(self):
        """Update the message every 15 seconds."""
        assert self.task
        start_time = datetime.utcnow()

        while True:
            await asyncio.sleep(15)
            if datetime.utcnow() - start_time > timedelta(minutes=30):
                self.embed.colour = discord.Colour.dark_red()
                await self.cancel("The game timed out")
                return

            try:
                fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
                await self.message.edit(embed=self.embed)
            except Exception:
                log.exception(
                    "Failed to refresh the game menu "
                    f"guild={self.message.guild.id} "
                    f"player1={self.player1.user.id} player2={self.player2.user.id}"
                )
                self.embed.colour = discord.Colour.dark_red()
                await self.cancel("The game timed out")
                return

    async def start(self):
        """Start the game by sending the initial message."""
        self._generate_embed()
        fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
        self.message = await self.channel.send(
            content=f"Hey {self.player2.user.mention}, {self.player1.user.name} "
            "is challenging you to a football match!",
            embed=self.embed,
            view=self.current_view,
            allowed_mentions=discord.AllowedMentions(users=self.player2.player.can_be_mentioned),
        )
        self.task = self.bot.loop.create_task(self.update_message_loop())

    async def cancel(self, reason: str = "The game has been cancelled."):
        """Cancel the game immediately."""
        if self.task:
            self.task.cancel()

        for position_players in list(self.player1.team.values()) + list(self.player2.team.values()):
            for ball in position_players:
                await ball.unlock()

        self.current_view.stop()
        for item in self.current_view.children:
            item.disabled = True

        fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
        self.embed.description = f"**{reason}**"
        if getattr(self, "message", None):
            await self.message.edit(embed=self.embed, view=self.current_view)

    async def user_cancel(self, player: GameUser):
        """Mark a player as having cancelled the game."""
        player.cancelled = True
        await self.cancel(f"{player.user.name} cancelled the game.")

    async def lock(self, player: GameUser):
        """Lock a player's team and check if both are ready to execute the game."""
        player.locked = True

        if self.player1.locked and self.player2.locked:
            await self.execute_game()

    def _generate_match_event(
        self, minute: int, player_name: str, ball: BallInstance, event_type: str
    ) -> str:
        """Generate a realistic match event description."""
        player_country = ball.countryball.country
        
        events = {
            "goal": f"{minute}' | GOAL by {player_country} - {player_name}",
            "save": f"{minute}' | SAVE by {player_country} - {player_name}",
            "offside": f"{minute}' | OFFSIDE GOAL by {player_country} - {player_name}",
            "defense": f"{minute}' | CRUCIAL DEFENSE by {player_country} - {player_name}",
            "foul": f"{minute}' | FOUL by {player_country} - {player_name}",
        }
        
        return events.get(event_type, f"{minute}' | Event")

    async def execute_game(self):
        """Execute the football match simulation."""
        if self.task:
            self.task.cancel()

        self.embed.title = "⚽ ChampFut Game"
        self.embed.color = discord.Color.blue()
        self.embed.description = "**The match is starting!**\n\nMatch events will appear below..."
        
        fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
        await self.message.edit(embed=self.embed, view=self.current_view)

        strength1 = self.player1.get_team_strength()
        strength2 = self.player2.get_team_strength()
        
        if strength1 > strength2:
            stronger_player = self.player1
            weaker_player = self.player2
            stronger_chance = 0.55
        elif strength2 > strength1:
            stronger_player = self.player2
            weaker_player = self.player1
            stronger_chance = 0.55
        else:
            stronger_player = self.player1
            weaker_player = self.player2
            stronger_chance = 0.50

        score = {self.player1.user.name: 0, self.player2.user.name: 0}
        self.match_events = []

        event_types = ["goal", "save", "offside", "defense", "foul"]
        
        for i in range(10):
            minute = (i + 1) * 10
            await asyncio.sleep(6)

            winner_player = stronger_player if random.random() < stronger_chance else weaker_player
            loser_player = weaker_player if winner_player == stronger_player else stronger_player
            
            event_type = random.choice(event_types)
            
            if event_type == "goal":
                all_forwards = winner_player.team["FW"]
                if all_forwards:
                    scorer = random.choice(all_forwards)
                    event = self._generate_match_event(
                        minute, winner_player.user.name, scorer, "goal"
                    )
                    score[winner_player.user.name] += 1
                    self.match_events.append(event)
            elif event_type == "save":
                all_gks = winner_player.team["GK"]
                if all_gks:
                    saver = random.choice(all_gks)
                    event = self._generate_match_event(
                        minute, winner_player.user.name, saver, "save"
                    )
                    self.match_events.append(event)
            elif event_type == "offside":
                all_forwards = loser_player.team["FW"]
                if all_forwards:
                    offsider = random.choice(all_forwards)
                    event = self._generate_match_event(
                        minute, loser_player.user.name, offsider, "offside"
                    )
                    self.match_events.append(event)
            elif event_type == "defense":
                all_defenders = winner_player.team["DF"]
                if all_defenders:
                    defender = random.choice(all_defenders)
                    event = self._generate_match_event(
                        minute, winner_player.user.name, defender, "defense"
                    )
                    self.match_events.append(event)
            elif event_type == "foul":
                all_mids = loser_player.team["MF"]
                if all_mids:
                    fouler = random.choice(all_mids)
                    event = self._generate_match_event(
                        minute, loser_player.user.name, fouler, "foul"
                    )
                    self.match_events.append(event)

            events_text = "\n".join(self.match_events[-10:])
            score_line = f"**{self.player1.user.name} | {score[self.player1.user.name]} - {score[self.player2.user.name]} | {self.player2.user.name}**"
            
            self.embed.description = f"**Match in Progress...**\n\n{score_line}"
            fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
            
            if len(events_text) > 0:
                self.embed.add_field(
                    name="📋 Match Events",
                    value=f"```\n{events_text}\n```",
                    inline=False
                )
            
            await self.message.edit(embed=self.embed)

        await asyncio.sleep(2)

        if score[self.player1.user.name] > score[self.player2.user.name]:
            winner = self.player1
            loser = self.player2
        elif score[self.player2.user.name] > score[self.player1.user.name]:
            winner = self.player2
            loser = self.player1
        else:
            self.match_events.append("90' | DRAW! Going to penalties!")
            
            events_text = "\n".join(self.match_events[-10:])
            score_line = f"**{self.player1.user.name} | {score[self.player1.user.name]} - {score[self.player2.user.name]} | {self.player2.user.name}**"
            
            self.embed.description = f"**Match tied! Penalty Shootout starting...**\n\n{score_line}"
            fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
            
            if len(events_text) > 0:
                self.embed.add_field(
                    name="📋 Match Events",
                    value=f"```\n{events_text}\n```",
                    inline=False
                )
            
            await self.message.edit(embed=self.embed)
            await asyncio.sleep(3)
            
            penalty_score = {self.player1.user.name: 0, self.player2.user.name: 0}
            
            for penalty_round in range(1, 4):
                await asyncio.sleep(4)
                
                for shooter_player in [self.player1, self.player2]:
                    other_player = self.player2 if shooter_player == self.player1 else self.player1
                    
                    forwards = shooter_player.team.get("FW", [])
                    goalkeepers = other_player.team.get("GK", [])
                    
                    if forwards and goalkeepers:
                        shooter = random.choice(forwards)
                        gk = random.choice(goalkeepers)
                        
                        shooter_strength = shooter.attack + shooter.health
                        gk_strength = gk.attack + gk.health
                        
                        if shooter_strength > gk_strength:
                            success_chance = 0.70
                        elif gk_strength > shooter_strength:
                            success_chance = 0.50
                        else:
                            success_chance = 0.60
                        
                        if random.random() < success_chance:
                            penalty_score[shooter_player.user.name] += 1
                            self.match_events.append(
                                f"Penalty {penalty_round} | GOAL by {shooter.countryball.country} - {shooter_player.user.name}"
                            )
                        else:
                            self.match_events.append(
                                f"Penalty {penalty_round} | SAVED by {gk.countryball.country} - {other_player.user.name}"
                            )
                
                events_text = "\n".join(self.match_events[-10:])
                score_line = f"**Penalties: {self.player1.user.name} {penalty_score[self.player1.user.name]} - {penalty_score[self.player2.user.name]} {self.player2.user.name}**"
                
                self.embed.description = f"**Penalty Shootout...**\n\n{score_line}"
                fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
                
                if len(events_text) > 0:
                    self.embed.add_field(
                        name="📋 Match Events",
                        value=f"```\n{events_text}\n```",
                        inline=False
                    )
                
                await self.message.edit(embed=self.embed)
            
            if penalty_score[self.player1.user.name] > penalty_score[self.player2.user.name]:
                winner = self.player1
                loser = self.player2
            elif penalty_score[self.player2.user.name] > penalty_score[self.player1.user.name]:
                winner = self.player2
                loser = self.player1
            else:
                winner = secrets.choice([self.player1, self.player2])
                loser = self.player2 if winner == self.player1 else self.player1
                self.match_events.append(
                    f"Sudden Death | GOAL by {winner.user.name}!"
                )
            
            score[self.player1.user.name] = f"{score[self.player1.user.name]} ({penalty_score[self.player1.user.name]})"
            score[self.player2.user.name] = f"{score[self.player2.user.name]} ({penalty_score[self.player2.user.name]})"

        winner.won = True

        all_team_balls = winner.get_all_players() + loser.get_all_players()
        
        valid_player1_bets = [b for b in self.player1.bets if b in all_team_balls]
        valid_player2_bets = [b for b in self.player2.bets if b in all_team_balls]
        betted_balls = valid_player1_bets + valid_player2_bets

        try:
            for ball in betted_balls:
                ball.player = winner.player
                await ball.save()
                await ball.unlock()
            
            for ball in all_team_balls:
                if ball not in betted_balls:
                    await ball.unlock()

            for position in winner.team.values():
                position.clear()
            for position in loser.team.values():
                position.clear()
            winner.bets.clear()
            loser.bets.clear()

            final_score = f"{self.player1.user.name} | {score[self.player1.user.name]} - {score[self.player2.user.name]} | {self.player2.user.name}"
            events_text = "\n".join(self.match_events)
            
            self.embed.title = "🎉 Match Complete! 🎉"
            self.embed.color = discord.Color.gold()
            
            if len(betted_balls) > 0:
                result_message = (
                    f"**{len(betted_balls)} betted {settings.plural_collectible_name} "
                    f"have been transferred to {winner.user.name}!**"
                )
            else:
                result_message = "**No balls were betted. This was a friendly match!**"
            
            self.embed.description = (
                f"**{final_score}**\n"
                f"Winner: {winner.user.name}\n\n"
                f"{result_message}"
            )
            
            if len(events_text) > 0:
                self.embed.add_field(
                    name="📋 Full Match Events",
                    value=f"```\n{events_text}\n```",
                    inline=False
                )

            self.current_view.stop()
            for item in self.current_view.children:
                item.disabled = True

            fill_game_embed_fields(self.embed, self.bot, self.player1, self.player2)
            await self.message.edit(embed=self.embed, view=self.current_view)

            await self.channel.send(
                f"🎉 **Match Result:** {winner.user.mention} defeats {loser.user.mention} "
                f"({score[winner.user.name]}-{score[loser.user.name]}) "
                f"and wins {len(betted_balls)} betted {settings.plural_collectible_name}! 🎉"
            )

        except Exception as e:
            log.exception(f"Failed to execute game: {e}")
            await self.cancel("An error occurred while executing the game.")
