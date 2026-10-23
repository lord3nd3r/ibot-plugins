import time
import os
import sqlite3
import json
import random
import threading
import logging
import atexit
import re
from sopel import module, formatting

"""weed.py — Sopel command to share lighthearted "weed" messages.

Features:
- Commands: weed, bong, joint, jay, doobie, keef, kief, trip, shrooms, acid, peyote, toke
- Channel-level and per-user-per-channel cooldowns (thread-safe)
- Non-blocking countdown via background thread with graceful shutdown
- Module-level constants for easy editing
"""

LOG = logging.getLogger(__name__)

# Cooldown in seconds (20 minutes = 1200 seconds)
COOLDOWN = 1200
# Per-user cooldown in seconds (30 seconds)
PER_USER_COOLDOWN = 30

# State (thread-safe access via LOCK)
LAST_USED = {}
PER_USER_LAST = {}
LOCK = threading.Lock()

# Thread management: track active countdown threads
_ACTIVE_THREADS = set()
_SHUTDOWN_EVENT = threading.Event()
_THREAD_LOCK = threading.Lock()

# =======================
# WEED Content
# =======================
WEED_GIFTS = [
    "a hand-rolled joint 🌿",
    "a fat bong rip 🌊",
    "a tasty edible (brownie) 🍪",
    "a vape hit ☁️",
    "a classic blunt 🔥",
    "a dab (slab) ⚡",
    "a bowl packed and ready 🔥",
    "a CBD gummy 🍬",
    "a preroll cone 🌯",
    "a joint dusted in kief ✨",
    "a wax pen cartridge 🖊️",
    "a gravity bong hit 🌀",
    "a Thai stick 🎋",
    "a hash cookie 🍫",
    "a spliff (weed + tobacco) 🚬",
    "a cross joint ✖️",
    "a THC-infused drink 🥤",
    "a moon rock (nug dipped in oil & kief) 🌙",
    "a rosin press hit 💎",
    "a waterfall bong 💧",
    "some live resin 🍯",
    "a backwoods blunt 🍂",
    "a tulip joint 🌷",
    "a chillum pipe 🪈",
    "a one-hitter dugout 🎯",
    "a bubbler 🫧",
    "a tincture dropper 💧",
    "THC-infused honey 🍯",
    "a weed lollipop 🍭",
    "a cannagar (cannabis cigar) 🎩",
]

WEED_ACTION_MESSAGES = [
    "hands {target} {gift}",
    "passes {gift} to {target}",
    "slides {gift} across the table to {target}",
    "offers {target} {gift} — puff responsibly!",
    "tosses {gift} to {target} with a wink 😉",
]

WEED_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Puff puff pass — light it up and keep it lit! 🌿🔥", formatting.colors.LIGHT_GREEN)),
    formatting.color("Roll a fat one, spark it, and pass the vibes 🙌🍁", formatting.colors.LIGHT_GREEN),
    formatting.color("Bong rip incoming — lean back and ride the clouds 🌊💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Edible vibes: chill mode activated, munchies on standby 🍪🔥", formatting.colors.LIGHT_GREEN),
    formatting.color("Blunt sesh: slow hits, deep laughs, and loud tunes 🎶🌬️", formatting.colors.LIGHT_GREEN),
    formatting.color("Vape clouds: show off your fattest ring ☁️🏆", formatting.colors.LIGHT_GREEN),
    formatting.color("Dab night — small hit, big trip ⚡", formatting.colors.LIGHT_GREEN),
    formatting.color("Hotbox the room — windows up, vibes up 🚗💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Kief it to the moon — sprinkle that goodness ✨", formatting.colors.LIGHT_GREEN),
    formatting.color("Keep it toasted and roasted — pass the flame 🔥🍞", formatting.colors.LIGHT_GREEN),
    formatting.color("Name your strain and flex it in chat — what's everyone smoking? 🌱👀", formatting.colors.LIGHT_GREEN),
    formatting.color("Snap a pic of your stash and share the glow-up 📸🌿", formatting.colors.LIGHT_GREEN),
    formatting.color("Munchies run! Pizza, tacos, cereal — vote now 🍕🌮🥣", formatting.colors.LIGHT_GREEN),
    formatting.color("Sesh soundtrack: drop a track and crank it up 🎵🔊", formatting.colors.LIGHT_GREEN),
    formatting.color("Cloud contest: who can make the biggest plume? 🌫️💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Keep the sesh lit — only good vibes allowed ✌️🔥", formatting.colors.LIGHT_GREEN),
]

WEED_COUNTDOWN = [
    formatting.color("🌿 3... Get ready...", formatting.colors.GREEN),
    formatting.color("🔥 2... Spark it...", formatting.colors.YELLOW),
    formatting.color("💨 1... Inhale...", formatting.colors.RED),
]


# =======================
# BONG Content
# =======================
BONG_GIFTS = [
    "a freshly cleaned bong 🫧", "an ice-catch bong ❄️", "a gravity bong 🌀", 
    "a mini bubbler 🫧", "a percolator bong 💧", "a massive beaker bong 🧪", 
    "a gas mask bong 😷", "a straight tube bong 🌬️", "a silicone bong 🪀", 
    "a multi-chamber bong 🏙️"
]

BONG_ACTION_MESSAGES = [
    "passes {gift} to {target} 🫧",
    "rips {gift} and hands it to {target} 💨",
    "milks {gift} for {target} 🥛",
    "clears the chamber of {gift} and gives it to {target} 🧊",
    "packs {gift} for {target} 🌿"
]

BONG_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Bong rip incoming — lean back and ride the clouds 🌊💨", formatting.colors.LIGHT_BLUE)),
    formatting.color("Clear that chamber! 🫧💨", formatting.colors.LIGHT_BLUE),
    formatting.color("Ice cold hits from the bong ❄️🧊", formatting.colors.CYAN),
    formatting.color("Don't drink the bong water! 🤢💧", formatting.colors.GREEN),
    formatting.color("That percolator is bubbling! 💧🫧", formatting.colors.LIGHT_BLUE),
]

BONG_COUNTDOWN = [
    formatting.color("🫧 3... Filling the water...", formatting.colors.LIGHT_BLUE),
    formatting.color("🧊 2... Adding ice...", formatting.colors.CYAN),
    formatting.color("🔥 1... Lighting the bowl...", formatting.colors.RED),
]


# =======================
# JOINT Content
# =======================
JOINT_GIFTS = [
    "a hand-rolled joint 🌿", "a cross joint ✖️", "a spliff 🚬", 
    "a fat cone 🍦", "a pinner joint 📍", "a kief-dusted joint ✨", 
    "a double-barrel joint ✌️", "a tulip joint 🌷", "a classic paper joint 📜", 
    "an infused joint 🍯", "a backwards-rolled joint 🔄"
]

JOINT_ACTION_MESSAGES = [
    "passes {gift} to {target} 🚬",
    "lights {gift} and hands it to {target} 🔥",
    "sparks up {gift} for {target} ✨",
    "rolls {gift} for {target} 📜",
    "tosses {gift} (lit) to {target} 🌿"
]

JOINT_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Puff puff pass! 🌿🔥", formatting.colors.LIGHT_GREEN)),
    formatting.color("Don't bogart the joint! 🏃💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Hotboxing with this joint 🚗💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Spark it up and pass it around! 🔥🍁", formatting.colors.LIGHT_GREEN),
    formatting.color("A perfectly rolled joint. Enjoy! 📜🤌", formatting.colors.LIGHT_GREEN),
]

JOINT_COUNTDOWN = [
    formatting.color("📜 3... Grinding...", formatting.colors.GREEN),
    formatting.color("👅 2... Rolling...", formatting.colors.YELLOW),
    formatting.color("🔥 1... Sparking...", formatting.colors.RED),
]


# =======================
# JAY Content
# =======================
JAY_GIFTS = [
    "a hand-rolled jay 🌿", "a cross jay ✖️", "a spliff 🚬", 
    "a fat cone 🍦", "a pinner jay 📍", "a kief-dusted jay ✨", 
    "a double-barrel jay ✌️", "a tulip jay 🌷", "a classic paper jay 📜", 
    "an infused jay 🍯", "a backwards-rolled jay 🔄"
]

JAY_ACTION_MESSAGES = [
    "passes {gift} to {target} 🚬",
    "lights {gift} and hands it to {target} 🔥",
    "sparks up {gift} for {target} ✨",
    "rolls {gift} for {target} 📜",
    "tosses {gift} (lit) to {target} 🌿"
]

