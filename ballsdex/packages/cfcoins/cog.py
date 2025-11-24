import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
import random
from discord import Embed, Color
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

from ballsdex.core.models import (
    Ball,
    BallInstance,
    Player,
)
from ballsdex.core.bot import BallsDexBot
from ballsdex.core.utils.transformers import (
    BallInstanceTransform,
    SpecialEnabledTransform,
)

LOG_CHANNEL_ID = 1375845805793218591
ADMIN_IDS = {1327148447673094255, 730411104450248766}

PACK_TYPES = {
    "normal": {
        "name": "Normal Pack",
        "price": 250,
        "emoji": "<:normalpack:1441903613055340808>",
        "color": Color.blue(),
        "min_rarity": 15.0,
        "max_rarity": 30.0,
    },
    "epic": {
        "name": "Epic Pack",
        "price": 500,
        "emoji": "<:epicpack:1441903555379200223>",
        "color": Color.purple(),
        "min_rarity": 1.0,
        "max_rarity": 5.0,
    },
    "mythic": {
        "name": "Mythic Pack",
        "price": 1500,
        "emoji": "<:mythicpack:1441903998897750076>",
        "color": Color.gold(),
        "min_rarity": 0.1,
        "max_rarity": 1.0,
    },
    "legendary": {
        "name": "Legendary Pack",
        "price": 3000,
        "emoji": "<:legendarypack:1441903650086715552>",
        "color": Color.from_rgb(255, 215, 0),
        "min_rarity": 0.01,
        "max_rarity": 0.1,
    },
}


