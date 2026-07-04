# SMC Trading Methodology Definitions

## Core Concepts

### IDM (Inducement)
The last pullback/swing before a structural shift (BOS/CHOCH).
- **Valid IDM**: Must be taken (swept) before price reaches the POI
- **IDM Sweep**: Price wicks through the IDM low/high, then reverses
- **Invalidation**: POI broken before IDM is swept

### POI (Point of Interest)
Refined entry zones — either an Order Block or Fair Value Gap:
- **Order Block (OB)**: Last bearish candle before a bullish BOS (bullish OB), or last bullish candle before a bearish BOS (bearish OB)
- **FVG (Fair Value Gap)**: 3-candle imbalance where candle 1 high and candle 3 low don't overlap (bullish), or candle 1 low and candle 3 high don't overlap (bearish)
- **Refined POI**: OB + FVG overlap zone (highest probability)

### SMT (Smart Money Technique / Divergence)
Divergence between correlated pairs indicating liquidity manipulation:
- **Bullish SMT**: EURUSD makes lower low but DXY fails to make higher high (or vice versa)
- **Bearish SMT**: EURUSD makes higher high but DXY fails to make lower low
- **Confirmation**: Used as additional confluence at POI entry

### BOS (Break of Structure)
Price closes beyond the previous swing high (bullish BOS) or swing low (bearish BOS).
- Indicates shift in market structure
- Must be a **close** beyond structure, not just a wick

### CHOCH (Change of Character)
First BOS against the prevailing trend — signals potential reversal.

### PDH/PDL (Previous Day High / Previous Day Low)
Liquidity pools resting above PDH or below PDL.
- **PDH Sweep**: Price wicks above PDH then reverses — bearish
- **PDL Sweep**: Price wicks below PDL then reverses — bullish

---

## A+ Setup Criteria

A trade qualifies as A+ when ALL of the following are met:

1. **Session**: NY Open (9:30–11:00 EST) or London Open (7:00–10:00 GMT) only
2. **Liquidity Sweep**: PDH or PDL liquidity taken (wick above/below, then close back inside)
3. **IDM Sweep**: After PDH/PDL sweep, price sweeps the IDM (last pullback low/high)
4. **POI Entry**: Price returns to a confirmed Order Block or FVG
5. **SMT Confluence** (optional but preferred): DXY divergence at the POI
6. **Confirmation**: 5m or 15m candle close back inside the OB/FVG

## Risk Parameters
- SL: Below the wick of the POI candle (+ small buffer)
- TP: Next liquidity pool (opposing PDH/PDL, equal highs/lows)
- Min RR: 1:2