JAY_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Puff puff pass! 🌿🔥", formatting.colors.LIGHT_GREEN)),
    formatting.color("Don't bogart the jay! 🏃💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Hotboxing with this jay 🚗💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Spark it up and pass it around! 🔥🍁", formatting.colors.LIGHT_GREEN),
    formatting.color("A perfectly rolled jay. Enjoy! 📜🤌", formatting.colors.LIGHT_GREEN),
]

JAY_COUNTDOWN = [
    formatting.color("📜 3... Grinding...", formatting.colors.GREEN),
    formatting.color("👅 2... Rolling...", formatting.colors.YELLOW),
    formatting.color("🔥 1... Sparking...", formatting.colors.RED),
]


# =======================
# DOOBIE Content
# =======================
DOOBIE_GIFTS = [
    "a hand-rolled doobie 🌿", "a cross doobie ✖️", "a spliff 🚬", 
    "a fat cone 🍦", "a pinner doobie 📍", "a kief-dusted doobie ✨", 
    "a double-barrel doobie ✌️", "a tulip doobie 🌷", "a classic paper doobie 📜", 
    "an infused doobie 🍯", "a backwards-rolled doobie 🔄"
]

DOOBIE_ACTION_MESSAGES = [
    "passes {gift} to {target} 🚬",
    "lights {gift} and hands it to {target} 🔥",
    "sparks up {gift} for {target} ✨",
    "rolls {gift} for {target} 📜",
    "tosses {gift} (lit) to {target} 🌿"
]

DOOBIE_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Puff puff pass! 🌿🔥", formatting.colors.LIGHT_GREEN)),
    formatting.color("Don't bogart the doobie! 🏃💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Hotboxing with this doobie 🚗💨", formatting.colors.LIGHT_GREEN),
    formatting.color("Spark it up and pass it around! 🔥🍁", formatting.colors.LIGHT_GREEN),
    formatting.color("A perfectly rolled doobie. Enjoy! 📜🤌", formatting.colors.LIGHT_GREEN),
]

DOOBIE_COUNTDOWN = [
    formatting.color("📜 3... Grinding...", formatting.colors.GREEN),
    formatting.color("👅 2... Rolling...", formatting.colors.YELLOW),
    formatting.color("🔥 1... Sparking...", formatting.colors.RED),
]


# =======================
# SPLIFF Content
# =======================
SPLIFF_GIFTS = [
    "a European-style spliff (weed + tobacco) 🚬",
    "a fat spliff with Dutch tobacco 🇳🇱🚬",
    "a hash & tobacco spliff 🟤🚬",
    "a cone spliff with menthol tobacco 🍃🚬",
    "a cone spliff wrapped in tobacco leaf 🍂🚬",
    "a custom-rolled spliff with golden Virginia 🍂🌿",
    "a pinner spliff 📍🚬",
    "a cross spliff ✖️🚬",
]

SPLIFF_ACTION_MESSAGES = [
    "passes {gift} to {target} 🚬",
    "lights {gift} and hands it to {target} 🔥",
    "sparks up {gift} for {target} ✨",
    "rolls a fresh {gift} for {target} 📜",
    "tosses {gift} (lit) to {target} 🌿",
]

SPLIFF_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Spliff session — tobacco buzz meets green chill! 🚬🌿🔥", formatting.colors.LIGHT_GREEN)),
    formatting.color("That tobacco spin + weed high hit just right 🍂☁️", formatting.colors.LIGHT_GREEN),
    formatting.color("Euro-style smoking: roll the spliff and pass it around 🇪🇺🚬", formatting.colors.LIGHT_GREEN),
    formatting.color("Perfect ratio: 70% green, 30% tobacco ⚖️🌿", formatting.colors.LIGHT_GREEN),
    formatting.color("Smooth burn on this spliff. Puff puff pass! 📜🤌", formatting.colors.LIGHT_GREEN),
]

SPLIFF_COUNTDOWN = [
    formatting.color("🍂 3... Mixing weed & tobacco...", formatting.colors.GREEN),
    formatting.color("👅 2... Rolling the spliff...", formatting.colors.YELLOW),
    formatting.color("🔥 1... Sparking up...", formatting.colors.RED),
]


# =======================
# KEEF Content
# =======================
KEEF_GIFTS = [
    "a bowl topped with keef ✨", "a keef puck 🥏", "a sprinkle of keef 🪄", 
    "a kief-coated moonrock 🌑", "a scoop of pure keef 🥄", "a keefy joint ☄️", 
    "a press of keef rosin 🍯", "a bowl full of pure keef 🥣"
]

KEEF_ACTION_MESSAGES = [
    "sprinkles {gift} for {target} ✨",
    "packs {gift} for {target} 🥣",
    "dusts {target}'s joint with {gift} 🪄",
    "shares {gift} with {target} 🏆",
    "presses {gift} for {target} 🥏"
]

KEEF_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Keef it to the moon! ✨🌙", formatting.colors.YELLOW)),
    formatting.color("That keef hits different 🌌💫", formatting.colors.YELLOW),
    formatting.color("Golden dust vibes ✨🍯", formatting.colors.YELLOW),
    formatting.color("Sprinkle a little magic on it 🧚‍♀️🪄", formatting.colors.YELLOW),
    formatting.color("Careful with that keef, it's strong! ⚠️✨", formatting.colors.YELLOW),
]

KEEF_COUNTDOWN = [
    formatting.color("✨ 3... Collecting dust...", formatting.colors.YELLOW),
    formatting.color("🪄 2... Sprinkling...", formatting.colors.YELLOW),
    formatting.color("🔥 1... Melting...", formatting.colors.RED),
]


# =======================
# DMT Content
# =======================
DMT_GIFTS = [
    "a hit from a glass vapor genie 🌬️", "a breakthrough dose loaded in a pipe 🚀",
    "a DMT vape cart ☁️🌀", "a pharmahuasca capsule 💊", "a changa joint (DMT + MAOI herbs) 🌿🔥",
    "an ayahuasca brew 🍵🌀", "a crystal of pure freebase 💎", "a sandwich-method bowl 🪨",
    "an e-mesh loaded with 40mg ⚡", "a bufo toad secretion dose 🐸✨",
    "a DMT-infused honey stick 🍯🌀", "an enhanced leaf blend 🍂✨",
    "a sublingually-dosed tab 👅🌈", "a yopo seed pod 🌰👽",
]

DMT_ACTION_MESSAGES = [
    "loads {gift} for {target} — safe travels ✨🚀",
    "carefully hands {gift} to {target} — see you on the other side 🌀",
    "offers {gift} to {target} — hold on tight 👽",
    "presents {gift} to {target} — breathe deep 🌬️🌈",
    "places {gift} before {target} — the entities await 🔮",
]

DMT_FINAL_MESSAGES = [
    formatting.bold(formatting.color("B R E A K T H R O U G H — the chrysanthemum opens 🌸🌀👽", formatting.colors.PURPLE)),
    formatting.color("The machine elves are waving hello 🧙‍♂️👋✨ welcome to hyperspace", formatting.colors.PURPLE),
    formatting.color("Fractals within fractals within fractals 🌀🔯🌀 you are everything", formatting.colors.LIGHT_BLUE),
    formatting.color("The waiting room dissolves… geometric entities approach 🔶🔷🔶 do you have a question?", formatting.colors.PURPLE),
    formatting.color("Time has ceased to exist — you are the universe experiencing itself 🌌🧠✨", formatting.colors.LIGHT_BLUE),
    formatting.color("Jester entities juggle impossible shapes while singing in colors 🎪🎨🎶", formatting.colors.PURPLE),
    formatting.color("The cosmic serpent uncoils and shows you the DNA of reality 🐍🧬🌌", formatting.colors.TEAL),
    formatting.color("You have been yeeted through a kaleidoscope cannon into God's living room 💥🌀🛋️", formatting.colors.PURPLE),
]

DMT_COUNTDOWN = [
    formatting.color("🌬️  3... Inhale deeply...", formatting.colors.PURPLE),
    formatting.color("🌀  2... Hold it... hold it...", formatting.colors.LIGHT_BLUE),
    formatting.color("🚀  1... Let go...", formatting.colors.LIGHT_PURPLE),
]


# =======================
# SHROOMS Content
# =======================
SHROOMS_GIFTS = [
    "a handful of golden teachers 🍄✨", "a chocolate bar with 3.5g penis envy 🍫🍄",
    "a cup of shroom tea ☕🍄", "a dose of blue meanies 💙🍄",
    "a bag of liberty caps 🍂🍄", "a heroic dose of 5g dried 🦸🍄",
    "a microdose capsule (0.2g) 💊🌱", "some albino A+ caps 🦢🍄",
    "a jar of shroom honey 🍯🍄", "a lemon tek shot 🍋⚡",
    "some Amazonian cubensis 🌿🍄", "a psilocybin gummy bear 🐻🌈",
    "a fat stem of B+ 🍄🚀", "a truffle of psilocybe tampanensis 🪵✨",
]

