"""
IRC Voting Module for Sopel
Allows halfops and above to create polls with multiple options and time limits
"""

from sopel import plugin, tools
from sopel.config.types import StaticSection, ValidatedAttribute
import sqlite3
import threading
import time
from datetime import datetime, timedelta
import re
import os
from collections import defaultdict

# Database lock for thread safety
db_lock = threading.Lock()

# Active votes tracking (in-memory for quick access)
active_votes = {}
vote_timers = {}


class VotingSection(StaticSection):
    """Configuration section for voting module."""
    db_path = ValidatedAttribute('db_path', default='voting.db')
    message_delay = ValidatedAttribute('message_delay', default=0.5)  # Delay between messages in seconds


def get_db_path(bot):
    """Get the absolute path to the database, resolving it relative to bot's homedir if necessary."""
    db_path = bot.config.voting.db_path
    if not os.path.isabs(db_path):
        homedir = getattr(bot.config.core, 'homedir', None)
        if homedir:
            db_path = os.path.join(homedir, db_path)
        else:
            db_path = os.path.abspath(db_path)
    return db_path


def parse_db_datetime(dt_str):
    """Robustly parse SQLite datetime string into a naive datetime object."""
    if not dt_str:
        return datetime.now()
    if isinstance(dt_str, datetime):
        return dt_str
    
    # Standardize separator
    dt_str = dt_str.replace(' ', 'T')
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        try:
            return datetime.strptime(dt_str.split('.')[0], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return datetime.now()


def setup(bot):
    """Setup the module, initialize database, and load active votes."""
    bot.config.define_section('voting', VotingSection)
    init_database(bot)
    load_active_votes(bot)


def shutdown(bot):
    """Cancel all active vote timers on plugin unload to prevent leaks."""
    for channel, timer in list(vote_timers.items()):
        try:
            timer.cancel()
        except Exception:
            pass
    vote_timers.clear()
    active_votes.clear()


def init_database(bot):
    """Initialize the SQLite database for storing votes."""
    db_path = get_db_path(bot)
    
    # Ensure parent directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
        
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Table for vote metadata
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS votes (
                vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                creator TEXT NOT NULL,
                question TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'active',
                total_votes INTEGER DEFAULT 0
            )
        ''')
        
        # Table for vote options
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vote_options (
                option_id INTEGER PRIMARY KEY AUTOINCREMENT,
                vote_id INTEGER NOT NULL,
                option_number INTEGER NOT NULL,
                option_text TEXT NOT NULL,
                vote_count INTEGER DEFAULT 0,
                FOREIGN KEY (vote_id) REFERENCES votes(vote_id)
            )
        ''')
        
        # Table for tracking who voted
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vote_id INTEGER NOT NULL,
                nick TEXT NOT NULL,
                option_number INTEGER NOT NULL,
                voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vote_id) REFERENCES votes(vote_id),
                UNIQUE(vote_id, nick)
            )
        ''')
        
        conn.commit()
        conn.close()


def load_active_votes(bot):
    """Load active votes from the database and restart their end timers."""
    db_path = get_db_path(bot)
    now = datetime.now()
    
    with db_lock:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT vote_id, channel, creator, question, expires_at
                FROM votes
                WHERE status = 'active'
            ''')
            rows = cursor.fetchall()
            
            for row in rows:
                vote_id, channel, creator, question, expires_at_str = row
                expires_at = parse_db_datetime(expires_at_str)
                
                # Fetch options
                cursor.execute('''
                    SELECT option_number, option_text
                    FROM vote_options
                    WHERE vote_id = ?
                ''', (vote_id,))
                opt_rows = cursor.fetchall()
                options = {opt_num: opt_text for opt_num, opt_text in opt_rows}
                
                time_left = (expires_at - now).total_seconds()
                
                # If it already expired, end it after a short delay
                if time_left <= 0:
                    timer = threading.Timer(5.0, end_vote, args=[bot, channel, vote_id])
                else:
                    timer = threading.Timer(min(time_left, MAX_VOTE_SECONDS), end_vote, args=[bot, channel, vote_id])
                
                active_votes[channel] = {
                    'vote_id': vote_id,
                    'question': question,
                    'options': options,
                    'expires_at': expires_at,
                    'creator': creator
                }
                
                timer.start()
                vote_timers[channel] = timer
            
            conn.close()
        except Exception:
            pass


