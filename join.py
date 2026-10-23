from sopel import module

@module.commands('join')  # Trigger with .join #channel [key]
@module.require_owner()   # Restrict to owner only (or use require_admin for admins)
def join(bot, trigger):
    """Make the bot join a channel for this session only (not saved; use .bjoin to also auto-rejoin on restart). Usage: .join #channel [key]"""
    if not trigger.group(2):
        bot.reply("Usage: .join #channel [key]")
        return
    parts = trigger.group(2).split()
    channel = parts[0]
    key = parts[1] if len(parts) > 1 else None
    bot.join(channel, key)
    bot.reply(f"Joining {channel}")