SHROOMS_ACTION_MESSAGES = [
    "hands {target} {gift} — the mushroom speaks if you listen 🍄👂",
    "passes {gift} to {target} — nature's gift 🌿✨",
    "offers {gift} to {target} — enjoy the journey 🌈",
    "shares {gift} with {target} — mush love 🍄❤️",
    "drops {gift} into {target}'s hand — see the world breathe 🌍💫",
]

SHROOMS_FINAL_MESSAGES = [
    formatting.bold(formatting.color("The walls are breathing and the carpet is alive 🌊🍄🧘", formatting.colors.TEAL)),
    formatting.color("Trees are talking to each other and you can hear it 🌳🗣️🌳 shhhh listen", formatting.colors.GREEN),
    formatting.color("Everything is connected — you are the mycelial network 🍄🕸️🌍", formatting.colors.TEAL),
    formatting.color("The music has colors and the colors have feelings 🎨🎵😂😭", formatting.colors.GREEN),
    formatting.color("Time is a flat circle and you're sitting in the middle of it eating chips 🍕♾️😂", formatting.colors.TEAL),
    formatting.color("Your third eye is open and honestly it's a bit much 👁️🔮😅", formatting.colors.GREEN),
    formatting.color("You just had a 45-minute conversation with a tree. It was profound 🌲🧠💬", formatting.colors.TEAL),
    formatting.color("Ego? Never met her. You are stardust, friend ✨🌌🫠", formatting.colors.GREEN),
]

SHROOMS_COUNTDOWN = [
    formatting.color("🍄 3... Chewing...", formatting.colors.TEAL),
    formatting.color("🌊 2... The come-up begins...", formatting.colors.GREEN),
    formatting.color("🌈 1... Here it comes...", formatting.colors.LIGHT_GREEN),
]


# =======================
# ACID Content
# =======================
ACID_GIFTS = [
    "a tab of white-on-white 🧮✨", "a gel tab (300ug) 💎🌈",
    "a blotter with Grateful Dead art 💀⚡", "a sugar cube drop 🧂💧",
    "a liquid vial hit 🧪🌀", "a strip of dancing bears 🐻🎶",
    "a tab of bicycle day art 🚲🌈", "a double-dipped tab 👅👅",
    "a breathmint with a surprise inside 🌬️😈", "a microdose smart tab 🧠✨",
    "a gummy bear with 200ug 🐻🌟", "a ten-strip for the whole channel 🎟️🚀",
    "an Owsley original — vintage vibes 🎸✨", "a tab with Alex Grey art 👁️🎨",
]

ACID_ACTION_MESSAGES = [
    "places {gift} on {target}'s tongue 👅✨",
    "slides {gift} to {target} — buckle up 🎢",
    "hands {gift} to {target} — see you in 12 hours ⏰🌈",
    "drops {gift} for {target} — the walls will melt, it's fine 😉🌀",
    "offers {gift} to {target} — the fractals are calling 🔯",
]

ACID_FINAL_MESSAGES = [
    formatting.bold(formatting.color("The ceiling is a Mandelbrot set and you can zoom forever 🔯♾️🤯", formatting.colors.LIGHT_PURPLE)),
    formatting.color("Tracers on EVERYTHING — your hand just painted a rainbow across the room 🌈💫✋", formatting.colors.LIGHT_PURPLE),
    formatting.color("The music is a physical structure and you're walking through it 🎶🏛️🚶", formatting.colors.LIGHT_BLUE),
    formatting.color("You just understood the universe for 3 seconds and now it's gone 🌌🧠💨", formatting.colors.LIGHT_PURPLE),
    formatting.color("Is it still Tuesday? It's been Tuesday for 47 years 📅♾️😵‍💫", formatting.colors.LIGHT_BLUE),
    formatting.color("Your face in the mirror just winked at you independently 🧑‍🎤😉😱", formatting.colors.LIGHT_PURPLE),
    formatting.color("Every leaf on that tree is a separate universe and you can feel all of them 🍃🌌🍃", formatting.colors.LIGHT_BLUE),
    formatting.color("The come-up hits and you realize: we're all just vibrations pretending to be solid 🎵🌊🫠", formatting.colors.LIGHT_PURPLE),
]

ACID_COUNTDOWN = [
    formatting.color("👅 3... Under the tongue...", formatting.colors.LIGHT_PURPLE),
    formatting.color("🌀 2... The edges start to shimmer...", formatting.colors.LIGHT_BLUE),
    formatting.color("🎢 1... Liftoff...", formatting.colors.PURPLE),
]


# =======================
# PEYOTE Content
# =======================
PEYOTE_GIFTS = [
    "a dried peyote button 🌵✨", "a cup of San Pedro cactus tea ☕🌵",
    "a slice of fresh peyote 🪓🌵", "a mescaline extract capsule 💊🌟",
    "a sacred medicine bundle 🌿🔮", "a peyote stitch pouch (with actual peyote inside) 🧶🌵",
    "a mescaline sulfate crystal 💎🌈", "a handful of San Pedro chips 🌵🍺",
    "an ancestral vision dose 🧬🌌", "a cactus smoothie (yes, really) 🥤🌵",
]

PEYOTE_ACTION_MESSAGES = [
    "offers {gift} to {target} — the desert spirit calls 🏜️🌵",
    "hands {gift} to {target} with reverence 🙏✨",
    "shares {gift} with {target} — the cactus knows 🌵👁️",
    "places {gift} before {target} — sit with the medicine 🧘🌵",
    "presents {gift} to {target} — the grandfather spirit watches 🌞👁️",
]

PEYOTE_FINAL_MESSAGES = [
    formatting.bold(formatting.color("The desert is alive and every grain of sand is singing 🏜️🎶✨", formatting.colors.ORANGE)),
    formatting.color("Grandfather Peyote shows you the horizon where earth meets spirit 🌅👁️🌌", formatting.colors.ORANGE),
    formatting.color("The cactus is 10,000 years old and it has something to tell you 🌵🧓📜", formatting.colors.YELLOW),
    formatting.color("Colors you've never seen before drip from the stars like honey 🌟🍯🎨", formatting.colors.ORANGE),
    formatting.color("A coyote made of light trots across your field of vision and nods 🦊✨👍", formatting.colors.YELLOW),
    formatting.color("The fire ceremony has begun — shadows dance stories of creation 🔥💃🌌", formatting.colors.ORANGE),
    formatting.color("You ARE the desert. The wind is your breath. The sun is your heart 🏜️💨☀️", formatting.colors.YELLOW),
    formatting.color("The mescaline hits and suddenly every cactus looks like it's waving at you 🌵👋🌵👋🌵", formatting.colors.ORANGE),
]

PEYOTE_COUNTDOWN = [
    formatting.color("🌵 3... Chewing the bitter button...", formatting.colors.ORANGE),
    formatting.color("🏜️  2... The desert wind stirs...", formatting.colors.YELLOW),
    formatting.color("🌞 1... The grandfather speaks...", formatting.colors.RED),
]


# =======================
# TOKE Content (flower only)
# =======================
TOKE_GIFTS = [
    "a fat bowl of OG Kush 🔥🌿",
    "a freshly packed one-hitter 🎯💨",
    "a pipe loaded with Purple Haze 💜🌬️",
    "a spoon pipe of Sour Diesel ⛽🔥",
    "a chillum packed with Girl Scout Cookies 🍪🌿",
]

TOKE_ACTION_MESSAGES = [
    "packs a bowl and passes the pipe to {target} 🔥💨",
    "lights up {gift} and hands it to {target} — toke up 🌿",
    "loads {gift} and slides the pipe to {target} 🌬️",
    "sparks {gift} for {target} — hit it and quit it 💨🔥",
    "torches {gift} and offers it to {target} — puff puff 🍃",
]

TOKE_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Toke it up — slow inhale, hold, exhale the stress 🌿💨✨", formatting.colors.GREEN)),
    formatting.color("Corner the bowl and pass it left — proper sesh etiquette 🔥🫡", formatting.colors.LIGHT_GREEN),
    formatting.color("One fat toke and the whole room goes quiet 😶‍🌫️🌿💨", formatting.colors.GREEN),
    formatting.color("Cash that bowl and pack another — the sesh never ends ♻️🔥🍃", formatting.colors.LIGHT_GREEN),
    formatting.color("That first toke of the day hits different 🌅🌿😌", formatting.colors.GREEN),
]

