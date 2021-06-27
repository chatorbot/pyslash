import asyncio
import typing
import discord
import inspect
from discord.http import Route
from discord.ext.commands import BadArgument, Context, Bot, Greedy, CheckFailure


class _SlashCommandArg:
    """Essentially acts as a struct for the bits of data."""
    def __init__(self, arg_type, func):
        self.arg_type = arg_type
        self.func = func


def _nothing_converter(_, __, arg):
    return arg


def _user_converter(bot, ctx, arg):
    resolved = ctx["data"]["resolved"]
    user = resolved.get("users", {}).get(arg)
    if not user:
        raise BadArgument("User specified is not someone the bot can see.")
    return discord.User(state=bot._get_state(), data=user)


def _member_converter(bot, ctx, arg):
    resolved = ctx["data"]["resolved"]
    member = resolved.get("members", {}).get(arg)
    if not member:
        raise BadArgument("User specified is not a member.")
    member["user"] = resolved["users"][arg]
    member["deaf"] = False  # Polyfill due to field missing.
    member["mute"] = False  # Polyfill due to field missing.
    return discord.Member(state=bot._get_state(), guild=bot.get_guild(int(ctx["guild_id"])), data=member)


def _text_channel_converter(bot, ctx, arg):
    channel_obj = ctx["data"]["resolved"].get("channels", {}).get(arg)
    if not channel_obj or channel_obj.get("guild_id") != ctx["guild_id"]:
        raise BadArgument("Channel not in this guild.")
    if channel_obj["type"] != 0:
        raise BadArgument("This is not a text channel.")
    return discord.TextChannel(state=bot._get_state(), guild=bot.get_guild(int(ctx["guild_id"])), data=channel_obj)


def _category_channel_converter(bot, ctx, arg):
    channel_obj = ctx["data"]["resolved"].get("channels", {}).get(arg)
    if not channel_obj or channel_obj.get("guild_id") != ctx["guild_id"]:
        raise BadArgument("Channel not in this guild.")
    if channel_obj["type"] != 4:
        raise BadArgument("This is not a category channel.")
    return discord.CategoryChannel(state=bot._get_state(), guild=bot.get_guild(int(ctx["guild_id"])), data=channel_obj)


def _role_converter(bot, ctx, arg):
    resolved = ctx["data"]["resolved"]
    return discord.Role(state=bot._get_state(), guild=bot.get_guild(int(ctx["guild_id"])), data=resolved["roles"][arg])


def _mentionable_converter(_, __, arg):
    return discord.Object(id=int(arg))


# Defines the various types.
_arg_types = {
    str: _SlashCommandArg(3, _nothing_converter),
    int: _SlashCommandArg(4, _nothing_converter),
    bool: _SlashCommandArg(5, _nothing_converter),
    discord.User: _SlashCommandArg(6, _user_converter),
    discord.Member: _SlashCommandArg(6, _member_converter),
    discord.TextChannel: _SlashCommandArg(7, _text_channel_converter),
    discord.CategoryChannel: _SlashCommandArg(7, _category_channel_converter),
    discord.Role: _SlashCommandArg(8, _role_converter),
    discord.Object: _SlashCommandArg(9, _mentionable_converter)
}


class CommandsMessage(discord.Message):
    """
    Defines a mock message based on context data.
    The difference here is that the message ID is the interaction ID to allow created_at to work properly.
    """
    def __init__(self, ctx_data, bot):
        self._ctx_data = ctx_data
        super().__init__(state=bot._get_state(), channel=self._create_text_channel(ctx_data, bot), data=self._create_message_payload(ctx_data))

    @staticmethod
    def _create_message_payload(ctx_data):
        """Creates a pseudo message payload with the interaction data."""
        member = ctx_data.get("member")
        return {
            "id": ctx_data["id"],
            "channel_id": ctx_data["channel_id"],
            "guild_id": ctx_data["guild_id"],
            "member": member,
            "author": member["user"] if member else ctx_data.get("user"),
            "content": "",
            "tts": False,
            "mention_everyone": False,
            "mentions": [],
            "mention_roles": [],
            "mention_channels": [],
            "attachments": [],
            "embeds": [],
            "reactions": [],
            "pinned": False,
            "type": 0,
            "edited_timestamp": None
        }

    @staticmethod
    def _create_text_channel(ctx_data, bot: Bot):
        """Creates a text channel with the data."""
        return bot.get_channel(int(ctx_data["channel_id"]))


