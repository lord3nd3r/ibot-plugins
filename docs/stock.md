# 📈 Stocks (stock)

> **Works with [ibot](https://github.com/lord3nd3r/ibot) (recommended) · Sopel-compatible.** Examples use the `$` prefix; ibot's default is `.`. To load these plugins, see the [Quick start](../README.md#-quick-start-with-ibot).

Look up stocks by ticker symbol or company name. Shows current price with multi-period trend indicators.

---

## Setup

**1. Install dependencies:**
```bash
pip install yfinance
```

**2. Place the script** in the plugin folder your ibot `[core] extra` points to (`~/.sopel/scripts` here):
```
~/.sopel/scripts/stock.py
```

**No config section needed.** Uses the free Yahoo Finance API via `yfinance` — no API key required.

---

## Commands

| Command | Description |
|---------|-------------|
| `$stock <symbol or name>` | Look up a stock |

---

## How It Works

1. **Ticker lookup**: Tries the input as a direct ticker symbol first (e.g. `AAPL`).
2. **Name fallback**: If that fails, searches by company name and grabs the first matching ticker.
3. **Multi-period trends**: Calculates percentage change for **5 time periods**: 24h, 7d, 30d, 6m, and 1y.
4. **Trend indicators**: Each period gets a colored emoji:
   - 🟢 — price went **up** in that period
   - 🔴 — price went **down** in that period
   - ⚪ — data not available for that period

### Limits

- One `$stock` per user every 15 seconds and one per channel every 5 seconds (extra requests are ignored silently; admins are exempt).
- Queries must be letters, digits, spaces and `.-^=&'` only, up to 40 characters (otherwise: `❌ Invalid stock query.`).
- Each lookup has a 20-second budget (`❌ Stock lookup for '…' timed out.`). At most 2 lookups run at once (`⏳ Stock lookups are busy, try again shortly.`).
- Errors from Yahoo Finance are logged; the channel only sees a short generic message.

### Output Format

```
📈 Company Name (TICKER)  💵 $PRICE  🟢24h +X.XX%  🔴7d -X.XX%  🟢30d +X.XX%  🟢6m +X.XX%  🟢1y +X.XX%
```

The currency symbol adapts to the stock's market (e.g. `$` for USD, `€` for EUR, `£` for GBP). Prices include thousands separators.

---

## Examples

**Look up by ticker:**
```
<User> $stock AAPL
<Glitchy> 📈 Apple Inc. (AAPL)  💵 $189.84  🟢24h +0.65%  🔴7d -1.20%  🟢30d +3.41%  🟢6m +8.92%  🟢1y +22.15%
```

**Look up by company name:**
```
<User> $stock Microsoft
<Glitchy> 📈 Microsoft Corp. (MSFT)  💵 $378.91  🔴24h -0.55%  🟢7d +1.10%  🟢30d +4.23%  🟢6m +12.30%  🟢1y +18.75%
```

**Volatile stock with mixed trends:**
```
<User> $stock Tesla
<Glitchy> 📈 Tesla Inc. (TSLA)  💵 $248.42  🟢24h +2.33%  🔴7d -4.50%  🔴30d -8.12%  🟢6m +15.60%  🟢1y +45.20%
```

**International stock:**
```
<User> $stock Toyota
<Glitchy> 📈 Toyota Motor Corp. (TM)  💵 $182.50  🟢24h +0.30%  🟢7d +2.10%  ⚪30d N/A  🟢6m +5.40%  🟢1y +10.80%
```

**Unknown ticker:**
```
<User> $stock XYZNOTREAL
<Glitchy> ❌ Could not find stock data for "XYZNOTREAL".
```

---

*Using Sopel instead? This plugin should also work there: put it in `~/.sopel/scripts/` and any config section in `~/.sopel/default.cfg`. See [Using with Sopel](../README.md#-using-with-sopel).*
