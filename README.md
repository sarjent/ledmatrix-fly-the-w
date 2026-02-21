# Fly the W — Chicago Cubs Win Celebration Plugin

A plugin for the [LEDMatrix](https://github.com/your-org/LEDMatrix) project that celebrates Chicago Cubs wins on your Raspberry Pi LED matrix display.

When the Cubs win, the display immediately shows an animated waving "W" flag in Cubs blue and red, with "CUBS WIN!" text and the final score overlay. The celebration window is fully configurable (default: 1 hour after the final whistle).

## Features

- Automatically detects Cubs wins via the free ESPN MLB API (no API key required)
- Animated waving W flag drawn entirely with PIL — no external GIF files needed
- Displays immediately after a game goes final (`live_priority` support)
- Configurable celebration window (default 1 hour)
- Optional "CUBS WIN!" text and final score overlay
- Vegas scroll mode support (pauses the scroll while the flag is displayed)
- Respects the standard LEDMatrix plugin lifecycle (update / display / cleanup)

## Installation

### Option A — via `dev_plugin_setup.sh` (recommended)

```bash
cd /path/to/LEDMatrix
./dev_plugin_setup.sh link fly-the-w /path/to/ledmatrix-fly-the-w
```

### Option B — manual symlink

```bash
cd /path/to/LEDMatrix/plugins
ln -s /path/to/ledmatrix-fly-the-w fly-the-w
```

### Enable in config

Add the following to `config/config.json`:

```json
{
  "fly-the-w": {
    "enabled": true,
    "live_priority": true,
    "celebration_hours": 1.0,
    "display_duration": 30,
    "show_score": true,
    "show_text": true
  }
}
```

## Configuration Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable the plugin |
| `display_duration` | number | `30` | Seconds to show per rotation slot while celebrating |
| `update_interval` | number | `300` | Seconds between ESPN API polls |
| `celebration_hours` | number | `1.0` | Hours to celebrate after a Cubs win |
| `animation_fps` | number | `12.0` | Target frames per second for the flag wave |
| `show_score` | bool | `true` | Overlay the final score |
| `show_text` | bool | `true` | Show "CUBS WIN!" text |
| `font_name` | string | `4x6-font.ttf` | Font for text overlays |
| `font_size` | integer | `6` | Font size |
| `live_priority` | bool | `true` | Take over display immediately after a win |
| `vegas_mode` | string | `static` | Vegas scroll behavior (`static` or `fixed`) |

## How It Works

1. On each `update()` call (throttled by `update_interval`), the plugin fetches today's MLB scoreboard from the ESPN public API.
2. It scans for a game where the Chicago Cubs (`CHC`) appear as a competitor and the game state is `post` (final).
3. If the Cubs won, `celebrating` is set to `True` and an expiry timestamp is recorded.
4. While celebrating, `has_live_content()` returns `True`, which (with `live_priority: true`) causes the display controller to insert the plugin at high priority.
5. The `display()` method cycles through pre-rendered PIL animation frames at the configured FPS.
6. When the expiry window elapses, `celebrating` resets to `False` automatically.

## Vegas Scroll Mode

The plugin reports itself as `static` Vegas content when celebrating. This pauses the continuous scroll, plays the flag animation for `display_duration` seconds, then resumes scrolling. Change `vegas_mode` to `fixed` if you prefer the flag to scroll by as a block instead.

## Data Source

- **ESPN MLB Scoreboard API** — `https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard`
- Public, no authentication required.
- Polled at `update_interval` (default every 5 minutes).

## Requirements

- Python ≥ 3.9
- Pillow ≥ 9.0
- requests ≥ 2.28
- pytz ≥ 2022.1

All dependencies are bundled with the LEDMatrix core environment on Raspberry Pi.

## License

MIT
