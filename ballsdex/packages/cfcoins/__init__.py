from typing import TYPE_CHECKING

from ballsdex.packages.cfcoins.cog import CFCoins

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(CFCoins(bot))
