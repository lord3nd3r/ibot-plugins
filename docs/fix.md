# 🔧 Typo Correction (fix)

Full-featured IRC sed correction plugin for Sopel. It allows users to fix typos in their own last messages or suggest corrections for others using standard `s/pattern/replacement/` syntax.

---

## Setup

**1. Place the script:**
```
~/.sopel/scripts/fix.py
```

**2. Behavior:**
* The plugin automatically records a running history of the last 20 messages for every user in each channel.
* It patches the bot's sending layer so the bot's own messages are recorded and correctable.
* Rate-limited to 4 seconds between corrections per user per channel to prevent spam.

---

## Usage Syntax

```
[nick[,: ]]s<delim>pattern<delim>replacement[<delim>[flags]]
```

* **Delimiter**: Any non-alphanumeric, non-space character (e.g., `/`, `|`, `!`, `@`, `#`).
* **Flags**:
  * `g` (global): Replace all occurrences instead of just the first one.
  * `i` (case-insensitive): Ignore case during match.
  * `1-9` (Nth occurrence): Replace only the Nth match of the pattern.
* **Pattern**: Full Python regular expression. Supports backreferences (e.g. `\1`, `\2`) and the `&` symbol (which represents the entire matched pattern) in the replacement text.

---

## Examples

### 1. Correcting Your Own Last Message
If you typo'd a word:
```
<User> I love coding in Pythno!
<User> s/Pythno/Python
<Glitchy> User meant to say: I love coding in Python!
```

### 2. Correcting Another User
If you want to point out someone else's typo:
```
<Friend> That movie was gr8t!
<User> Friend: s/gr8t/great
<Glitchy> User thinks Friend meant to say: That movie was great!
```

### 3. Global Replacement
Using the `g` flag:
```
<User> bad code, bad logic, bad tests
<User> s/bad/good/g
<Glitchy> User meant to say: good code, good logic, good tests
```

### 4. Custom Delimiter & Backreferences
If your pattern contains forward slashes, you can use a different delimiter (like `|` or `#`):
```
<User> Check out http://google.com
<User> s|http://google.com|https://google.com|
<Glitchy> User meant to say: Check out https://google.com
```

Using capture groups and backreferences:
```
<User> edit the file source.py now
<User> s|(source\.py)|[\1]|
<Glitchy> User meant to say: edit the file [source.py] now
```

---

## Safety Controls
* **Pattern Length Limit**: Maximum pattern length is 200 characters.
* **ReDoS protection**: Plain-text patterns (no regex special characters) are matched directly. Real regex patterns are run in a short-lived separate Python process that is killed after **2 seconds**, so a pathological pattern like `s/(a+)+$/x/` can't freeze the bot. At most 2 regex corrections run at once; if one times out or the bot is busy, nothing is said.
* **Search depth**: A correction searches at most the last 200 remembered messages.
* **Output Truncation**: Corrected messages are capped at 420 characters to comply with IRC protocol limits.