class CommandsContext(Context):
    """Defines the context for a slash command."""
    def __init__(self, ctx_data, bot):
        super().__init__(bot=bot, prefix="", message=CommandsMessage(ctx_data, bot))
        self._first_reply = True
        self._ctx_data = ctx_data
        self.ephemeral = False

    async def reinvoke(self, *, call_hooks=False, restart=True):
        raise NotImplementedError("This is not implemented in slash commands.")

    async def reply(self, content=None, **kwargs):
        # Handle secondary replies with the default replies handler.
        if not self._first_reply:
            return await super().reply(content, **kwargs)

        # Create the route hooking into existing params.
        route = Route("POST", "/interactions/{channel_id}/{guild_id}/callback", channel_id=self._ctx_data["id"],
                      guild_id=self._ctx_data["token"])
        route.url = route.url.replace("v7", "v9")

        # Do the request.
        bot: Bot = self.bot

        data = {
            "tts": bool(kwargs.get("tts"))
        }  # TODO: Add component support!

        if self.ephemeral:
            data["flags"] = 64

        if content:
            data["content"] = content

        embeds = kwargs.get("embeds")
        if embeds:
            data["embeds"] = [e.to_dict() for e in embeds]

        embed = kwargs.get("embed")
        if embed:
            data["embeds"] = [embed.to_dict()]

        allowed_mentions = kwargs.get("allowed_mentions")
        if allowed_mentions:
            data["allowed_mentions"] = allowed_mentions

        await bot.http.request(route, json={
            "type": 4,
            "data": data
        })

        # Any replies from now are secondary replies.
        self._first_reply = False


def _wrap_arg_handler_async(f):
    async def x(bot, ctx, arg):
        return f(bot, ctx, arg)
    return x


async def _is_typing_optional(f):
    async def x(bot, ctx, arg):
        return await f(bot, ctx, arg)
    x._required = False
    return x


def _get_converter_function(converter_type):
    """Get the correct converter function and argument type."""
    # Handle the easiest and most likely event that it's a simple argument.
    x = _arg_types.get(converter_type)
    if x:
        # This means we get all the data right off the bat.
        func = _wrap_arg_handler_async(x.func)
        arg_type = x.arg_type
        return func, arg_type

    # Handle typing.
    x = getattr(converter_type, "__origin__", None)
    if x:
        if x is typing.Optional:
            # Take the first argument and put it through the processor.
            x = converter_type.__args__[0]
            (converter_function, param_type_id) = _get_converter_function(x)
            return _is_typing_optional(converter_function), param_type_id

        if x is typing.Union:
            # Handle fetching multiple converters.
            required = True
            discord_obj_fallback = False
            arg_type = set()
            converters = []
            for arg in converter_type.__args__:
                # Handle nonetypes.
                if isinstance(arg, type(None)):
                    # Set required to false and continue.
                    required = False
                    continue

                # Handle discord.Object.
                if arg is discord.Object:
                    discord_obj_fallback = True
                    continue

                # Get the converter with the current function.
                (converter_function, param_type_id) = _get_converter_function(x)

                # Append the converter.
                converters.append((converter_function, param_type_id))
                arg_type.add(param_type_id)

            # Get the converters length.
            converters_len = len(converters)

            # If the length is 0, it isn't valid.
            if converters_len == 0:
                raise TypeError("No converters in union.")

            # If the length is 1, just return the first thing.
            if converters_len == 1:
                if not required:
                    (x, y) = converters[0]
                    return _is_typing_optional(x), y
                return converters[0]

            # Parses a single type.
            def parse_single_type(arg_type_popped):
                last = None

                async def single_type_processor(bot, ctx, arg):
                    for conv in converters:
                        try:
                            return await conv[0](bot, ctx, arg)
                        except BadArgument:
                            pass
                    return await last[0](bot, ctx, arg)

                if discord_obj_fallback:
                    # Check if this is a Discord ID-able object.
                    if arg_type_popped in {3, 4, 5}:
                        raise TypeError("Argument type not ID'able.")
                    last = (_wrap_arg_handler_async(_mentionable_converter),)
                else:
                    # Set last to the last converter.
                    last = converters.pop()

                return single_type_processor, arg_type

            # Check if all one type. If so, return a generic handler to go through them all.
            if len(arg_type) == 1:
                return parse_single_type(arg_type.pop())

            # Allow int > string fallbacks.
            if arg_type == {3, 4}:
                # Transform int fallbacks with a parser.
                for index, c in enumerate(converters):
                    if c[1] == 4:
                        origin = c[0]

                        async def transform_int(bot, ctx, arg):
                            try:
                                y = int(arg)
                            except ValueError:
                                raise BadArgument("Unable to parse int.")
                            return await origin(bot, ctx, y)

                        converters[index] = (transform_int, c[1])

                # Parse as all strings.
                return parse_single_type(3), 3

            # Throw that this is a unsupported fallback.
            raise TypeError("Unsupported fallback")

        if x is Greedy:
            # commands.Greedy isn't supported.
            raise TypeError("commands.Greedy is not supported with slash commands.")

    # Handle discord.ext.commands compatibility with transformers.
    x = getattr(converter_type, "convert", None)
    if not x:
        raise TypeError("The argument type is not discord.ext.commands compatible.")

    async def compat_layer(bot, ctx, arg):
        return await x(CommandsContext(ctx, bot), arg)

    return compat_layer, 3