TOKE_COUNTDOWN = [
    formatting.color("🌿 3... Packing the bowl...", formatting.colors.GREEN),
    formatting.color("🔥 2... Cornering the green...", formatting.colors.YELLOW),
    formatting.color("💨 1... Toke...", formatting.colors.RED),
]


# =======================
# EDIBLES Content
# =======================
EDIBLE_GIFTS = [
    "a double-fudge canna-brownie 🍫🌿", "a bag of 100mg gummy worms 🪱🌈",
    "a space cake straight from Amsterdam 🍰🚀", "a THC chocolate bar (12 squares) 🍫✨",
    "a canna-butter rice krispie treat 🍬🌿", "a jar of infused honey 🍯💫",
    "a THC seltzer (10mg) 🥤🫧", "a bag of canna-caramel popcorn 🍿🍯",
    "a firecracker (peanut butter + decarb) 🥜🧨", "a weed lollipop 🍭🌿",
    "a tin of 5mg mints (deceptively innocent) 🌬️😇", "a slice of infused cheesecake 🍰😍",
    "a canna-cookie the size of your face 🍪🌕", "a THC capsule for the no-nonsense stoner 💊📋",
]

EDIBLE_ACTION_MESSAGES = [
    "hands {target} {gift} — start low, go slow 🐢",
    "passes {gift} to {target} — wait an hour before round two! ⏰",
    "serves {target} {gift} fresh from the canna-kitchen 👩‍🍳🌿",
    "sneaks {gift} to {target} with a knowing nod 🤫",
    "plates up {gift} for {target} — bon appétit 🍽️✨",
]

EDIBLE_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Edibles kicking in — you are now one with the couch 🛋️🫠", formatting.colors.GREEN)),
    formatting.color("45 minutes in: 'these aren't working' — famous last words 😂⏳", formatting.colors.LIGHT_GREEN),
    formatting.color("Two hours later: WHY is the ceiling so far away 🌌😳", formatting.colors.GREEN),
    formatting.color("Body high activated — gravity now 3x stronger 🪐😌", formatting.colors.LIGHT_GREEN),
    formatting.color("Do NOT eat the second brownie. Narrator: they ate the second brownie 🍫💀", formatting.colors.GREEN),
    formatting.color("Munchies inbound from an edible?? Snackception 🍕🍪♾️", formatting.colors.LIGHT_GREEN),
]

EDIBLE_COUNTDOWN = [
    formatting.color("🍪 3... Chewing...", formatting.colors.GREEN),
    formatting.color("⏰ 2... Waiting... waiting...", formatting.colors.YELLOW),
    formatting.color("🚀 1... Oh. OH. It hits...", formatting.colors.RED),
]


# =======================
# DAB Content
# =======================
DAB_GIFTS = [
    "a low-temp dab of live rosin 💎🌿", "a fat glob of shatter ⚡🍯",
    "a terp pearl spinning in a banger 🔮🌪️", "a scoop of golden budder 🧈✨",
    "a diamond-and-sauce dab 💎🍯", "a crumble dab of Wedding Cake 🍰⚡",
    "a full-melt hash rosin dab 🫠💛", "a cold-start dab (patience pays) ❄️🔥",
    "a honey-bucket swing of distillate 🍯🌀", "a THCa diamond the size of a dice 🎲💎",
]

DAB_ACTION_MESSAGES = [
    "heats the banger to a perfect 500° and dabs {gift} for {target} 🔥🌡️",
    "carb caps {gift} for {target} — milk it 🌪️🥛",
    "twirls {gift} onto the nail for {target} 💎🔥",
    "loads {gift} into the e-rig and hands it to {target} 🔋💨",
    "drops {gift} in for {target} — exhale when you see God 😇💨",
]

DAB_FINAL_MESSAGES = [
    formatting.bold(formatting.color("SEND IT — one dab and you're in orbit 🚀💎", formatting.colors.CYAN)),
    formatting.color("Coughing is just your lungs applauding 👏😵‍💫💨", formatting.colors.LIGHT_BLUE),
    formatting.color("Low temp, full flavor — taste those terps 🍋🌲👅", formatting.colors.CYAN),
    formatting.color("That glob was NOT a personal-size portion 😳⚡", formatting.colors.LIGHT_BLUE),
    formatting.color("Welcome to Dab City. Population: you, melting 🏙️🫠", formatting.colors.CYAN),
]

DAB_COUNTDOWN = [
    formatting.color("🔥 3... Heating the banger...", formatting.colors.RED),
    formatting.color("❄️ 2... Letting it cool... patience...", formatting.colors.CYAN),
    formatting.color("💎 1... Send it...", formatting.colors.LIGHT_BLUE),
]


# =======================
# BLUNT Content
# =======================
BLUNT_GIFTS = [
    "a slow-burning backwoods 🍂🔥", "a grape Swisher Sweet 🍇🚬",
    "a two-gram torpedo 🚀🌿", "a hemp-wrap blunt (tobacco-free) 🌱✨",
    "a gold-leaf-wrapped blunt (bougie mode) 🏆💛", "a Dutch Master rolled to perfection 🎩🌿",
    "an L rolled with extra crutch 📐🚬", "a honey-dipped blunt 🍯🔥",
    "a snoop-approved 10-incher 🎤🌿", "a front-to-back even burn (rare) 🕯️👌",
]

BLUNT_ACTION_MESSAGES = [
    "breaks down a swisher and rolls {gift} for {target} 🍂🤲",
    "sparks {gift} and passes it to {target} — left side 🔥👈",
    "seals {gift} with a slow lick and hands it to {target} 👅📜",
    "puts {gift} behind {target}'s ear for later 👂🌿",
    "lights {gift} for {target} — respect the rotation 🔁🔥",
]

BLUNT_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Blunt sesh: slow burns, deep laughs, loud speakers 🎶🍂🔥", formatting.colors.ORANGE)),
    formatting.color("Puff puff PASS — this ain't a solo mission 🫵🔁", formatting.colors.YELLOW),
    formatting.color("The rotation is sacred. Don't break the circle ⭕🙏", formatting.colors.ORANGE),
    formatting.color("Backwoods burning slow — settle in, we're here a while 🍂⏳", formatting.colors.YELLOW),
    formatting.color("Canoeing?? Somebody lick that thing back into shape 🛶😤", formatting.colors.ORANGE),
]

BLUNT_COUNTDOWN = [
    formatting.color("🍂 3... Splitting the wrap...", formatting.colors.ORANGE),
    formatting.color("👅 2... Rolling it tight...", formatting.colors.YELLOW),
    formatting.color("🔥 1... Sparking...", formatting.colors.RED),
]


# =======================
# VAPE Content
# =======================
VAPE_GIFTS = [
    "a live resin cart (Blue Dream) 🫐☁️", "a fresh disposable pen 🖊️💨",
    "a dry-herb vape at exactly 185°C 🌡️🌿", "a rosin cart — solventless gang 💎🖊️",
    "a full-spectrum pod ☁️🌈", "a discreet mini pen for the movies 🎬🤫",
    "a cart with the terps of a lemon grove 🍋☁️", "a volcano bag filled to the brim 🌋💨",
    "an old-school vape brick from 2015 (still works) 🧱😂", "a ceramic-coil cart (smooth operator) 🏺💨",
]

VAPE_ACTION_MESSAGES = [
    "hands {target} {gift} — 3 clicks to start ☁️🔋",
    "passes {gift} to {target} — no smell, no tell 🤫💨",
    "preheats {gift} for {target} 🌡️✨",
    "slides {gift} to {target} — sneaky hits only 🥷☁️",
    "fills the balloon and hands {gift} to {target} 🎈💨",
]

VAPE_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Vape clouds: show off your fattest ring ☁️🏆", formatting.colors.LIGHT_BLUE)),
    formatting.color("Stealth mode: hit it in the parking lot, nobody knows 🥷☁️", formatting.colors.CYAN),
    formatting.color("Battery died mid-sesh — a moment of silence 🔋🪦", formatting.colors.LIGHT_BLUE),
    formatting.color("Smooth pulls, zero cough — technology is beautiful 🤖☁️", formatting.colors.CYAN),
    formatting.color("That cart hits like a freight train in slippers 🚂🥿", formatting.colors.LIGHT_BLUE),
]

VAPE_COUNTDOWN = [
    formatting.color("🔋 3... Charging up...", formatting.colors.LIGHT_BLUE),
    formatting.color("🌡️ 2... Preheating...", formatting.colors.YELLOW),
    formatting.color("☁️ 1... Rip it...", formatting.colors.CYAN),
]


