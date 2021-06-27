import os
import discord
from discord.ext.commands import Bot, Cog, has_role
from pyslash import slash_command_wrapper, commands_init, update_commands_list

b = Bot("")
b.remove_command("help")

commands_init(b)


class Content(Cog):
    @staticmethod
    @has_role(858744856368775198)
    @slash_command_wrapper()
    async def hello_world(ctx, member: discord.Member):
        """POG"""
        ctx.ephemeral = True
        await ctx.reply(f"Hello World! You tagged someone with the nickname {member.nick}!")


b.add_cog(Content())


@b.event
async def on_ready():
    await update_commands_list(b)


b.run(os.environ["TOKEN"])
