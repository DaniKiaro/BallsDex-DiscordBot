from typing import TYPE_CHECKING

from ballsdex.packages.footballgame.cog import FootballGame

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(FootballGame(bot))