class BallSelectDropdown(discord.ui.Select):
    def __init__(self, balls: list, page: int = 0):
        self.balls = balls
        self.page = page
        self.balls_per_page = 25
        
        start_idx = page * self.balls_per_page
        end_idx = min(start_idx + self.balls_per_page, len(balls))
        page_balls = balls[start_idx:end_idx]
        
        options = []
        for ball in page_balls:
            ball_id = f"#{ball.pk:0X}"
            ball_name = ball.ball.country
            rarity = ball.ball.rarity
            label = f"{ball_name} {ball_id}"
            description = f"Rarity: {rarity}"
            if ball.favorite:
                description += " | ⭐ Favorited"
            
            options.append(discord.SelectOption(
                label=label[:100],
                description=description[:100],
                value=str(ball.pk)
            ))
        
        super().__init__(
            placeholder=f"Select balls to sell (Page {page + 1})",
            min_values=0,
            max_values=len(options),
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()


class BulkSellView(discord.ui.View):
    def __init__(self, cog, user: discord.User, balls: list, user_id: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.user = user
        self.all_balls = [b for b in balls if not b.favorite]
        self.user_id = user_id
        self.selected_balls = []
        self.cached_values = {}
        self.current_page = 0
        self.total_pages = (len(self.all_balls) + 24) // 25
        
        self.dropdown = BallSelectDropdown(self.all_balls, self.current_page)
        self.add_item(self.dropdown)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ This is not your bulk sell menu!",
                ephemeral=True
            )
            return False
        return True
    
    async def update_embed(self, interaction: discord.Interaction):
        selected_text = "None"
        if self.selected_balls:
            selected_text = "\n".join([
                f"• {b.ball.country} #{b.pk:0X} (Rarity: {b.ball.rarity})"
                for b in self.selected_balls[:10]
            ])
            if len(self.selected_balls) > 10:
                selected_text += f"\n... and {len(self.selected_balls) - 10} more"
        
        total_value = sum(
            self.cached_values.get(b.pk, 0)
            for b in self.selected_balls
        )
        
        embed = Embed(
            title="💰 Bulk Sell",
            description="Select the balls you want to sell.",
            color=Color.blue()
        )
        embed.add_field(
            name="📊 Selected", 
            value=f"{len(self.selected_balls)} ball(s)",
            inline=True
        )
        embed.add_field(
            name="💰 Total Value",
            value=f"{total_value} CF coins",
            inline=True
        )
        embed.add_field(
            name="📋 Selected Balls",
            value=selected_text,
            inline=False
        )
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages}")
        
        await interaction.edit_original_response(embed=embed, view=self)
    
    @discord.ui.button(label="Quit", style=discord.ButtonStyle.danger, row=1)
    async def quit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()
    
    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, row=1)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_balls:
            await interaction.response.send_message(
                "❌ No balls selected to sell!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        total_coins = 0
        sold_balls = []
        
        for ball in self.selected_balls:
            await ball.fetch_related("ball")
            coins = self.cached_values.get(ball.pk, 0)
            total_coins += coins
            sold_balls.append(f"{ball.ball.country} #{ball.pk:0X}")
            await ball.delete()
        
        self.cog.bot.cf_wallet[self.user_id] += total_coins
        
        embed = Embed(
            title="✅ Bulk Sell Complete!",
            description=f"You sold **{len(sold_balls)} balls** for **{total_coins} CF coins**!",
            color=Color.green()
        )
        embed.add_field(
            name="💳 New Balance",
            value=f"{self.cog.bot.cf_wallet[self.user_id]} CF coins",
            inline=False
        )
        
        await interaction.edit_original_response(embed=embed, view=None)
        
        logger.info(
            f"[CF COINS BULK SELL] {self.user} ({self.user.id}) "
            f"sold {len(sold_balls)} balls for {total_coins} CF coins"
        )
        
        await self.cog.log_action(
            title="💰 Bulk Sell",
            description=f"{self.user.mention} sold multiple balls",
            color=Color.green(),
            fields=[
                {"name": "User", "value": f"{self.user.name} ({self.user.id})", "inline": True},
                {"name": "Balls Sold", "value": f"{len(sold_balls)}", "inline": True},
                {"name": "Total Coins Earned", "value": f"{total_coins} CF coins", "inline": True},
                {"name": "New Balance", "value": f"{self.cog.bot.cf_wallet[self.user_id]} CF coins", "inline": True},
                {"name": "Balls", "value": ", ".join(sold_balls[:10]) + (f"... and {len(sold_balls) - 10} more" if len(sold_balls) > 10 else ""), "inline": False}
            ]
        )
        
        self.stop()
    
    @discord.ui.button(label="Clear", style=discord.ButtonStyle.secondary, row=2)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_balls = []
        await self.update_embed(interaction)
    
    @discord.ui.button(label="Select Page", style=discord.ButtonStyle.primary, row=2)
    async def select_page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        selected_values = self.dropdown.values
        
        start_idx = self.current_page * 25
        end_idx = min(start_idx + 25, len(self.all_balls))
        page_balls = self.all_balls[start_idx:end_idx]
        
        for value in selected_values:
            ball_pk = int(value)
            ball = next((b for b in page_balls if b.pk == ball_pk), None)
            if ball and ball not in self.selected_balls:
                self.selected_balls.append(ball)
                if ball.pk not in self.cached_values:
                    self.cached_values[ball.pk] = self.cog.calculate_sell_value(ball.ball.rarity)
        
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.remove_item(self.dropdown)
            self.dropdown = BallSelectDropdown(self.all_balls, self.current_page)
            self.add_item(self.dropdown)
        
        await self.update_embed(interaction)


class CFCoins(commands.GroupCog, name="cfcoins"):
    """
    CF Coins economy system for BallsDex
    """

    def __init__(self, bot: BallsDexBot):
        self.bot = bot
        if not hasattr(bot, 'cf_wallet'):
            bot.cf_wallet = defaultdict(int)
        if not hasattr(bot, 'cf_packs'):
            bot.cf_packs = defaultdict(lambda: {"normal": 0, "epic": 0, "mythic": 0, "legendary": 0})
        super().__init__()

    def calculate_sell_value(self, rarity: float) -> int:
        if rarity >= 20.0:
            return 7
        elif rarity >= 10.0:
            return 15
        elif rarity >= 5.0:
            return 30
        elif rarity >= 2.0:
            return 60
        elif rarity >= 1.0:
            return 115
        elif rarity >= 0.5:
            return 225
        elif rarity >= 0.1:
            return 450
        elif rarity >= 0.01:
            return 800
        else:
            return 1300

    async def get_random_ball_in_range(
        self, min_rarity: float, max_rarity: float
    ) -> Ball | None:
        all_balls = await Ball.filter(
            rarity__gte=min_rarity, rarity__lte=max_rarity, enabled=True
        ).all()

        if not all_balls:
            return None

        weighted_choices = []
        for ball in all_balls:
            weight = 1.0 / ball.rarity if ball.rarity > 0 else 100
            weighted_choices.append((ball, weight))

        total_weight = sum(w for _, w in weighted_choices)
        random_value = random.uniform(0, total_weight)

        cumulative = 0
        for ball, weight in weighted_choices:
            cumulative += weight
            if random_value <= cumulative:
                return ball

        return weighted_choices[-1][0] if weighted_choices else None

    async def log_action(self, title: str, description: str, color: Color, fields: list = None):
        try:
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if not log_channel:
                logger.warning(f"Log channel {LOG_CHANNEL_ID} not found")
                return
            
            embed = Embed(
                title=title,
                description=description,
                color=color,
                timestamp=datetime.now(timezone.utc)
            )
            
            if fields:
                for field in fields:
                    embed.add_field(
                        name=field.get("name", ""),
                        value=field.get("value", ""),
                        inline=field.get("inline", False)
                    )
            
            await log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to log action: {e}")

    @app_commands.command(name="daily", description="Claim your daily CF coins!")
    @app_commands.checks.cooldown(1, 86400, key=lambda i: i.user.id)
    async def daily(self, interaction: discord.Interaction[BallsDexBot]):
        user_id = str(interaction.user.id)

        min_creation = datetime.now(timezone.utc) - timedelta(days=14)
        if interaction.user.created_at > min_creation:
            await interaction.response.send_message(
                "Your account must be at least 14 days old to use this command.",
                ephemeral=True,
            )
            return

        coins = random.randint(30, 60)
        self.bot.cf_wallet[user_id] += coins

        embed = Embed(
            title="💰 Daily CF Coins Claimed!",
            description=f"You received **{coins} CF coins**!",
            color=Color.green(),
        )
        embed.add_field(
            name="💳 Your Balance", value=f"{self.bot.cf_wallet[user_id]} CF coins", inline=False
        )
        embed.set_footer(text="Come back in 24 hours for your next daily reward!")
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.response.send_message(embed=embed)

        logger.info(
            f"[CF COINS DAILY] {interaction.user} ({interaction.user.id}) "
            f"claimed {coins} CF coins. New balance: {self.bot.cf_wallet[user_id]}"
        )
        
        await self.log_action(
            title="💰 Daily Claim",
            description=f"{interaction.user.mention} claimed their daily reward",
            color=Color.green(),
            fields=[
                {"name": "User", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Coins Claimed", "value": f"{coins} CF coins", "inline": True},
                {"name": "New Balance", "value": f"{self.bot.cf_wallet[user_id]} CF coins", "inline": True}
            ]
        )

    @app_commands.command(name="weekly", description="Claim your weekly CF coins!")
    @app_commands.checks.cooldown(1, 604800, key=lambda i: i.user.id)
    async def weekly(self, interaction: discord.Interaction[BallsDexBot]):
        user_id = str(interaction.user.id)

        min_creation = datetime.now(timezone.utc) - timedelta(days=14)
        if interaction.user.created_at > min_creation:
            await interaction.response.send_message(
                "Your account must be at least 14 days old to use this command.",
                ephemeral=True,
            )
            return

        coins = random.randint(150, 200)
        self.bot.cf_wallet[user_id] += coins

        embed = Embed(
            title="🎉 Weekly CF Coins Claimed!",
            description=f"You received **{coins} CF coins**!",
            color=Color.from_rgb(255, 215, 0),
        )
        embed.add_field(
            name="💳 Your Balance", value=f"{self.bot.cf_wallet[user_id]} CF coins", inline=False
        )
        embed.set_footer(text="Come back in 7 days for your next weekly reward!")
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.response.send_message(embed=embed)

        logger.info(
            f"[CF COINS WEEKLY] {interaction.user} ({interaction.user.id}) "
            f"claimed {coins} CF coins. New balance: {self.bot.cf_wallet[user_id]}"
        )
        
        await self.log_action(
            title="🎉 Weekly Claim",
            description=f"{interaction.user.mention} claimed their weekly reward",
            color=Color.from_rgb(255, 215, 0),
            fields=[
                {"name": "User", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Coins Claimed", "value": f"{coins} CF coins", "inline": True},
                {"name": "New Balance", "value": f"{self.bot.cf_wallet[user_id]} CF coins", "inline": True}
            ]
        )

    @app_commands.command(name="sell", description="Sell a ball for CF coins!")
    @app_commands.describe(ball="The ball you want to sell")
    async def sell(
        self,
        interaction: discord.Interaction[BallsDexBot],
        ball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ):
        user_id = str(interaction.user.id)

        if not ball:
            await interaction.response.send_message(
                "Ball not found in your collection.", ephemeral=True
            )
            return

        player, _ = await Player.get_or_create(discord_id=user_id)
        ball_instance = await BallInstance.filter(pk=ball.pk, player=player).prefetch_related("ball").first()
        
        if not ball_instance:
            await interaction.response.send_message(
                "❌ This ball is not in your collection!",
                ephemeral=True
            )
            return

        if ball_instance.favorite:
            await interaction.response.send_message(
                "❌ You cannot sell a favorited ball! Remove it from favorites first.",
                ephemeral=True,
            )
            return

        rarity = ball_instance.ball.rarity
        coins = self.calculate_sell_value(rarity)

        ball_name = ball_instance.ball.country
        ball_id = f"#{ball_instance.pk:0X}"

        try:
            await ball_instance.delete()
        except Exception as e:
            logger.error(
                f"[CF COINS SELL ERROR] Failed to delete ball {ball_id} for user {interaction.user.id}: {e}"
            )
            await interaction.response.send_message(
                "❌ An error occurred while trying to sell this ball. Please try again later.",
                ephemeral=True
            )
            return

        self.bot.cf_wallet[user_id] += coins

        embed = Embed(
            title="💵 Ball Sold!",
            description=f"You sold **{ball_name}** {ball_id} for **{coins} CF coins**!",
            color=Color.green(),
        )
        embed.add_field(name="🎯 Rarity", value=f"{rarity}", inline=True)
        embed.add_field(
            name="💳 New Balance", value=f"{self.bot.cf_wallet[user_id]} CF coins", inline=True
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.response.send_message(embed=embed)

        logger.info(
            f"[CF COINS SELL] {interaction.user} ({interaction.user.id}) "
            f"sold {ball_name} (rarity {rarity}) for {coins} CF coins"
        )
        
        await self.log_action(
            title="💵 Ball Sold",
            description=f"{interaction.user.mention} sold a ball",
            color=Color.green(),
            fields=[
                {"name": "User", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Ball", "value": f"{ball_name} {ball_id}", "inline": True},
                {"name": "Rarity", "value": f"{rarity}", "inline": True},
                {"name": "Coins Earned", "value": f"{coins} CF coins", "inline": True},
                {"name": "New Balance", "value": f"{self.bot.cf_wallet[user_id]} CF coins", "inline": True}
            ]
        )

    @app_commands.command(name="bulksell", description="Sell multiple balls at once for CF coins!")
    async def bulksell(self, interaction: discord.Interaction[BallsDexBot]):
        user_id = str(interaction.user.id)
        
        player, _ = await Player.get_or_create(discord_id=user_id)
        balls = await BallInstance.filter(player=player).prefetch_related("ball")
        
        if not balls:
            await interaction.response.send_message(
                "❌ You don't have any balls to sell!",
                ephemeral=True
            )
            return
        
        non_favorited_balls = [b for b in balls if not b.favorite]
        if not non_favorited_balls:
            await interaction.response.send_message(
                "❌ You don't have any non-favorited balls to sell! All your balls are favorited.",
                ephemeral=True
            )
            return
        
        view = BulkSellView(self, interaction.user, balls, user_id)
        embed = Embed(
            title="💰 Bulk Sell",
            description="Select the balls you want to sell.",
            color=Color.blue()
        )
        embed.add_field(
            name="📊 Selected", 
            value=f"{len(view.selected_balls)} ball(s)",
            inline=True
        )
        embed.set_footer(text="Select balls from the dropdown menu below")
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="wallet", description="Check your CF coins and packs!")
    async def wallet(self, interaction: discord.Interaction[BallsDexBot]):
        user_id = str(interaction.user.id)

        coins = self.bot.cf_wallet[user_id]
        packs = self.bot.cf_packs[user_id]

        embed = Embed(
            title="💼 Your CF Wallet",
            description=f"**💰 CF Coins:** {coins}",
            color=Color.blue(),
        )

        pack_text = ""
        total_packs = 0
        for pack_type, pack_info in PACK_TYPES.items():
            count = packs[pack_type]
            total_packs += count
            if count > 0:
                pack_text += f"{pack_info['emoji']} **{pack_info['name']}:** {count}\n"

        if not pack_text:
            pack_text = "No packs owned"

        embed.add_field(name="📦 Your Packs", value=pack_text, inline=False)
        embed.set_footer(text=f"Total Packs: {total_packs}")
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shop", description="View the CF coins pack shop!")
    async def shop(self, interaction: discord.Interaction[BallsDexBot]):
        embed = Embed(
            title="🏪 CF Coins Pack Shop",
            description="Purchase packs with your CF coins to get rare balls!",
            color=Color.gold(),
        )

        for pack_type, pack_info in PACK_TYPES.items():
            embed.add_field(
                name=f"{pack_info['emoji']} {pack_info['name']}",
                value=(
                    f"**Price:** {pack_info['price']} CF coins\n"
                    f"**Rarity Range:** {pack_info['min_rarity']} - {pack_info['max_rarity']}\n"
                    f"Use `/cfcoins buy {pack_type}` to purchase!"
                ),
                inline=False,
            )

        embed.set_footer(text="Use /cfcoins buy <pack_type> to purchase a pack!")
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Buy a pack with CF coins!")
    @app_commands.describe(
        pack_type="The type of pack to buy (normal, epic, mythic, legendary)"
    )
    @app_commands.choices(
        pack_type=[
            app_commands.Choice(name="Normal Pack (250 coins)", value="normal"),
            app_commands.Choice(name="Epic Pack (500 coins)", value="epic"),
            app_commands.Choice(name="Mythic Pack (1500 coins)", value="mythic"),
            app_commands.Choice(name="Legendary Pack (3000 coins)", value="legendary"),
        ]
    )
    async def buy(self, interaction: discord.Interaction[BallsDexBot], pack_type: str):
        user_id = str(interaction.user.id)

        if pack_type not in PACK_TYPES:
            await interaction.response.send_message(
                "Invalid pack type! Choose: normal, epic, mythic, or legendary.",
                ephemeral=True,
            )
            return

        pack_info = PACK_TYPES[pack_type]
        price = pack_info["price"]

        if self.bot.cf_wallet[user_id] < price:
            await interaction.response.send_message(
                f"❌ You don't have enough CF coins! You need **{price}** CF coins but only have **{self.bot.cf_wallet[user_id]}**.",
                ephemeral=True,
            )
            return

        self.bot.cf_wallet[user_id] -= price
        self.bot.cf_packs[user_id][pack_type] += 1

        embed = Embed(
            title="🎉 Pack Purchased!",
            description=f"You bought a **{pack_info['name']}** {pack_info['emoji']}!",
            color=pack_info["color"],
        )
        embed.add_field(
            name="💰 Price Paid", value=f"{price} CF coins", inline=True
        )
        embed.add_field(
            name="💳 Remaining Balance",
            value=f"{self.bot.cf_wallet[user_id]} CF coins",
            inline=True,
        )
        embed.add_field(
            name="📦 Total Packs of This Type",
            value=f"{self.bot.cf_packs[user_id][pack_type]}",
            inline=False,
        )
        embed.set_footer(text="Use /cfcoins open to open your pack!")
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await interaction.response.send_message(embed=embed)

        logger.info(
            f"[CF COINS BUY] {interaction.user} ({interaction.user.id}) "
            f"bought {pack_info['name']} for {price} CF coins"
        )
        
        await self.log_action(
            title="🎉 Pack Purchased",
            description=f"{interaction.user.mention} bought a pack",
            color=pack_info["color"],
            fields=[
                {"name": "User", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Pack Type", "value": f"{pack_info['emoji']} {pack_info['name']}", "inline": True},
                {"name": "Price Paid", "value": f"{price} CF coins", "inline": True},
                {"name": "Remaining Balance", "value": f"{self.bot.cf_wallet[user_id]} CF coins", "inline": True},
                {"name": "Total Packs of This Type", "value": f"{self.bot.cf_packs[user_id][pack_type]}", "inline": True}
            ]
        )

    @app_commands.command(name="open", description="Open a pack to get a ball!")
    @app_commands.describe(
        pack_type="The type of pack to open (normal, epic, mythic, legendary)"
    )
    @app_commands.choices(
        pack_type=[
            app_commands.Choice(name="Normal Pack", value="normal"),
            app_commands.Choice(name="Epic Pack", value="epic"),
            app_commands.Choice(name="Mythic Pack", value="mythic"),
            app_commands.Choice(name="Legendary Pack", value="legendary"),
        ]
    )
    async def open(self, interaction: discord.Interaction[BallsDexBot], pack_type: str):
        user_id = str(interaction.user.id)

        if pack_type not in PACK_TYPES:
            await interaction.response.send_message(
                "Invalid pack type! Choose: normal, epic, mythic, or legendary.",
                ephemeral=True,
            )
            return

        if self.bot.cf_packs[user_id][pack_type] < 1:
            await interaction.response.send_message(
                f"❌ You don't have any {PACK_TYPES[pack_type]['name']}s to open!",
                ephemeral=True,
            )
            return

        pack_info = PACK_TYPES[pack_type]

        player, _ = await Player.get_or_create(discord_id=str(interaction.user.id))
        ball = await self.get_random_ball_in_range(
            pack_info["min_rarity"], pack_info["max_rarity"]
        )

        if not ball:
            await interaction.response.send_message(
                "❌ No balls are available in this rarity range. Your pack was not consumed.",
                ephemeral=True,
            )
            return

        self.bot.cf_packs[user_id][pack_type] -= 1

        instance = await BallInstance.create(
            ball=ball,
            player=player,
            attack_bonus=random.randint(-20, 20),
            health_bonus=random.randint(-20, 20),
        )

        walkout_embed = Embed(
            title=f"{pack_info['emoji']} Opening {pack_info['name']}...",
            color=Color.dark_gray(),
        )
        walkout_embed.set_footer(text="CF Coins Pack System")
        await interaction.response.defer()
        msg = await interaction.followup.send(embed=walkout_embed)

        await asyncio.sleep(1.5)
        walkout_embed.description = f"✨ **Rarity:** `{ball.rarity}`"
        await msg.edit(embed=walkout_embed)

        await asyncio.sleep(1.5)
        regime_name = ball.cached_regime.name if ball.cached_regime else "Unknown"
        walkout_embed.description += f"\n💳 **Card:** **{regime_name}**"
        await msg.edit(embed=walkout_embed)

        await asyncio.sleep(1.5)
        walkout_embed.description += (
            f"\n💖 **Health:** `{instance.health}`\n⚽ **Attack:** `{instance.attack}`"
        )
        await msg.edit(embed=walkout_embed)

        await asyncio.sleep(1.5)
        walkout_embed.title = f"🎁 You got **{ball.country}**!"
        walkout_embed.color = pack_info["color"]

        content, file, view = await instance.prepare_for_message(interaction)
        walkout_embed.set_image(url="attachment://" + file.filename)
        walkout_embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )

        await msg.edit(embed=walkout_embed, attachments=[file], view=view)
        file.close()

        logger.info(
            f"[CF COINS OPEN] {interaction.user} ({interaction.user.id}) "
            f"opened {pack_info['name']} and got {ball.country} (rarity {ball.rarity})"
        )
        
        await self.log_action(
            title="🎁 Pack Opened",
            description=f"{interaction.user.mention} opened a pack",
            color=pack_info["color"],
            fields=[
                {"name": "User", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Pack Type", "value": f"{pack_info['emoji']} {pack_info['name']}", "inline": True},
                {"name": "Ball Received", "value": f"{ball.country}", "inline": True},
                {"name": "Rarity", "value": f"{ball.rarity}", "inline": True},
                {"name": "Health", "value": f"{instance.health}", "inline": True},
                {"name": "Attack", "value": f"{instance.attack}", "inline": True}
            ]
        )

    @app_commands.command(name="giftcoins", description="Gift CF coins to another user!")
    @app_commands.describe(user="The user to gift coins to", amount="Amount of CF coins to gift")
    async def giftcoins(self, interaction: discord.Interaction[BallsDexBot], user: discord.User, amount: int):
        sender_id = str(interaction.user.id)
        receiver_id = str(user.id)
        
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ You cannot gift coins to yourself!",
                ephemeral=True
            )
            return
        
        if user.bot:
            await interaction.response.send_message(
                "❌ You cannot gift coins to a bot!",
                ephemeral=True
            )
            return
        
        if amount <= 0:
            await interaction.response.send_message(
                "❌ You must gift at least 1 CF coin!",
                ephemeral=True
            )
            return
        
        if self.bot.cf_wallet[sender_id] < amount:
            await interaction.response.send_message(
                f"❌ You don't have enough CF coins! You have **{self.bot.cf_wallet[sender_id]}** CF coins but tried to gift **{amount}**.",
                ephemeral=True
            )
            return
        
        self.bot.cf_wallet[sender_id] -= amount
        self.bot.cf_wallet[receiver_id] += amount
        
        embed = Embed(
            title="💝 Coins Gifted!",
            description=f"You gifted **{amount} CF coins** to {user.mention}!",
            color=Color.green()
        )
        embed.add_field(name="💳 Your New Balance", value=f"{self.bot.cf_wallet[sender_id]} CF coins", inline=False)
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url
        )
        
        await interaction.response.send_message(embed=embed)
        
        logger.info(
            f"[CF COINS GIFT] {interaction.user} ({interaction.user.id}) "
            f"gifted {amount} CF coins to {user} ({user.id})"
        )
        
        await self.log_action(
            title="💝 Coins Gifted",
            description=f"{interaction.user.mention} gifted coins to {user.mention}",
            color=Color.green(),
            fields=[
                {"name": "Sender", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Receiver", "value": f"{user.name} ({user.id})", "inline": True},
                {"name": "Amount", "value": f"{amount} CF coins", "inline": True},
                {"name": "Sender New Balance", "value": f"{self.bot.cf_wallet[sender_id]} CF coins", "inline": True},
                {"name": "Receiver New Balance", "value": f"{self.bot.cf_wallet[receiver_id]} CF coins", "inline": True}
            ]
        )

    @app_commands.command(name="giftpacks", description="Gift a pack to another user!")
    @app_commands.describe(
        user="The user to gift the pack to",
        pack_type="The type of pack to gift (normal, epic, mythic, legendary)"
    )
    @app_commands.choices(
        pack_type=[
            app_commands.Choice(name="Normal Pack", value="normal"),
            app_commands.Choice(name="Epic Pack", value="epic"),
            app_commands.Choice(name="Mythic Pack", value="mythic"),
            app_commands.Choice(name="Legendary Pack", value="legendary"),
        ]
    )
    async def giftpacks(self, interaction: discord.Interaction[BallsDexBot], user: discord.User, pack_type: str):
        sender_id = str(interaction.user.id)
        receiver_id = str(user.id)
        
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "❌ You cannot gift packs to yourself!",
                ephemeral=True
            )
            return
        
        if user.bot:
            await interaction.response.send_message(
                "❌ You cannot gift packs to a bot!",
                ephemeral=True
            )
            return
        
        if pack_type not in PACK_TYPES:
            await interaction.response.send_message(
                "Invalid pack type! Choose: normal, epic, mythic, or legendary.",
                ephemeral=True
            )
            return
        
        if self.bot.cf_packs[sender_id][pack_type] < 1:
            await interaction.response.send_message(
                f"❌ You don't have any {PACK_TYPES[pack_type]['name']}s to gift!",
                ephemeral=True
            )
            return
        
        pack_info = PACK_TYPES[pack_type]
        
        self.bot.cf_packs[sender_id][pack_type] -= 1
        self.bot.cf_packs[receiver_id][pack_type] += 1
        
        embed = Embed(
            title="🎁 Pack Gifted!",
            description=f"You gifted a **{pack_info['name']}** {pack_info['emoji']} to {user.mention}!",
            color=pack_info["color"]
        )
        embed.add_field(
            name="📦 Your Remaining Packs",
            value=f"{self.bot.cf_packs[sender_id][pack_type]} {pack_info['name']}s",
            inline=False
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url
        )
        
        await interaction.response.send_message(embed=embed)
        
        logger.info(
            f"[CF COINS GIFT PACK] {interaction.user} ({interaction.user.id}) "
            f"gifted {pack_info['name']} to {user} ({user.id})"
        )
        
        await self.log_action(
            title="🎁 Pack Gifted",
            description=f"{interaction.user.mention} gifted a pack to {user.mention}",
            color=pack_info["color"],
            fields=[
                {"name": "Sender", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Receiver", "value": f"{user.name} ({user.id})", "inline": True},
                {"name": "Pack Type", "value": f"{pack_info['emoji']} {pack_info['name']}", "inline": True},
                {"name": "Sender Remaining Packs", "value": f"{self.bot.cf_packs[sender_id][pack_type]}", "inline": True},
                {"name": "Receiver Total Packs", "value": f"{self.bot.cf_packs[receiver_id][pack_type]}", "inline": True}
            ]
        )

    @app_commands.command(name="adminaddcoins", description="[ADMIN] Add CF coins to a user")
    @app_commands.describe(user="The user to add coins to", amount="Amount of CF coins to add")
    async def adminaddcoins(self, interaction: discord.Interaction[BallsDexBot], user: discord.User, amount: int):
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message(
                "❌ You don't have permission to use this command!",
                ephemeral=True
            )
            return
        
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be greater than 0!",
                ephemeral=True
            )
            return
        
        user_id = str(user.id)
        old_balance = self.bot.cf_wallet[user_id]
        self.bot.cf_wallet[user_id] += amount
        new_balance = self.bot.cf_wallet[user_id]
        
        embed = Embed(
            title="✅ Coins Added",
            description=f"Added **{amount} CF coins** to {user.mention}",
            color=Color.green()
        )
        embed.add_field(name="Old Balance", value=f"{old_balance} CF coins", inline=True)
        embed.add_field(name="New Balance", value=f"{new_balance} CF coins", inline=True)
        embed.set_footer(text=f"Admin: {interaction.user.name}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        logger.info(
            f"[CF COINS ADMIN ADD] {interaction.user} ({interaction.user.id}) "
            f"added {amount} CF coins to {user} ({user.id})"
        )
        
        await self.log_action(
            title="🔧 Admin: Coins Added",
            description=f"Admin {interaction.user.mention} added coins to {user.mention}",
            color=Color.orange(),
            fields=[
                {"name": "Admin", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Target User", "value": f"{user.name} ({user.id})", "inline": True},
                {"name": "Amount Added", "value": f"{amount} CF coins", "inline": True},
                {"name": "Old Balance", "value": f"{old_balance} CF coins", "inline": True},
                {"name": "New Balance", "value": f"{new_balance} CF coins", "inline": True}
            ]
        )

    @app_commands.command(name="adminremovecoins", description="[ADMIN] Remove CF coins from a user")
    @app_commands.describe(user="The user to remove coins from", amount="Amount of CF coins to remove")
    async def adminremovecoins(self, interaction: discord.Interaction[BallsDexBot], user: discord.User, amount: int):
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message(
                "❌ You don't have permission to use this command!",
                ephemeral=True
            )
            return
        
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be greater than 0!",
                ephemeral=True
            )
            return
        
        user_id = str(user.id)
        old_balance = self.bot.cf_wallet[user_id]
        self.bot.cf_wallet[user_id] = max(0, self.bot.cf_wallet[user_id] - amount)
        new_balance = self.bot.cf_wallet[user_id]
        
        embed = Embed(
            title="✅ Coins Removed",
            description=f"Removed **{amount} CF coins** from {user.mention}",
            color=Color.red()
        )
        embed.add_field(name="Old Balance", value=f"{old_balance} CF coins", inline=True)
        embed.add_field(name="New Balance", value=f"{new_balance} CF coins", inline=True)
        embed.set_footer(text=f"Admin: {interaction.user.name}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        logger.info(
            f"[CF COINS ADMIN REMOVE] {interaction.user} ({interaction.user.id}) "
            f"removed {amount} CF coins from {user} ({user.id})"
        )
        
        await self.log_action(
            title="🔧 Admin: Coins Removed",
            description=f"Admin {interaction.user.mention} removed coins from {user.mention}",
            color=Color.red(),
            fields=[
                {"name": "Admin", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Target User", "value": f"{user.name} ({user.id})", "inline": True},
                {"name": "Amount Removed", "value": f"{amount} CF coins", "inline": True},
                {"name": "Old Balance", "value": f"{old_balance} CF coins", "inline": True},
                {"name": "New Balance", "value": f"{new_balance} CF coins", "inline": True}
            ]
        )

    @app_commands.command(name="adminaddpacks", description="[ADMIN] Add packs to a user")
    @app_commands.describe(
        user="The user to add packs to",
        pack_type="The type of pack to add",
        amount="Number of packs to add"
    )
    @app_commands.choices(
        pack_type=[
            app_commands.Choice(name="Normal Pack", value="normal"),
            app_commands.Choice(name="Epic Pack", value="epic"),
            app_commands.Choice(name="Mythic Pack", value="mythic"),
            app_commands.Choice(name="Legendary Pack", value="legendary"),
        ]
    )
    async def adminaddpacks(self, interaction: discord.Interaction[BallsDexBot], user: discord.User, pack_type: str, amount: int):
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message(
                "❌ You don't have permission to use this command!",
                ephemeral=True
            )
            return
        
        if pack_type not in PACK_TYPES:
            await interaction.response.send_message(
                "Invalid pack type!",
                ephemeral=True
            )
            return
        
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be greater than 0!",
                ephemeral=True
            )
            return
        
        user_id = str(user.id)
        pack_info = PACK_TYPES[pack_type]
        old_amount = self.bot.cf_packs[user_id][pack_type]
        self.bot.cf_packs[user_id][pack_type] += amount
        new_amount = self.bot.cf_packs[user_id][pack_type]
        
        embed = Embed(
            title="✅ Packs Added",
            description=f"Added **{amount} {pack_info['name']}s** {pack_info['emoji']} to {user.mention}",
            color=pack_info["color"]
        )
        embed.add_field(name="Old Amount", value=f"{old_amount} packs", inline=True)
        embed.add_field(name="New Amount", value=f"{new_amount} packs", inline=True)
        embed.set_footer(text=f"Admin: {interaction.user.name}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        logger.info(
            f"[CF COINS ADMIN ADD PACKS] {interaction.user} ({interaction.user.id}) "
            f"added {amount} {pack_info['name']}s to {user} ({user.id})"
        )
        
        await self.log_action(
            title="🔧 Admin: Packs Added",
            description=f"Admin {interaction.user.mention} added packs to {user.mention}",
            color=Color.orange(),
            fields=[
                {"name": "Admin", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Target User", "value": f"{user.name} ({user.id})", "inline": True},
                {"name": "Pack Type", "value": f"{pack_info['emoji']} {pack_info['name']}", "inline": True},
                {"name": "Amount Added", "value": f"{amount} packs", "inline": True},
                {"name": "Old Amount", "value": f"{old_amount} packs", "inline": True},
                {"name": "New Amount", "value": f"{new_amount} packs", "inline": True}
            ]
        )

    @app_commands.command(name="adminremovepacks", description="[ADMIN] Remove packs from a user")
    @app_commands.describe(
        user="The user to remove packs from",
        pack_type="The type of pack to remove",
        amount="Number of packs to remove"
    )
    @app_commands.choices(
        pack_type=[
            app_commands.Choice(name="Normal Pack", value="normal"),
            app_commands.Choice(name="Epic Pack", value="epic"),
            app_commands.Choice(name="Mythic Pack", value="mythic"),
            app_commands.Choice(name="Legendary Pack", value="legendary"),
        ]
    )
    async def adminremovepacks(self, interaction: discord.Interaction[BallsDexBot], user: discord.User, pack_type: str, amount: int):
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message(
                "❌ You don't have permission to use this command!",
                ephemeral=True
            )
            return
        
        if pack_type not in PACK_TYPES:
            await interaction.response.send_message(
                "Invalid pack type!",
                ephemeral=True
            )
            return
        
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Amount must be greater than 0!",
                ephemeral=True
            )
            return
        
        user_id = str(user.id)
        pack_info = PACK_TYPES[pack_type]
        old_amount = self.bot.cf_packs[user_id][pack_type]
        self.bot.cf_packs[user_id][pack_type] = max(0, self.bot.cf_packs[user_id][pack_type] - amount)
        new_amount = self.bot.cf_packs[user_id][pack_type]
        
        embed = Embed(
            title="✅ Packs Removed",
            description=f"Removed **{amount} {pack_info['name']}s** {pack_info['emoji']} from {user.mention}",
            color=Color.red()
        )
        embed.add_field(name="Old Amount", value=f"{old_amount} packs", inline=True)
        embed.add_field(name="New Amount", value=f"{new_amount} packs", inline=True)
        embed.set_footer(text=f"Admin: {interaction.user.name}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        logger.info(
            f"[CF COINS ADMIN REMOVE PACKS] {interaction.user} ({interaction.user.id}) "
            f"removed {amount} {pack_info['name']}s from {user} ({user.id})"
        )
        
        await self.log_action(
            title="🔧 Admin: Packs Removed",
            description=f"Admin {interaction.user.mention} removed packs from {user.mention}",
            color=Color.red(),
            fields=[
                {"name": "Admin", "value": f"{interaction.user.name} ({interaction.user.id})", "inline": True},
                {"name": "Target User", "value": f"{user.name} ({user.id})", "inline": True},
                {"name": "Pack Type", "value": f"{pack_info['emoji']} {pack_info['name']}", "inline": True},
                {"name": "Amount Removed", "value": f"{amount} packs", "inline": True},
                {"name": "Old Amount", "value": f"{old_amount} packs", "inline": True},
                {"name": "New Amount", "value": f"{new_amount} packs", "inline": True}
            ]
        )


async def setup(bot: BallsDexBot):
    await bot.add_cog(CFCoins(bot))
