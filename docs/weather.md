# 🌤️ Weather (weather)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Powered by PirateWeather API. Register your location for quick lookups.

---

## Setup

**1. Install dependencies:**
```bash
pip install requests
```

**2. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/weather.py
```

**3. Get a PirateWeather API key:**
- Sign up at [pirateweather.net](https://pirateweather.net)
- The API key is set as `WEATHER_API_KEY` at the top of `weather.py`
- Edit the variable directly in the script:
```python
WEATHER_API_KEY = "your-api-key-here"
```

**Data file:** User-registered locations are stored in `~/.sopel/weather_locations.json` (created automatically).

---

## Commands

| Command | Description |
|---------|-------------|
| `$w <location>` | Current weather for a location |
| `$w` | Current weather for your saved location |
| `$w -n <user>` | Current weather for another user's location |
| `$f <location>` | 3-day forecast |
| `$ef <location>` | Extended 8-day forecast (PM) |
| `$wa` | Weather alerts for your location (PM) |
| `$wa -n <user>` | Weather alerts for another user's location |
| `$space` / `$spaceweather` | Space weather report |
| `$register_location <location>` | Register your default location |
| `$change_location <location>` | Change your saved location |
| `$unregister_location` | Remove your saved location |
| `$helpweather` | Full help (PM) |

---

## Examples

**Current weather by location:**
```
<User> $w New York
<Glitchy> 🌤️ New York, NY: 72°F (22°C) | Partly Cloudy | Humidity: 55% | Wind: 8 mph SW
```

**Current weather with saved location:**
```
<User> $w
<Glitchy> 🌤️ Boston, MA: 65°F (18°C) | Clear | Humidity: 40% | Wind: 12 mph NW
```

**Check another user's weather:**
```
<User> $w -n Friend
<Glitchy> 🌤️ London, UK: 15°C (59°F) | Overcast | Humidity: 78% | Wind: 15 mph W
```

**3-day forecast:**
```
<User> $f Chicago
<Glitchy> 📅 Chicago, IL — Mon: 68°F ⛅ | Tue: 72°F ☀️ | Wed: 61°F 🌧️
```

**Extended forecast (sent via PM):**
```
<User> $ef Seattle
```

**Weather alerts:**
```
<User> $wa
<Glitchy> (via PM) ⚠️ Severe Thunderstorm Warning until 8:00 PM EDT...
```

**Space weather:**
```
<User> $space
<Glitchy> 🌌 Space Weather: Kp Index: 3 (Quiet) | Solar Wind: 380 km/s | X-ray Flux: B4.2
```

**Register your location:**
```
<User> $register_location Boston, MA
<Glitchy> ✅ Location registered: Boston, MA. Use $w with no arguments for quick lookups!
```

**Change location:**
```
<User> $change_location Portland, OR
<Glitchy> ✅ Location updated to Portland, OR.
```

**Remove location:**
```
<User> $unregister_location
<Glitchy> ✅ Your saved location has been removed.
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