MAX_VOTE_SECONDS = 30 * 86400   # T: cap (30 days)
MIN_VOTE_SECONDS = 10
MAX_VOTE_OPTIONS = 10


def parse_time_duration(time_str):
    """Parse time duration like '24h', '30m', '2d' into seconds."""
    time_str = time_str.lower().strip()
    
    # Match pattern like 24h, 30m, 2d
    match = re.match(r'^(\d{1,9})([smhd])$', time_str)
    if not match:
        return None
    
    value, unit = match.groups()
    value = int(value)
    
    if unit == 's':
        return value
    elif unit == 'm':
        return value * 60
    elif unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400
    
    return None


def is_halfop_or_above(bot, channel, nick):
    """Check if user has halfop or higher privileges."""
    if not channel.startswith('#'):
        return False
    
    if channel not in bot.channels:
        return False
    
    if nick not in bot.channels[channel].users:
        return False
    
    privileges = bot.channels[channel].privileges.get(nick, 0)
    
    # Using Sopel/ibot constants for bitwise privilege verification
    halfop = getattr(plugin, 'HALFOP', 2)
    op = getattr(plugin, 'OP', 4)
    admin = getattr(plugin, 'ADMIN', 8)
    owner = getattr(plugin, 'OWNER', 16)
    
    return bool(privileges & (halfop | op | admin | owner))