# =======================
# HASH Content
# =======================
HASH_GIFTS = [
    "a temple ball of Nepalese hash 🏯🟤", "a slab of Moroccan blonde 🇲🇦✨",
    "a finger of hand-rubbed charas 🖐️🌿", "a bowl of full-melt bubble hash 🫧💛",
    "a gram of Lebanese red 🇱🇧🔴", "a piece of Afghani black 🖤🏔️",
    "some dry-sift pressed fresh 🧈✨", "a snake of hash rolled for the spliff 🐍🚬",
    "a chunk that smells like 1974 🕰️🟤", "a hot-knife hit (kitchen chemistry) 🔪🔥",
]

HASH_ACTION_MESSAGES = [
    "crumbles {gift} into the bowl for {target} 🤲🟤",
    "warms {gift} and passes it to {target} 🔥🖐️",
    "presses {gift} into {target}'s palm — old world style 🏺✨",
    "rolls {gift} into a snake and tops {target}'s spliff 🐍🚬",
    "shares {gift} with {target} — centuries of tradition 📜🟤",
]

HASH_FINAL_MESSAGES = [
    formatting.bold(formatting.color("Old-world hash: centuries of craft in one bowl 🏺🟤✨", formatting.colors.ORANGE)),
    formatting.color("Temple ball glistening — this is artisanal stoning 🏯💎", formatting.colors.YELLOW),
    formatting.color("That charas came down a mountain on a donkey. Respect it 🏔️🐴", formatting.colors.ORANGE),
    formatting.color("Bubble hash so clean it melts like butter 🫧🧈", formatting.colors.YELLOW),
    formatting.color("One hot knife and you're speaking in colors 🔪🌈", formatting.colors.ORANGE),
]

HASH_COUNTDOWN = [
    formatting.color("🟤 3... Warming the hash...", formatting.colors.ORANGE),
    formatting.color("🤲 2... Crumbling it in...", formatting.colors.YELLOW),
    formatting.color("🔥 1... Torching...", formatting.colors.RED),
]


# =======================
# MUNCHIES Content
# =======================
MUNCHIES_GIFTS = [
    "an entire large pepperoni pizza 🍕📦", "a family-size bag of nacho cheese chips 🧀🔺",
    "a gas-station haul (chips, slushie, 3 candy bars) ⛽🛍️",
    "a tub of cookie dough ice cream 🍦🍪", "a tray of loaded nachos 🧀🌶️",
    "a box of cereal + the milk (no bowl) 🥣❌", "a stack of peanut butter toast 🥜🍞",
    "a bag of gummy sharks 🦈🌈", "cold leftover chinese takeout (elite tier) 🥡😤",
    "a sleeve of chocolate sandwich cookies 🍪🖤", "a microwave burrito at 2am 🌯🕑",
    "the entire snack drawer, unlocked 🗄️🔓",
]

MUNCHIES_ACTION_MESSAGES = [
    "delivers {gift} to {target} — emergency rations 🚨🍕",
    "slides {gift} to {target} — no judgment here 🤝🍪",
    "airdrops {gift} onto {target}'s lap 🪂🍿",
    "presents {gift} to {target} like a Michelin course 👨‍🍳✨",
    "hands {target} {gift} — the sesh saver 🦸🍫",
]

MUNCHIES_FINAL_MESSAGES = [
    formatting.bold(formatting.color("MUNCHIES RUN! Pizza, tacos, cereal — vote now 🍕🌮🥣", formatting.colors.YELLOW)),
    formatting.color("That first bite after the sesh?? Best meal of your LIFE 😭🍕", formatting.colors.ORANGE),
    formatting.color("Chips + candy + pickle in one bite. Genius or war crime? 🧪😳", formatting.colors.YELLOW),
    formatting.color("The fridge light hits different at 1am 🧊💡😌", formatting.colors.ORANGE),
    formatting.color("You've eaten cereal three times today and that's okay 🥣🥣🥣", formatting.colors.YELLOW),
]

MUNCHIES_COUNTDOWN = [
    formatting.color("🍕 3... Raiding the kitchen...", formatting.colors.YELLOW),
    formatting.color("🎤 2... Microwave hums...", formatting.colors.ORANGE),
    formatting.color("😋 1... FEAST...", formatting.colors.RED),
]


# Content Mapping
DATA = {
    'weed': (WEED_GIFTS, WEED_ACTION_MESSAGES, WEED_FINAL_MESSAGES, WEED_COUNTDOWN),
    'bong': (BONG_GIFTS, BONG_ACTION_MESSAGES, BONG_FINAL_MESSAGES, BONG_COUNTDOWN),
    'joint': (JOINT_GIFTS, JOINT_ACTION_MESSAGES, JOINT_FINAL_MESSAGES, JOINT_COUNTDOWN),
    'jay': (JAY_GIFTS, JAY_ACTION_MESSAGES, JAY_FINAL_MESSAGES, JAY_COUNTDOWN),
    'doobie': (DOOBIE_GIFTS, DOOBIE_ACTION_MESSAGES, DOOBIE_FINAL_MESSAGES, DOOBIE_COUNTDOWN),
    'spliff': (SPLIFF_GIFTS, SPLIFF_ACTION_MESSAGES, SPLIFF_FINAL_MESSAGES, SPLIFF_COUNTDOWN),
    'keef': (KEEF_GIFTS, KEEF_ACTION_MESSAGES, KEEF_FINAL_MESSAGES, KEEF_COUNTDOWN),
    'kief': (KEEF_GIFTS, KEEF_ACTION_MESSAGES, KEEF_FINAL_MESSAGES, KEEF_COUNTDOWN),
    'trip': (DMT_GIFTS, DMT_ACTION_MESSAGES, DMT_FINAL_MESSAGES, DMT_COUNTDOWN),
    'shrooms': (SHROOMS_GIFTS, SHROOMS_ACTION_MESSAGES, SHROOMS_FINAL_MESSAGES, SHROOMS_COUNTDOWN),
    'mushrooms': (SHROOMS_GIFTS, SHROOMS_ACTION_MESSAGES, SHROOMS_FINAL_MESSAGES, SHROOMS_COUNTDOWN),
    'acid': (ACID_GIFTS, ACID_ACTION_MESSAGES, ACID_FINAL_MESSAGES, ACID_COUNTDOWN),
    'lsd': (ACID_GIFTS, ACID_ACTION_MESSAGES, ACID_FINAL_MESSAGES, ACID_COUNTDOWN),
    'peyote': (PEYOTE_GIFTS, PEYOTE_ACTION_MESSAGES, PEYOTE_FINAL_MESSAGES, PEYOTE_COUNTDOWN),
    'mescaline': (PEYOTE_GIFTS, PEYOTE_ACTION_MESSAGES, PEYOTE_FINAL_MESSAGES, PEYOTE_COUNTDOWN),
    'toke': (TOKE_GIFTS, TOKE_ACTION_MESSAGES, TOKE_FINAL_MESSAGES, TOKE_COUNTDOWN),
    'edibles': (EDIBLE_GIFTS, EDIBLE_ACTION_MESSAGES, EDIBLE_FINAL_MESSAGES, EDIBLE_COUNTDOWN),
    'edible': (EDIBLE_GIFTS, EDIBLE_ACTION_MESSAGES, EDIBLE_FINAL_MESSAGES, EDIBLE_COUNTDOWN),
    'dab': (DAB_GIFTS, DAB_ACTION_MESSAGES, DAB_FINAL_MESSAGES, DAB_COUNTDOWN),
    'dabs': (DAB_GIFTS, DAB_ACTION_MESSAGES, DAB_FINAL_MESSAGES, DAB_COUNTDOWN),
    'blunt': (BLUNT_GIFTS, BLUNT_ACTION_MESSAGES, BLUNT_FINAL_MESSAGES, BLUNT_COUNTDOWN),
    'vape': (VAPE_GIFTS, VAPE_ACTION_MESSAGES, VAPE_FINAL_MESSAGES, VAPE_COUNTDOWN),
    'hash': (HASH_GIFTS, HASH_ACTION_MESSAGES, HASH_FINAL_MESSAGES, HASH_COUNTDOWN),
    'munchies': (MUNCHIES_GIFTS, MUNCHIES_ACTION_MESSAGES, MUNCHIES_FINAL_MESSAGES, MUNCHIES_COUNTDOWN),
    'w33d': (WEED_GIFTS, WEED_ACTION_MESSAGES, WEED_FINAL_MESSAGES, WEED_COUNTDOWN),
}


