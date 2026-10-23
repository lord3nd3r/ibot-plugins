"""Custom help command — links to the commands reference on GitHub."""

from sopel import module

COMMANDS_URL = "https://github.com/lord3nd3r/ibot-plugins/blob/main/commands.md"


@module.commands("help", "commands", "cmds")
@module.example("$help")
def help_command(bot, trigger):
    """Links to the full command reference."""
    bot.say(f"{trigger.nick}: Full command list: {COMMANDS_URL}")
