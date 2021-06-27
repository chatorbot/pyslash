import asyncio
from discord.http import Route
from discord.ext.commands import Bot, CommandNotFound
from .slash_command import SlashCommand, CommandsContext


class _CommandsProcessor:
    """Used to process the command registration."""
    def __init__(self, bot):
        self.bot = bot
        self.commands = {}

    def unload_command(self, name):
        """Unloads a command from the commands processor."""
        del self.commands[name]

    def _create_command(self, name, item, nested):
        """Create the command object."""
        # Get the commands description.
        description = ""
        if item.__doc__:
            description = item.__doc__

        # Create the command object.
        handler = None
        is_async = asyncio.iscoroutinefunction(item)
        if is_async:
            handler = item
        elif nested > 1:
            raise RecursionError("Command is nested too deep.")
        command = SlashCommand(self.bot, name, description, handler, getattr(item, "_private", False))

        # If this is a class, initialise all the sub-command children.
        if not is_async:
            # Handle the initialisation.
            for key in item.__dir__():
                attr = getattr(item, key)
                command_name = getattr(attr, "_slash", False)
                if command_name:
                    subcommand = self._create_command(command_name, attr, nested + 1)
                    command._add_child(subcommand)

            # Check the command has sub-commands.
            if len(command.children) == 0:
                raise TypeError("A sub-command class requires children.")

        # Return the command.
        return command

    def load_command(self, item):
        """Load command is used to load a command into the processor."""
        self.commands[item._slash] = self._create_command(item._slash, item, 0)


class _CommandsPatcher:
    """
    Used to handle the patching of the bot for slash commands support.
    This will handle processing cogs and slash command WebSocket events.
    """
    def __init__(self, bot: Bot):
        self.bot = bot
        self.bot._slash_commands = self
        self.processor = _CommandsProcessor(bot)
        self.patch()

    async def _process_interaction(self, data):
        """Used to process interactions from the user data."""
        if data["type"] == 2:
            # Handle processing commands with our internal processor.
            command_name = data["data"]["name"]
            try:
                try:
                    cmd = self.processor.commands[command_name]
                except KeyError:
                    raise CommandNotFound(f'The root command "{command_name}" was not found.')
                await cmd.execute(data)
            except Exception as e:
                self.bot.dispatch("command_error", CommandsContext(data, self.bot), e)
            return

        # TODO: Add component support!

    async def on_socket_response(self, message):
        """Defines the socket response listener."""
        if message["t"] != "INTERACTION_CREATE":
            # We are only worried about interactions with this library.
            return

        # Get the payload data.
        data = message["d"]

        # Handle processing the interaction.
        if data["type"] in (2, 3):
            await self._process_interaction(data)

    def patch(self):
        """Patch the bot with our own events and handlers."""
        # Add the socket listener.
        self.bot.add_listener(self.on_socket_response)

        # Patch cog loading.
        original_load_cog = Bot.add_cog

        def patched_load_cog(bot, cog):
            # Check if the cog itself is a command.
            if getattr(cog, "_slash", False):
                # It is a command and we should load it.
                self.processor.load_command(cog)
            else:
                # We're going on a slash hunt, we're gonna catch a big one.
                for key in cog.__dir__():
                    attr = getattr(cog, key)
                    if getattr(attr, "_slash", False):
                        # We found a command to register.
                        self.processor.load_command(attr)

            # Call the original function.
            original_load_cog(bot, cog)

        Bot.add_cog = patched_load_cog

        # Patch cog unloading.
        original_remove_cog = Bot.remove_cog

        def patched_remove_cog(bot: Bot, name):
            # Get the cog.
            cog = bot.get_cog(name)
            if cog:
                # Check if the cog itself is a command.
                if getattr(cog, "_slash", False):
                    # It is a command and we should unload it.
                    self.processor.unload_command(name)
                else:
                    # We're going on a slash hunt, we're gonna catch a big one.
                    for key in cog.__dir__():
                        attr = getattr(cog, key)
                        name = getattr(attr, "_slash", False)
                        if name:
                            # We found a command to register.
                            self.processor.unload_command(name)

            # Call the original function.
            original_remove_cog(bot, name)

        Bot.remove_cog = patched_remove_cog


def commands_init(bot: Bot):
    """Calls the class that patches the bot with slash commands support."""
    _CommandsPatcher(bot)


def slash_command_wrapper(name: str = None, private: bool = False):
    """Defines the slash command wrapper."""
    def wrapper(x):
        x._private = private
        if name:
            x._slash = name
        else:
            x._slash = x.__name__.lower()
        return x
    return wrapper


def slash_command_parent(name: str, description: str = None, private: bool = False):
    """Creates a slash command parent which you can add to."""
    class Parent:
        def __init__(self):
            """Constructs the class in the function scope."""
            if description:
                self.__doc__ = description
            else:
                self.__doc__ = ""
            self._slash = name
            self._private = private

        def command(self, child_name: str = None, child_private: bool = False):
            """Defines the slash command child wrapper."""
            def wrapper(x):
                x._private = child_private
                if child_name:
                    x._slash = child_name
                else:
                    x._slash = x.__name__.lower()
                setattr(self, x.__name__, x)
                return x
            return wrapper

    return Parent()


def _cmd_to_dict(cmd: SlashCommand):
    """Turns a command into a dict."""
    if cmd.handler:
        # Create the root command.
        return {
            "name": cmd.name,
            "description": cmd.description,
            "options": cmd.args
        }

    # Create the children.
    children = []
    for name, child in enumerate(cmd.children):
        # Add for type checking.
        name: str
        child: SlashCommand

        # Check if this is a group.
        if child.handler:
            children.append({
                "type": 1,
                "name": name,
                "description": child.description,
                "required": True,
                "options": child.args
            })
        else:
            # Append the group.
            subchildren = []
            for subname, subchild in enumerate(child.children):
                # Add for type checking.
                subname: str
                subchild: SlashCommand

                # Append the child.
                subchildren.append({
                    "type": 2,
                    "name": subname,
                    "description": subchild.description,
                    "required": True,
                    "options": subchild.args
                })

            # Create the child.
            children.append({
                "type": 1,
                "name": name,
                "description": child.description,
                "required": True,
                "options": subchildren
            })

    # Return the children.
    return {
        "name": cmd.name,
        "description": cmd.description,
        "options": children
    }


async def update_commands_list(bot: Bot):
    """Update a slash commands list from the Discord API."""
    # Get all the command dicts.
    x: _CommandsPatcher = getattr(bot, "_slash_commands", None)
    if not x:
        raise TypeError("Slash commands patch not initialised.")
    cmd_dicts = [_cmd_to_dict(c) for c in x.processor.commands.values()]

    # Create the route hooking into existing major params.
    route = Route("PUT", "/applications/{channel_id}/commands", channel_id=bot.user.id)
    route.url = route.url.replace("v7", "v9")

    # Run the HTTP request.
    await bot.http.request(route, json=cmd_dicts)