@plugin.command('votehelp')
@plugin.example('.votehelp')
def vote_help(bot, trigger):
    """
    Send voting help information via PM.
    Usage: .votehelp or .vote help
    """
    nick = trigger.nick
    
    # Send help via PM using standard bot.say without destination= keyword
    bot.say("📚 ═══════════════════════════════════════", nick)
    bot.say("🗳️  VOTING SYSTEM HELP", nick)
    bot.say("📚 ═══════════════════════════════════════", nick)
    bot.say("", nick)
    bot.say("🎯 CREATING A VOTE (Halfop+ only)", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("Command: .vote <question> [A1:option1 A2:option2...] [T:duration]", nick)
    bot.say("Aliases: .voting, .poll", nick)
    bot.say("", nick)
    bot.say("📝 Formats:", nick)
    bot.say("  Simple: .vote <question>           (defaults to Yes/No options, 24h)", nick)
    bot.say("  Custom: .vote Q:question A1:opt1 A2:opt2 T:duration", nick)
    bot.say("", nick)
    bot.say("⏰ Time formats:", nick)
    bot.say("  30s = 30 seconds", nick)
    bot.say("  15m = 15 minutes", nick)
    bot.say("  24h = 24 hours (default)", nick)
    bot.say("  7d = 7 days", nick)
    bot.say("", nick)
    bot.say("💡 Examples:", nick)
    bot.say("  .vote Should we order pizza?", nick)
    bot.say("  .vote Best pizza? A1:Pepperoni A2:Cheese A3:Veggie T:1h", nick)
    bot.say("", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("🗳️  CASTING YOUR VOTE", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("Command: .v <number>", nick)
    bot.say("", nick)
    bot.say("💡 Examples:", nick)
    bot.say("  .v 1    - Vote for option 1", nick)
    bot.say("  .v 2    - Vote for option 2", nick)
    bot.say("", nick)
    bot.say("✨ You can change your vote anytime!", nick)
    bot.say("", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("📊 VIEWING STATISTICS", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("Commands:", nick)
    bot.say("  .votestats   - Show current results", nick)
    bot.say("  .vstats      - Same as above", nick)
    bot.say("  .voteresults - Same as above", nick)
    bot.say("  .voting      - View current poll status", nick)
    bot.say("", nick)
    bot.say("Shows: vote counts, percentages, progress bars, time left", nick)
    bot.say("", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("🛑 ENDING A VOTE EARLY", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("Command: .endvote", nick)
    bot.say("", nick)
    bot.say("⚠️  Only the vote creator or halfops+ can end votes", nick)
    bot.say("", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("ℹ️  ADDITIONAL INFO", nick)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", nick)
    bot.say("• One active vote per channel at a time", nick)
    bot.say("• One vote per person (can be changed)", nick)
    bot.say("• Votes auto-close when time expires", nick)
    bot.say("• Winner announced with 🏆 trophy", nick)
    bot.say("• All votes saved to database", nick)
    bot.say("", nick)
    bot.say("📚 ═══════════════════════════════════════", nick)
    
    # Confirm in channel/trigger source
    bot.reply("📬 Help information sent via PM!")


@plugin.command('vote', 'voting', 'poll')
@plugin.example('.vote Should we add a new feature?')
@plugin.example('.vote Q:Should we add a new feature? A1:Yes A2:No A3:Maybe T:24h')
def create_vote(bot, trigger):
    """
    Create a new vote/poll in the channel or view status of an active vote.
    Usage: .vote <question> [A1:option1 A2:option2...] [T:duration]
    Default options: Yes / No | Default duration: 24h
    Requires halfop or above to create.
    """
    # Check if user has required privileges
    if not is_halfop_or_above(bot, trigger.sender, trigger.nick):
        bot.reply("❌ You need to be at least halfop to create a vote!")
        return
    
    raw_args = trigger.group(2)
    
    # If no arguments provided, or user asks for status/help:
    if not raw_args or raw_args.strip().lower() in ('status', 'stats', 'results'):
        if trigger.sender in active_votes:
            show_vote_stats(bot, trigger)
            return
        bot.reply("Usage: .vote <question> [A1:opt1 A2:opt2...] [T:duration] (e.g. .vote Should we order pizza? or .vote help)")
        return
    
    if raw_args.strip().lower() in ('help', 'votehelp'):
        vote_help(bot, trigger)
        return
    
    # Check if there is already an active vote in this channel
    if trigger.sender in active_votes:
        bot.reply("❌ There is already an active vote in this channel! End it with .endvote first or view results with .votestats")
        return
    
    args = raw_args.strip()
    
    # Extract optional time duration T:<duration>
    t_match = re.search(r'\bT:(\d+[smhd])\b', args, re.IGNORECASE)
    if t_match:
        duration_str = t_match.group(1)
        args_clean = (args[:t_match.start()] + " " + args[t_match.end():]).strip()
    else:
        duration_str = '24h'
        args_clean = args
    
    duration_seconds = parse_time_duration(duration_str)
    if not duration_seconds:
        bot.reply("❌ Invalid time format! Use format like: 30m, 24h, 2d")
        return
    if duration_seconds > MAX_VOTE_SECONDS or duration_seconds < MIN_VOTE_SECONDS:
        bot.reply(f"❌ Vote duration must be between {MIN_VOTE_SECONDS}s and "
                  f"{MAX_VOTE_SECONDS // 86400}d.")
        return
    
    # Extract options A1:..., A2:..., etc.
    options = {}
    option_matches = list(re.finditer(r'A(\d+):([^AT]+?)(?=A\d+:|T:|$)', args_clean, re.IGNORECASE))
    
    if option_matches:
        # Question is whatever text comes before the first option match
        first_opt_start = option_matches[0].start()
        q_text = args_clean[:first_opt_start].strip()
        q_text = re.sub(r'^Q:\s*', '', q_text, flags=re.IGNORECASE).strip()
        question = q_text if q_text else "Vote"
        
        for match in option_matches:
            opt_num = int(match.group(1))
            opt_text = match.group(2).strip()
            if opt_text:
                options[opt_num] = opt_text
    else:
        # No explicit option matches: treat entire cleaned text as question, default to Yes/No
        q_text = re.sub(r'^Q:\s*', '', args_clean, flags=re.IGNORECASE).strip()
        question = q_text if q_text else "Vote"
        options = {1: "Yes", 2: "No"}
    
    if len(options) < 2:
        bot.reply("❌ You need at least 2 options! Example: A1:option1 A2:option2")
        return
    if len(options) > MAX_VOTE_OPTIONS:
        bot.reply(f"❌ Too many options (max {MAX_VOTE_OPTIONS}).")
        return
    
    # Create vote in database
    db_path = get_db_path(bot)
    expires_at = datetime.now() + timedelta(seconds=duration_seconds)
    
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Insert vote
        cursor.execute('''
            INSERT INTO votes (channel, creator, question, expires_at)
            VALUES (?, ?, ?, ?)
        ''', (trigger.sender, trigger.nick, question, expires_at))
        
        vote_id = cursor.lastrowid
        
        # Insert options
        for opt_num, opt_text in sorted(options.items()):
            cursor.execute('''
                INSERT INTO vote_options (vote_id, option_number, option_text)
                VALUES (?, ?, ?)
            ''', (vote_id, opt_num, opt_text))
        
        conn.commit()
        conn.close()
    
    # Store in active votes
    active_votes[trigger.sender] = {
        'vote_id': vote_id,
        'question': question,
        'options': options,
        'expires_at': expires_at,
        'creator': trigger.nick
    }
    
    # Schedule vote end
    timer = threading.Timer(duration_seconds, end_vote, args=[bot, trigger.sender, vote_id])
    timer.start()
    vote_timers[trigger.sender] = timer
    
    # Get message delay setting safely as float
    try:
        delay = float(bot.config.voting.message_delay)
    except (ValueError, TypeError):
        delay = 0.5
    
    # Announce the vote (with anti-flood delays, using standard bot.say without destination= keyword)
    bot.say("📊 ═══════════════════════════════════════", trigger.sender)
    time.sleep(delay)
    bot.say(f"🗳️  NEW VOTE by {trigger.nick}", trigger.sender)
    time.sleep(delay)
    bot.say(f"❓ {question}", trigger.sender)
    time.sleep(delay)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", trigger.sender)
    time.sleep(delay)
    
    for opt_num, opt_text in sorted(options.items()):
        emoji = get_number_emoji(opt_num)
        bot.say(f"{emoji} Option {opt_num}: {opt_text}", trigger.sender)
        time.sleep(delay)
    
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", trigger.sender)
    time.sleep(delay)
    bot.say(f"⏰ Vote ends in {format_duration(duration_seconds)}", trigger.sender)
    time.sleep(delay)
    bot.say(f"💡 Vote with: .v {' or '.join([str(n) for n in sorted(options.keys())])}", trigger.sender)
    time.sleep(delay)
    bot.say("📊 ═══════════════════════════════════════", trigger.sender)


@plugin.command('v', 'castvote')
@plugin.example('.v 1')
def cast_vote(bot, trigger):
    """
    Cast your vote for an active poll.
    Usage: .v <option_number>
    """
    if trigger.sender not in active_votes:
        bot.reply("❌ No active vote in this channel!")
        return
    
    if not trigger.group(2):
        bot.reply("Usage: .v <option_number>")
        return
    
    try:
        option_num = int(trigger.group(2).strip())
    except ValueError:
        bot.reply("❌ Please provide a valid option number!")
        return
    
    vote_data = active_votes[trigger.sender]
    
    if option_num not in vote_data['options']:
        bot.reply(f"❌ Invalid option! Choose from: {', '.join([str(n) for n in sorted(vote_data['options'].keys())])}")
        return
    
    # Record vote in database
    db_path = get_db_path(bot)
    vote_id = vote_data['vote_id']
    
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if user already voted
        cursor.execute('''
            SELECT option_number FROM user_votes
            WHERE vote_id = ? AND nick = ?
        ''', (vote_id, trigger.nick))
        
        existing_vote = cursor.fetchone()
        
        if existing_vote:
            old_option = existing_vote[0]
            
            # Update vote
            cursor.execute('''
                UPDATE user_votes
                SET option_number = ?, voted_at = CURRENT_TIMESTAMP
                WHERE vote_id = ? AND nick = ?
            ''', (option_num, vote_id, trigger.nick))
            
            # Update vote counts
            cursor.execute('''
                UPDATE vote_options
                SET vote_count = vote_count - 1
                WHERE vote_id = ? AND option_number = ?
            ''', (vote_id, old_option))
            
            cursor.execute('''
                UPDATE vote_options
                SET vote_count = vote_count + 1
                WHERE vote_id = ? AND option_number = ?
            ''', (vote_id, option_num))
            
            conn.commit()
            conn.close()
            
            emoji = get_number_emoji(option_num)
            bot.reply(f"✅ Vote changed to {emoji} Option {option_num}!")
        else:
            # Insert new vote
            cursor.execute('''
                INSERT INTO user_votes (vote_id, nick, option_number)
                VALUES (?, ?, ?)
            ''', (vote_id, trigger.nick, option_num))
            
            # Update vote count
            cursor.execute('''
                UPDATE vote_options
                SET vote_count = vote_count + 1
                WHERE vote_id = ? AND option_number = ?
            ''', (vote_id, option_num))
            
            # Update total votes
            cursor.execute('''
                UPDATE votes
                SET total_votes = total_votes + 1
                WHERE vote_id = ?
            ''', (vote_id,))
            
            conn.commit()
            conn.close()
            
            emoji = get_number_emoji(option_num)
            bot.reply(f"✅ Vote recorded for {emoji} Option {option_num}!")


@plugin.command('votestats', 'vstats', 'voteresults')
@plugin.example('.votestats')
def show_vote_stats(bot, trigger):
    """
    Show current statistics for the active vote.
    Usage: .votestats
    """
    if trigger.sender not in active_votes:
        bot.reply("❌ No active vote in this channel!")
        return
    
    vote_data = active_votes[trigger.sender]
    vote_id = vote_data['vote_id']
    
    # Get current stats from database
    db_path = get_db_path(bot)
    
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT option_number, option_text, vote_count
            FROM vote_options
            WHERE vote_id = ?
            ORDER BY option_number
        ''', (vote_id,))
        
        options = cursor.fetchall()
        
        cursor.execute('''
            SELECT total_votes FROM votes WHERE vote_id = ?
        ''', (vote_id,))
        
        row = cursor.fetchone()
        total_votes = row[0] if row else 0
        conn.close()
    
    # Calculate time remaining
    time_left = (vote_data['expires_at'] - datetime.now()).total_seconds()
    
    # Get message delay setting safely as float
    try:
        delay = float(bot.config.voting.message_delay)
    except (ValueError, TypeError):
        delay = 0.5
    
    # Display stats (with anti-flood delays, using standard bot.say without destination= keyword)
    bot.say("📊 ═══════════════════════════════════════", trigger.sender)
    time.sleep(delay)
    bot.say("📈 VOTE STATISTICS", trigger.sender)
    time.sleep(delay)
    bot.say(f"❓ {vote_data['question']}", trigger.sender)
    time.sleep(delay)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", trigger.sender)
    time.sleep(delay)
    bot.say(f"🗳️  Total Votes: {total_votes}", trigger.sender)
    time.sleep(delay)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", trigger.sender)
    time.sleep(delay)
    
    for opt_num, opt_text, vote_count in options:
        percentage = (vote_count / total_votes * 100) if total_votes > 0 else 0
        bar = create_progress_bar(percentage)
        emoji = get_number_emoji(opt_num)
        
        bot.say(f"{emoji} Option {opt_num}: {opt_text}", trigger.sender)
        time.sleep(delay)
        bot.say(f"   {bar} {vote_count} votes ({percentage:.1f}%)", trigger.sender)
        time.sleep(delay)
    
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", trigger.sender)
    time.sleep(delay)
    
    if time_left > 0:
        bot.say(f"⏰ Time remaining: {format_duration(int(time_left))}", trigger.sender)
    else:
        bot.say("⏰ Vote has ended!", trigger.sender)
    
    time.sleep(delay)
    bot.say("📊 ═══════════════════════════════════════", trigger.sender)


@plugin.command('endvote')
def manual_end_vote(bot, trigger):
    """
    Manually end the active vote (creator or halfop+ only).
    Usage: .endvote
    """
    if trigger.sender not in active_votes:
        bot.reply("❌ No active vote in this channel!")
        return
    
    vote_data = active_votes[trigger.sender]
    
    # Check if user is creator or has privileges
    if trigger.nick != vote_data['creator'] and not is_halfop_or_above(bot, trigger.sender, trigger.nick):
        bot.reply("❌ Only the vote creator or halfops+ can end the vote!")
        return
    
    # Cancel timer if exists
    if trigger.sender in vote_timers:
        try:
            vote_timers[trigger.sender].cancel()
        except Exception:
            pass
        del vote_timers[trigger.sender]
    
    # End the vote
    end_vote(bot, trigger.sender, vote_data['vote_id'])


def end_vote(bot, channel, vote_id):
    """End a vote and display final results."""
    if channel not in active_votes:
        return
    
    vote_data = active_votes[channel]
    
    # Protect against racing or mismatched closing
    if vote_data['vote_id'] != vote_id:
        return
    
    # Mark vote as closed in database
    db_path = get_db_path(bot)
    
    with db_lock:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE votes
            SET status = 'closed'
            WHERE vote_id = ?
        ''', (vote_id,))
        
        cursor.execute('''
            SELECT option_number, option_text, vote_count
            FROM vote_options
            WHERE vote_id = ?
            ORDER BY vote_count DESC, option_number
        ''', (vote_id,))
        
        options = cursor.fetchall()
        
        cursor.execute('''
            SELECT total_votes FROM votes WHERE vote_id = ?
        ''', (vote_id,))
        
        row = cursor.fetchone()
        total_votes = row[0] if row else 0
        conn.commit()
        conn.close()
    
    # Get message delay setting safely as float
    try:
        delay = float(bot.config.voting.message_delay)
    except (ValueError, TypeError):
        delay = 0.5
    
    # Display final results (with anti-flood delays, using standard bot.say without destination= keyword)
    bot.say("🏁 ═══════════════════════════════════════", channel)
    time.sleep(delay)
    bot.say("🎉 VOTE ENDED - FINAL RESULTS", channel)
    time.sleep(delay)
    bot.say(f"❓ {vote_data['question']}", channel)
    time.sleep(delay)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", channel)
    time.sleep(delay)
    bot.say(f"🗳️  Total Votes: {total_votes}", channel)
    time.sleep(delay)
    bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", channel)
    time.sleep(delay)
    
    if total_votes > 0:
        winner = options[0]
        winner_emoji = get_number_emoji(winner[0])
        
        for opt_num, opt_text, vote_count in options:
            percentage = (vote_count / total_votes * 100) if total_votes > 0 else 0
            bar = create_progress_bar(percentage)
            emoji = get_number_emoji(opt_num)
            
            # Add trophy for winner
            trophy = "🏆 " if opt_num == winner[0] else "   "
            
            bot.say(f"{trophy}{emoji} Option {opt_num}: {opt_text}", channel)
            time.sleep(delay)
            bot.say(f"   {bar} {vote_count} votes ({percentage:.1f}%)", channel)
            time.sleep(delay)
        
        bot.say("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", channel)
        time.sleep(delay)
        bot.say(f"🏆 WINNER: {winner_emoji} Option {winner[0]} - {winner[1]}", channel)
    else:
        bot.say("😔 No votes were cast!", channel)
    
    time.sleep(delay)
    bot.say("🏁 ═══════════════════════════════════════", channel)
    
    # Clean up
    if channel in active_votes:
        del active_votes[channel]
    if channel in vote_timers:
        del vote_timers[channel]


def create_progress_bar(percentage, length=20):
    """Create a visual progress bar."""
    filled = int(length * percentage / 100)
    empty = length - filled
    
    bar = "█" * filled + "░" * empty
    return f"[{bar}]"


def get_number_emoji(num):
    """Get emoji representation of a number."""
    emoji_map = {
        1: "1️⃣",
        2: "2️⃣",
        3: "3️⃣",
        4: "4️⃣",
        5: "5️⃣",
        6: "6️⃣",
        7: "7️⃣",
        8: "8️⃣",
        9: "9️⃣",
        10: "🔟"
    }
    return emoji_map.get(num, f"#{num}")


def format_duration(seconds):
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes > 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        if hours > 0:
            return f"{days}d {hours}h"
        return f"{days}d"