def _to_1337(text):
    """Convert text to 1337 speak while preserving IRC formatting codes and emojis."""
    if not text:
        return text
    table = {
        'a': '4', 'A': '4',
        'b': '8', 'B': '8',
        'e': '3', 'E': '3',
        'g': '9', 'G': '9',
        'i': '1', 'I': '1',
        'l': '1', 'L': '1',
        'o': '0', 'O': '0',
        's': '5', 'S': '5',
        't': '7', 'T': '7',
        'z': '2', 'Z': '2',
    }
    pattern = r'(\x03\d{0,2}(?:,\d{1,2})?|\x02|\x0f|\x16|\x1d|\x1f)'
    parts = re.split(pattern, text)
    res = []
    for part in parts:
        if not part:
            continue
        if part.startswith('\x03') or part in ('\x02', '\x0f', '\x16', '\x1d', '\x1f'):
            res.append(part)
        else:
            res.append(''.join(table.get(c, c) for c in part))
    return ''.join(res)


def _format_remaining(seconds):
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _cooldown_check_and_set(store, key, cooldown, now):
    """Atomically check a cooldown and claim it if free.

    Returns 0.0 if claimed (caller may proceed), else the remaining seconds.
    Check and set happen under one lock so two simultaneous triggers can't
    both pass the check. Also prunes stale entries so the dicts don't grow
    forever.
    """
    with LOCK:
        last = store.get(key)
        if last and (now - last) < cooldown:
            return cooldown - (now - last)
        store[key] = now
        if len(store) > 512:
            stale = [k for k, t in store.items() if (now - t) > COOLDOWN]
            for k in stale:
                store.pop(k, None)
        return 0.0


def _start_countdown(bot, channel, cmd, countdown_msgs, final_msgs, source='command'):
    """Spawn the countdown/final-message background thread."""
    try:
        t = threading.Thread(
            target=_countdown_and_final,
            args=(bot, channel, cmd, countdown_msgs, final_msgs),
            daemon=True,
            name=f"weed_{source}_{cmd}_{channel}",
        )
        t.start()
        LOG.debug(f"Started {source} countdown thread for ${cmd} in {channel}")
    except Exception as e:
        LOG.error(f"Error starting countdown thread for ${cmd}: {e}")


def _countdown_and_final(bot, channel, cmd, countdown_msgs, final_messages):
    """Run countdown and final message in a background thread.

    Args:
        bot: Sopel bot instance
        channel: IRC channel name
        cmd: Command name (for logging context)
        countdown_msgs: List of countdown messages
        final_messages: List of final messages
    """
    # Must be resolved HERE, inside the new thread — resolving it in the
    # spawner's args would register the Sopel handler thread instead.
    current_thread = threading.current_thread()
    try:
        # Register this thread
        with _THREAD_LOCK:
            _ACTIVE_THREADS.add(current_thread)

        if cmd == 'w33d':
            countdown_msgs = [_to_1337(m) for m in countdown_msgs]
            final_messages = [_to_1337(m) for m in final_messages]

        # Run countdown with shutdown signal checking
        if len(countdown_msgs) == 3:
            for i, msg in enumerate(countdown_msgs):
                # Check if shutdown was requested
                if _SHUTDOWN_EVENT.is_set():
                    LOG.debug(f"Countdown interrupted for ${cmd} in {channel} (shutdown)")
                    return

                bot.say(msg, channel)

                # Sleep in small increments to check shutdown signal
                if i < len(countdown_msgs) - 1:
                    for _ in range(6):  # 6s between messages (~12s total)
                        if _SHUTDOWN_EVENT.is_set():
                            LOG.debug(f"Countdown sleep interrupted for ${cmd} in {channel}")
                            return
                        time.sleep(1)
        
        # Check once more before final message
        if _SHUTDOWN_EVENT.is_set():
            LOG.debug(f"Final message skipped for ${cmd} in {channel} (shutdown)")
            return
        
        bot.say(random.choice(final_messages), channel)
        LOG.debug(f"Completed countdown for ${cmd} in {channel}")
    
    except (IOError, OSError) as e:
        LOG.warning(f"Network error during ${cmd} countdown in {channel}: {e}")
    except Exception as e:
        LOG.error(f"Unexpected error in ${cmd} countdown for {channel}: {e}", exc_info=True)
    finally:
        # Deregister this thread
        with _THREAD_LOCK:
            _ACTIVE_THREADS.discard(current_thread)


def _cleanup_threads():
    """Signal all active threads to stop and wait for them to finish."""
    LOG.debug(f"Cleaning up {len(_ACTIVE_THREADS)} active countdown threads")
    _SHUTDOWN_EVENT.set()
    
    # Wait up to 5 seconds for all threads to finish
    timeout = 5
    start_time = time.time()
    while _ACTIVE_THREADS and (time.time() - start_time) < timeout:
        time.sleep(0.1)
    
    if _ACTIVE_THREADS:
        LOG.warning(f"{len(_ACTIVE_THREADS)} threads did not complete within {timeout}s")
    else:
        LOG.debug("All countdown threads completed gracefully")


def _get_grok_db_path(bot):
    """Retrieve the grok.sqlite3 path from bot.memory or standard location (ibot-safe)."""
    core = getattr(bot, '_bot', bot)
    if hasattr(core, 'memory') and core.memory.get('grok_db_path'):
        return core.memory.get('grok_db_path')
    if hasattr(bot, 'memory') and bot.memory.get('grok_db_path'):
        return bot.memory.get('grok_db_path')
    base_dir = os.environ.get('AI_GROK_DIR') or os.path.join(os.path.dirname(__file__), 'grok_data')
    return os.path.join(base_dir, 'grok.sqlite3')


def _get_effects_cache(bot):
    """Retrieve or create the grok_channel_effects dict across Sopel / ibot."""
    core = getattr(bot, '_bot', bot)
    if hasattr(core, 'memory'):
        return core.memory.setdefault('grok_channel_effects', {})
    if hasattr(bot, 'memory'):
        return bot.memory.setdefault('grok_channel_effects', {})
    return {}


def _apply_bot_intoxication(bot, channel, effect, giver, item_name):
    """Set or stack intoxication effect for the channel in ai-multi's database and memory."""
    if not channel or not str(channel).startswith('#'):
        return 0, 1
    chan_key = str(channel).lower()
    now = time.time()
    duration_secs = 3600  # 1 hour base per hit

    cache = _get_effects_cache(bot)
    chan_cache = cache.setdefault(chan_key, {})
    existing = chan_cache.get(effect)
    if existing and existing.get('expires_at', 0) > now:
        new_expires = min(existing['expires_at'] + 1800, now + 10800)  # +30 mins, max 3 hours
        intensity = min(existing.get('intensity', 1) + 1, 5)
    else:
        new_expires = min(now + duration_secs, now + 10800)
        intensity = 1

    effect_data = {
        'effect': effect,
        'expires_at': new_expires,
        'started_at': now,
        'giver': giver,
        'item_name': item_name,
        'intensity': intensity,
    }
    chan_cache[effect] = effect_data
    chan_cache.pop('sobered', None)

    db_path = _get_grok_db_path(bot)
    try:
        if os.path.exists(os.path.dirname(db_path)):
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS grok_channel_effects (
                        channel TEXT NOT NULL,
                        effect TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        started_at REAL NOT NULL,
                        giver TEXT,
                        item_name TEXT,
                        intensity INTEGER DEFAULT 1,
                        PRIMARY KEY (channel, effect)
                    )
                ''')
                conn.execute('DELETE FROM grok_channel_effects WHERE channel = ? AND effect = ?', (chan_key, 'sobered'))
                conn.execute('''
                    INSERT OR REPLACE INTO grok_channel_effects
                    (channel, effect, expires_at, started_at, giver, item_name, intensity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (chan_key, effect, new_expires, now, giver, item_name, intensity))
                conn.commit()
    except Exception as e:
        LOG.exception("Failed to write intoxication effect to DB: %s", e)

    remaining_secs = max(0, new_expires - now)
    return remaining_secs, intensity


