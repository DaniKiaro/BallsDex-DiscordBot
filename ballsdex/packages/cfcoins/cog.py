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

PACK_TYPES = {
    "normal": {
        "name": "Normal Pack",
        "price": 100,
        "emoji": "📦",
        "color": Color.blue(),
        "min_rarity": 15.0,
        "max_rarity": 30.0,
    },
    "epic": {
        "name": "Epic Pack",
        "price": 250,
        "emoji": "🎁",
        "color": Color.purple(),
        "min_rarity": 1.0,
        "max_rarity": 5.0,
    },
    "mythic": {
        "name": "Mythic Pack",
        "price": 500,
        "emoji": "✨",
        "color": Color.gold(),
        "min_rarity": 0.1,
        "max_rarity": 1.0,
    },
    "legendary": {
        "name": "Legendary Pack",
        "price": 1000,
        "emoji": "🌟",
        "color": Color.from_rgb(255, 215, 0),
        "min_rarity": 0.01,
        "max_rarity": 0.1,
    },
}


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
            return random.randint(5, 10)
        elif rarity >= 10.0:
            return random.randint(10, 20)
        elif rarity >= 5.0:
            return random.randint(20, 40)
        elif rarity >= 2.0:
            return random.randint(40, 80)
        elif rarity >= 1.0:
            return random.randint(80, 150)
        elif rarity >= 0.5:
            return random.randint(150, 300)
        elif rarity >= 0.1:
            return random.randint(300, 600)
        elif rarity >= 0.01:
            return random.randint(600, 1000)
        else:
            return random.randint(1000, 2000)

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

        if ball.favorite:
            await interaction.response.send_message(
                "❌ You cannot sell a favorited ball! Remove it from favorites first.",
                ephemeral=True,
            )
            return

        if not ball.is_tradeable:
            await interaction.response.send_message(
                "❌ This ball is not tradeable and cannot be sold.", ephemeral=True
            )
            return

        await ball.fetch_related("ball")
        rarity = ball.countryball.rarity
        coins = self.calculate_sell_value(rarity)

        ball_name = ball.countryball.country
        ball_id = f"#{ball.pk:0X}"

        self.bot.cf_wallet[user_id] += coins

        await ball.delete()

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
            app_commands.Choice(name="Normal Pack (100 coins)", value="normal"),
            app_commands.Choice(name="Epic Pack (250 coins)", value="epic"),
            app_commands.Choice(name="Mythic Pack (500 coins)", value="mythic"),
            app_commands.Choice(name="Legendary Pack (1000 coins)", value="legendary"),
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


async def setup(bot: BallsDexBot):
    await bot.add_cog(CFCoins(bot))