class SlashCommand:
    """Defines a slash command."""
    def __init__(self, bot: Bot, name: str, description: str, handler, private: bool):
        self.bot = bot
        self.name = name
        self.description = description
        self.handler = handler
        self.args = []
        if handler:
            self._processors = self._process_args()
        else:
            self._processors = None
        self.private = private
        self.children = {}
        self.checks = self._get_checks()

    def _get_checks(self):
        """Get the checks that are relevant to this command."""
        # Get all the check items.
        checks = []
        x = getattr(self.bot, "_checks", [])
        for c in x:
            checks.append(c)
        x = getattr(self.bot, "_check_once", [])
        for c in x:
            checks.append(c)
        if self.handler:
            x = getattr(self.handler, "__commands_checks__", [])
            x.reverse()
            for c in x:
                checks.append(c)

        # Check they are all async.
        for index, c in enumerate(checks):
            if not asyncio.iscoroutinefunction(c):
                async def wrapper(ctx):
                    return c(ctx)

                checks[index] = wrapper

        # Return the checks.
        return checks

    async def execute(self, ctx_data):
        """Executes the command."""
        # If there's not a handler, find the child command.
        if not self.handler:
            options = ctx_data["data"]["options"]
            if len(options) != 1:
                raise BadArgument("Expected type option, found weird argument set.")
            base_option = options[0]
            if base_option["type"] not in {1, 2}:
                raise BadArgument("First argument is not a sub-command type.")
            name = base_option["name"]
            subcommand = self.children.get(name)
            if not subcommand:
                raise BadArgument(f'Unknown subcommand "{subcommand}"')
            ctx_data["data"]["options"] = base_option["options"]
            return await subcommand.execute(ctx_data)

        # Create the commands context.
        ctx = CommandsContext(ctx_data, self.bot)

        # Process the checks.
        if len(self.checks) != 0:
            for check in self.checks:
                if not await check(ctx):
                    raise CheckFailure(f'The check functions for command {self.name} failed.')

        # Process the arguments.
        args = []
        params = ctx_data["data"]["options"]
        for index, arg in enumerate(self._processors):
            args.append(await arg(self.bot, ctx_data, params[index]))

        # Execute the command.
        await self.handler(ctx, *args)

    def _add_child(self, child: "SlashCommand"):
        """Adds a child to the slash command."""
        if self.handler:
            raise RecursionError("Async command cannot have children.")
        self.children[child.name] = child

    def _process_args(self):
        """Processes the args in such a way that they can be rapidly transformed."""
        # Defines the commands processors.
        processors = []

        # Get the signature for the handler.
        sig = inspect.signature(self.handler)
        params = iter(sig.parameters.items())
        try:
            possible_ctx = next(params)
        except StopIteration:
            raise TypeError("Expected context")
        if possible_ctx[1].annotation not in {possible_ctx[1].empty, Context, CommandsContext}:
            raise TypeError("Context type is invalid")
        for name, param in params:
            # Get the converter type.
            converter_type = param.annotation
            if converter_type is param.empty:
                # Default to string.
                converter_type = str

            # Defines if the param is required.
            required = param.default is param.empty

            # Get the converter function.
            (converter_function, param_type_id) = _get_converter_function(converter_type)
            if required:
                # Check for typing.Optional
                required = getattr(converter_function, "_required", True)

            # Handle pyslash's conversion entrypoint.
            async def conversion_entrypoint(bot, ctx, arg):
                # Get the value.
                arg = arg.get("value")

                # Handle optional arguments.
                if arg is None:
                    # Handle checking if this is an issue with the Discord response or default.
                    if required:
                        # We shouldn't have got this response. Throw an error.
                        raise BadArgument("Received optional param from Discord but no option is set.")

                    # Return the default parameter.
                    default = param.default
                    if default is param.empty:
                        default = None
                    return default

                # Do the argument conversion.
                return await converter_function(bot, ctx, arg)

            # Append the entrypoint.
            processors.append(conversion_entrypoint)

            # Append the argument for the Discord API list.
            description = "Optional input"
            if required:
                description = "Required input"
            self.args.append({
                "type": param_type_id,
                "name": name,
                "description": description,
                "required": required
            })

        # Return the processors.
        return processors