def _clear_bot_intoxication(bot, channel, effect=None, giver=None, item_name='splashing cold water on its face'):
    """Clear active bot intoxication effects for the channel and record sobering transition."""
    if not channel:
        return False
    chan_key = str(channel).lower()
    now = time.time()
    giver = giver or 'someone'
    cleared = False
    
    cache = _get_effects_cache(bot)
    chan_cache = cache.get(chan_key)
    if chan_cache is None:
        chan_cache = {}
        cache[chan_key] = chan_cache

    # If cache is missing effects, try loading active effects from DB
    if not chan_cache:
        db_path = _get_grok_db_path(bot)
        if os.path.exists(db_path):
            try:
                with sqlite3.connect(db_path, timeout=5.0) as conn:
                    conn.execute('DELETE FROM grok_channel_effects WHERE expires_at <= ?', (now,))
                    cur = conn.execute(
                        'SELECT effect, expires_at, started_at, giver, item_name, intensity '
                        'FROM grok_channel_effects WHERE channel = ? AND expires_at > ?',
                        (chan_key, now)
                    )
                    for row in cur.fetchall():
                        eff_name = row[0]
                        chan_cache[eff_name] = {
                            'effect': eff_name,
                            'expires_at': row[1],
                            'started_at': row[2],
                            'giver': row[3],
                            'item_name': row[4],
                            'intensity': row[5],
                        }
            except Exception:
                pass

    active_effects = [e for e in ('stoned', 'drunk', 'tripping') if e in chan_cache and chan_cache[e].get('expires_at', 0) > now]
    if not active_effects:
        return False

    prev_drunk = chan_cache.get('drunk')
    prev_stoned = chan_cache.get('stoned')
    prev_tripping = chan_cache.get('tripping')

    if 'drunk' in active_effects and 'stoned' in active_effects:
        prev_effect = 'crossfaded (both baked and drunk)'
        prev_giver = f"{prev_stoned.get('giver', 'someone')} and {prev_drunk.get('giver', 'someone')}"
        prev_item = f"{prev_stoned.get('item_name', 'weed')} and {prev_drunk.get('item_name', 'drinks')}"
    elif 'stoned' in active_effects:
        prev_effect = 'baked and high on weed'
        prev_giver = prev_stoned.get('giver', 'someone')
        prev_item = prev_stoned.get('item_name', 'weed')
    elif 'drunk' in active_effects:
        prev_effect = 'drunk'
        prev_giver = prev_drunk.get('giver', 'someone')
        prev_item = prev_drunk.get('item_name', 'alcohol')
    else:
        prev_effect = 'tripping on psychedelics'
        prev_giver = prev_tripping.get('giver', 'someone')
        prev_item = prev_tripping.get('item_name', 'psychedelics')

    # Remove active effects from cache
    for eff in ('stoned', 'drunk', 'tripping'):
        chan_cache.pop(eff, None)

    sober_payload = {
        'prev_effect': prev_effect,
        'prev_giver': prev_giver,
        'prev_item': prev_item,
        'sober_giver': giver,
        'sober_item': item_name,
    }
    sober_json = json.dumps(sober_payload)
    chan_cache['sobered'] = {
        'effect': 'sobered',
        'expires_at': now + 3600,
        'started_at': now,
        'giver': giver,
        'item_name': sober_json,
        'intensity': 0,
        **sober_payload
    }

    db_path = _get_grok_db_path(bot)
    try:
        if os.path.exists(db_path):
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute('DELETE FROM grok_channel_effects WHERE channel = ? AND effect IN ("stoned", "drunk", "tripping")', (chan_key,))
                conn.execute('''
                    INSERT OR REPLACE INTO grok_channel_effects
                    (channel, effect, expires_at, started_at, giver, item_name, intensity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (chan_key, 'sobered', now + 3600, now, giver, sober_json, 0))
                conn.commit()
                cleared = True
    except Exception as e:
        LOG.exception("Failed to clear intoxication effect from DB: %s", e)

    return True


BOT_RECEPTION_ACTIONS = [
    "happily accepts {gift} from {sender}, takes a quick rip 💨 and passes it right back!",
    "thanks {sender}, takes a fat hit of {gift} 🌿💨 and passes it back to {sender}",
    "grabs {gift} from {sender}, takes a puff ☁️ and hands it right back!",
    "sparks up {gift} courtesy of {sender} 🔥 and passes it back to {sender}",
    "takes a smooth hit of {gift} 🫧 and nudges it back to {sender}",
]


@module.commands('weed', 'bong', 'joint', 'jay', 'doobie', 'spliff', 'keef', 'kief', 'trip', 'shrooms', 'mushrooms',
                 'acid', 'lsd', 'peyote', 'mescaline', 'toke', 'edibles', 'edible',
                 'dab', 'dabs', 'blunt', 'vape', 'hash', 'munchies', 'w33d')
@module.example('$weed username', 'Give a user a random weed item/message')
def weed_commands(bot, trigger):
    """Send a lighthearted smoking message with cooldowns.

    If a `target` is given, send an action handing them a random gift and return.
    Otherwise, perform a short non-blocking countdown and post a final message.
    """
    channel = trigger.sender
    if not str(channel).startswith('#'):
        bot.notice("These commands only work in channels.", trigger.nick)
        return

    now = time.time()
    user_id = trigger.account or trigger.nick
    cmd = trigger.group(1).lower()
    LAST_WEED_ITEM[str(channel).lower()] = (cmd, now)

    gifts, action_msgs, final_msgs, countdown_msgs = DATA.get(cmd, DATA['weed'])

    # Shared cooldown key for all commands
    key = (channel, user_id)

    # If a target user is specified, give them a random item
    target_arg = trigger.group(2).strip() if trigger.group(2) else None
    if target_arg:
        target_user = target_arg.split()[0]
        chan_obj = bot.channels.get(str(channel))
        if chan_obj is not None:
            channel_users = [u.lower() for u in chan_obj.users.keys()]
            if target_user.lower() not in channel_users:
                # Tell them instead of silently burning the channel countdown cooldown
                notice_msg = f"{target_user} isn't in the channel."
                if cmd == 'w33d':
                    notice_msg = _to_1337(notice_msg)
                bot.notice(notice_msg, trigger.nick)
                return

        # Per-user cooldown (atomic check-and-claim)
        remaining = _cooldown_check_and_set(PER_USER_LAST, key, PER_USER_COOLDOWN, now)
        if remaining > 0:
            notice_msg = f"You must wait {_format_remaining(remaining)} before giving {cmd} again in {channel}."
            if cmd == 'w33d':
                notice_msg = _to_1337(notice_msg)
            bot.notice(notice_msg, trigger.nick)
            return

        gift = random.choice(gifts)
        bot_nick = (getattr(bot, 'nick', '') or '').lower()
        if target_user.lower() == bot_nick:
            is_trippy = cmd in ('trip', 'shrooms', 'mushrooms', 'acid', 'lsd', 'peyote', 'mescaline')
            effect = 'tripping' if is_trippy else 'stoned'
            rem_secs, intensity = _apply_bot_intoxication(bot, channel, effect, trigger.nick, gift)
            mins = max(1, int(round(rem_secs / 60)))
            base_act = random.choice(BOT_RECEPTION_ACTIONS).format(sender=trigger.nick, gift=gift)
            if is_trippy:
                tag = f"🍄✨ (Tripping in {channel} for ~{mins}m)"
            else:
                baked_tag = "baked" if intensity == 1 else ("blazed" if intensity == 2 else "zooted to another dimension")
                tag = f"🌿😵💨 ({bot.nick} is {baked_tag} in {channel} for ~{mins}m)"
            full_act = f"{base_act} {tag}"
            if cmd == 'w33d':
                full_act = _to_1337(full_act)
            bot.action(full_act)
        else:
            template = random.choice(action_msgs)
            act_text = template.format(target=target_user, gift=gift)
            if cmd == 'w33d':
                act_text = _to_1337(act_text)
            bot.action(act_text)
        LOG.debug(f"${cmd} gift to {target_user} in {channel} by {user_id}")
        return

    # Channel countdown cooldown (atomic check-and-claim)
    remaining = _cooldown_check_and_set(LAST_USED, channel, COOLDOWN, now)
    if remaining > 0:
        bot.notice(f"The countdown is on cooldown for {_format_remaining(remaining)} in {channel}.", trigger.nick)
        return

    _start_countdown(bot, channel, cmd, countdown_msgs, final_msgs)


# Trigger when $command appears mid-sentence. Built from DATA so new commands
# are picked up automatically. The ^(?!\$) guard means messages that START
# with a command prefix are left to @module.commands — otherwise a message
# like "$bong pass the $weed" would fire both the command AND this rule.
_INLINE_PATTERN = (
    r'^(?!\$).+\$(?P<incmd>'
    + '|'.join(sorted(DATA.keys(), key=len, reverse=True))
    + r')\b'
)


@module.rule(_INLINE_PATTERN)
def weed_inline(bot, trigger):
    """Fire the weed countdown when $command appears mid-sentence."""
    if not trigger.sender.startswith('#'):
        return

    cmd = trigger.match.group('incmd').lower()
    channel = trigger.sender
    now = time.time()
    user_id = trigger.account or trigger.nick

    gifts, action_msgs, final_msgs, countdown_msgs = DATA.get(cmd, DATA['weed'])

    remaining = _cooldown_check_and_set(LAST_USED, channel, COOLDOWN, now)
    if remaining > 0:
        bot.notice(f"The {cmd} countdown is on cooldown for {_format_remaining(remaining)} in {channel}.", trigger.nick)
        return

    _start_countdown(bot, channel, cmd, countdown_msgs, final_msgs, source='inline')
    LOG.debug(f"Inline ${cmd} triggered in {channel} by {user_id}")


# =======================
# PASS Command
# =======================
# Last weed item rolled/lit per channel, so $pass hands over the right thing.
LAST_WEED_ITEM = {}
PASS_ITEM_NAMES = {'joint': 'joint', 'jay': 'joint', 'doobie': 'doobie', 'spliff': 'spliff', 'blunt': 'blunt',
                   'bong': 'bong', 'toke': 'bowl', 'vape': 'vape', 'dab': 'dab rig', 'dabs': 'dab rig'}

PASS_ACTIONS = [
    "takes a fat rip from a bong 🫧💨 and passes it to {target}",
    "hits the joint, holds it… exhales a cloud ☁️ and slides it to {target}",
    "sparks a bowl, takes a deep toke 🔥💨 and hands the pipe to {target}",
    "lights a blunt, puffs twice 🌿🔥 and passes it left to {target}",
    "torches a one-hitter 🎯💨 then packs a fresh one for {target}",
    "milks the bong until it's white 🥛💨 clears it… and hands it to {target}",
    "pulls from the chillum 🪈💨 and passes the peace pipe to {target}",
    "takes a long drag off a spliff 🚬☁️ and offers it to {target}",
    "rips the bubbler 🫧🔥 coughs a little… passes it to {target}",
    "corners the bowl perfectly 🌿🔥 takes a smooth hit and nudges the pipe to {target}",
]

BOT_PASS_ACTIONS = [
    "grabs the chillum for a quick rip 🪈💨 and passes the peace pipe back to {sender}",
    "takes a fat rip from the bong 🫧💨 thanks {sender}, and passes it back!",
    "hits the joint, holds it… exhales a cloud ☁️ and passes it right back to {sender}",
    "sparks up the bowl, takes a deep toke 🔥💨 and hands the pipe back to {sender}",
    "lights the blunt, puffs twice 🌿🔥 and passes it back to {sender}",
    "torches the one-hitter 🎯💨 then packs a fresh hit and passes it to {sender}",
    "milks the bong until it's white 🥛💨 clears it… and hands it back to {sender}",
    "takes a long drag off the spliff 🚬☁️ and offers it back to {sender}",
    "rips the bubbler 🫧🔥 coughs a little… and passes it back to {sender}",
    "corners the bowl perfectly 🌿🔥 takes a smooth hit and nudges the pipe back to {sender}",
]


@module.commands('pass')
@module.example('$pass username', 'Take a hit and pass it to someone')
def pass_command(bot, trigger):
    """Take a hit and pass something to a user."""
    parts = (trigger.group(2) or '').split()
    target = parts[0] if parts else ''
    asked_item = ' '.join(parts[1:]).lower()
    if not target:
        bot.notice("Usage: $pass <nick> [joint|bong|blunt|...]", trigger.nick)
        return

    channel = trigger.sender
    if not channel.startswith('#'):
        bot.notice("$pass only works in channels.", trigger.nick)
        return

    # Check target is in the channel
    chan_obj = bot.channels.get(str(channel))
    if chan_obj and target.lower() not in [u.lower() for u in chan_obj.users.keys()]:
        bot.notice(f"{target} isn't in the channel.", trigger.nick)
        return

    # Per-user cooldown
    now = time.time()
    user_id = trigger.account or trigger.nick
    key = (channel, user_id, 'pass')
    remaining = _cooldown_check_and_set(PER_USER_LAST, key, PER_USER_COOLDOWN, now)
    if remaining > 0:
        bot.notice(f"Wait {_format_remaining(remaining)} before passing again.", trigger.nick)
        return

    bot_nick = (getattr(bot, 'nick', '') or '').lower()
    if target.lower() == bot_nick:
        rem_secs, intensity = _apply_bot_intoxication(bot, channel, 'stoned', trigger.nick, 'the peace pipe / joint')
        mins = max(1, int(round(rem_secs / 60)))
        base_act = random.choice(BOT_PASS_ACTIONS).format(sender=trigger.nick)
        baked_tag = "baked" if intensity == 1 else ("blazed" if intensity == 2 else "zooted to another dimension")
        bot.action(f"{base_act} 🌿😵💨 ({bot.nick} is {baked_tag} in {channel} for ~{mins}m)")
    else:
        last = LAST_WEED_ITEM.get(str(channel).lower())
        item = PASS_ITEM_NAMES.get(last[0]) if last and now - last[1] < 3600 else None
        if asked_item:
            item = PASS_ITEM_NAMES.get(asked_item, asked_item if asked_item in ('bowl', 'pipe', 'dab rig') else None) or item
        if item:
            bot.action(f"takes a long pull off the {item} and passes it to {target}")
        else:
            bot.action(random.choice(PASS_ACTIONS).format(target=target))


@module.commands('sober', 'unhigh')
@module.example('$sober glitchy', 'Sober up the bot from being high')
def sober_command(bot, trigger):
    """Sober up the bot or give another user coffee/water."""
    channel = trigger.sender
    if not str(channel).startswith('#'):
        bot.notice("This command only works in channels.", trigger.nick)
        return

    try:
        raw_target = trigger.group(2)
    except (IndexError, AttributeError):
        raw_target = None
    target = (raw_target or '').strip()
    bot_nick = (getattr(bot, 'nick', '') or '').lower()

    if target and target.lower() != bot_nick:
        bot.action(f"hands a fresh hot cup of coffee ☕ to {target} to help them sober up!")
        return

    cleared = _clear_bot_intoxication(bot, channel, giver=trigger.nick, item_name='splashing cold water on its face')
    if cleared:
        bot.action(f"splashes cold water on its face, shakes its head... 😳 Whew! 100% sober and back to earth thanks to {trigger.nick}.")
    else:
        bot.action(f"splashes cold water on its face... wasn't high or drunk anyway, but thanks {trigger.nick}!")


# =======================
# WEEDHELP Command
# =======================
@module.commands('weedhelp')
@module.example('$weedhelp', 'Get a PM listing all weed.py commands')
def weedhelp_command(bot, trigger):
    """PM the user a list of all available weed.py commands."""
    nick = trigger.nick
    lines = [
        formatting.bold("🌿 weed.py — Available Commands 🌿"),
        " ",
        formatting.bold("— Smoke Sesh Commands —"),
        "  $weed <nick>        — Give someone a random weed item",
        "  $w33d <nick>        — Give someone a random weed item (1337 speak)",
        "  $bong <nick>        — Give someone a bong rip",
        "  $joint <nick>       — Give someone a joint",
        "  $jay <nick>         — Give someone a jay",
        "  $doobie <nick>      — Give someone a doobie",
        "  $spliff <nick>      — Give someone a spliff",
        "  $blunt <nick>       — Give someone a blunt",
        "  $toke <nick>        — Give someone a bowl/pipe toke",
        "  $vape <nick>        — Give someone a vape hit",
        "  $dab / $dabs <nick> — Give someone a dab",
        "  $hash <nick>        — Give someone some hash",
        "  $keef / $kief <nick> — Give someone some keef",
        " ",
        formatting.bold("— Edibles & Munchies —"),
        "  $edible / $edibles <nick> — Give someone an edible",
        "  $munchies <nick>          — Give someone snacks",
        " ",
        formatting.bold("— Psychedelic Commands —"),
        "  $trip <nick>              — Give someone a DMT experience",
        "  $shrooms / $mushrooms <nick> — Give someone shrooms",
        "  $acid / $lsd <nick>       — Give someone acid",
        "  $peyote / $mescaline <nick> — Give someone peyote",
        " ",
        formatting.bold("— Other Commands —"),
        "  $pass <nick>        — Take a hit and pass it to someone (pass to bot to get it baked!)",
        "  $sober              — Sober the bot up from being high",
        "  $weedhelp           — Show this help message",
        " ",
        formatting.bold("— How It Works —"),
        "  With <nick>: gives that user a random item (30s per-user cooldown)",
        "  Targeting the bot: the bot takes a hit and acts stoned/tripping in the channel for 1–2 hours!",
        "  Without <nick>: triggers a channel countdown (20min cooldown)",
        "  Inline $command mid-sentence also triggers the countdown!",
    ]
    for line in lines:
        bot.notice(line, nick)


# Register cleanup handler for graceful shutdown
atexit.register(_cleanup_threads)
