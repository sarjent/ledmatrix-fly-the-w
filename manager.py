"""
Fly the W - Chicago Cubs Win Celebration Plugin for LEDMatrix

Displays an animated "W" flag on the LED matrix immediately after the Chicago
Cubs win a game. The celebration remains visible for a user-configurable
duration (default 1 hour) after the game ends.

Supports Vegas scroll mode.

API Version: 1.0.0
"""

import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode
from src.common import APIHelper

# ESPN MLB scoreboard endpoint (no API key required)
ESPN_MLB_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"

# Bundled GIF file (lives alongside manager.py)
GIF_FILE = Path(__file__).parent / "fly-the-w.gif"

# Chicago Cubs team abbreviation in ESPN data
CUBS_ABBR = "CHC"

# Cubs brand colors
CUBS_BLUE = (14, 51, 134)    # Wrigley blue
CUBS_RED = (204, 52, 51)     # Pinstripe red
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GOLD = (255, 215, 0)


class FlyTheWPlugin(BasePlugin):
    """
    Chicago Cubs 'Fly the W' celebration plugin.

    Monitors the MLB scoreboard via the ESPN API and activates immediately
    when the Cubs win a game. The animated W flag is displayed for a
    configurable window (default 1 hour) after the game becomes final.

    Configuration options:
        enabled (bool): Enable/disable plugin (default: true)
        display_duration (float): Seconds to show the plugin per rotation slot (default: 30)
        update_interval (int): API poll interval in seconds (default: 300)
        celebration_hours (float): Hours to celebrate after a win (default: 1.0)
        animation_fps (float): Target frames per second for flag wave (default: 12.0)
        show_score (bool): Overlay the final score on the display (default: true)
        show_text (bool): Overlay "CUBS WIN!" text (default: true)
    """

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
        plugin_manager: Any,
    ) -> None:
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        self.display_width: int = display_manager.matrix.width
        self.display_height: int = display_manager.matrix.height

        self.api_helper = APIHelper(cache_manager=cache_manager, logger=self.logger)

        self._load_config()
        self._load_fonts()

        # Win state
        self.celebrating: bool = False
        self.win_expires_at: Optional[datetime] = None
        self.last_win_score: str = ""
        self.last_update: Optional[datetime] = None
        self._win_info: Dict[str, Any] = {}        # cubs_abbr, opp_abbr, cubs_score, opp_score

        # Live-priority: fires once per new win, then lets normal rotation take over
        self._live_priority_fired: bool = False

        # Animation state
        self._frames: List[Image.Image] = []
        self._frame_durations: List[float] = []   # per-frame delay in seconds
        self._frame_index: int = 0
        self._last_frame_time: float = 0.0

        # Flash state for "CUBS WIN!" text
        self._flash_on: bool = True
        self._flash_last_toggle: float = 0.0
        self._flash_period: float = 0.5           # seconds between flashes

        # Build animation frames once during init
        self._build_frames()

        self.logger.info("Fly the W plugin initialized (display %dx%d)", self.display_width, self.display_height)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        self.update_interval_seconds: int = int(self.config.get("update_interval", 300))
        self.celebration_hours: float = float(self.config.get("celebration_hours", 1.0))
        self.animation_fps: float = float(self.config.get("animation_fps", 12.0))
        self.show_score: bool = bool(self.config.get("show_score", True))
        self.show_text: bool = bool(self.config.get("show_text", True))
        self.font_name: str = self.config.get("font_name", "4x6-font.ttf")
        self.font_size: int = int(self.config.get("font_size", 6))
        self.simulate_win: bool = bool(self.config.get("simulate_win", False))

    def _load_fonts(self) -> None:
        try:
            font_path = Path("assets/fonts") / self.font_name
            if font_path.exists():
                self.font = ImageFont.truetype(str(font_path), self.font_size)
            else:
                self.font = ImageFont.load_default()
                self.logger.warning("Font %s not found, using default", self.font_name)
        except Exception as exc:
            self.logger.error("Error loading font: %s", exc)
            self.font = ImageFont.load_default()

    # ------------------------------------------------------------------
    # Animation frame generation
    # ------------------------------------------------------------------

    def _build_frames(self) -> None:
        """
        Load animation frames from the bundled GIF file.
        Falls back to a programmatically generated waving flag if the GIF
        is missing.
        """
        if GIF_FILE.exists():
            if self._load_gif_frames():
                return
            self.logger.warning("GIF load failed, falling back to programmatic animation")
        else:
            self.logger.warning("GIF file not found at %s, using programmatic animation", GIF_FILE)

        self._build_programmatic_frames()

    def _load_gif_frames(self) -> bool:
        """
        Extract frames from fly-the-w.gif, resize them to fit the display,
        and optionally composite score/text overlays on top.

        Returns True on success, False on any error.
        """
        try:
            gif = Image.open(GIF_FILE)
            w, h = self.display_width, self.display_height

            frames: List[Image.Image] = []
            durations: List[float] = []

            for raw_frame in ImageSequence.Iterator(gif):
                # Grab per-frame duration (GIF stores it in milliseconds)
                duration_ms = raw_frame.info.get("duration", 100)
                durations.append(duration_ms / 1000.0)

                # Convert palette/transparency to RGBA so resize is clean
                frame_rgba = raw_frame.convert("RGBA")

                # Scale to fit the display (letterbox — preserve aspect ratio)
                frame_rgba.thumbnail((w, h), Image.Resampling.LANCZOS)

                # Center on a black RGB canvas
                canvas = Image.new("RGB", (w, h), BLACK)
                paste_x = (w - frame_rgba.width) // 2
                paste_y = (h - frame_rgba.height) // 2
                canvas.paste(frame_rgba, (paste_x, paste_y), frame_rgba)

                frames.append(canvas)

            if not frames:
                return False

            self._frames = frames
            self._frame_durations = durations
            self.logger.info(
                "Loaded %d frames from %s (display %dx%d)",
                len(frames), GIF_FILE.name, w, h,
            )
            return True

        except Exception as exc:
            self.logger.error("Error loading GIF: %s", exc, exc_info=True)
            return False

    def _draw_overlays(self, img: Image.Image) -> None:
        """
        Draw live overlays onto a frame copy:
        - "CUBS WIN!" centered at the top, flashing (gold when on, hidden when off)
        - Score right-aligned: cubs team abbr + score on top, opponent below
        """
        draw = ImageDraw.Draw(img)
        w, h = img.size

        # --- "CUBS WIN!" at top, flashing ---
        if self.show_text and self._flash_on:
            text = "CUBS WIN!"
            bbox = draw.textbbox((0, 0), text, font=self.font)
            text_w = bbox[2] - bbox[0]
            x = max(0, (w - text_w) // 2)
            # Black shadow for readability over the GIF
            self._draw_small_text(draw, text, x + 1, 2, BLACK)
            self._draw_small_text(draw, text, x, 1, GOLD)

        # --- Score on the right side ---
        if self.show_score and self._win_info:
            cubs_abbr  = self._win_info.get("cubs_abbr", "CHC")
            opp_abbr   = self._win_info.get("opp_abbr", "OPP")
            cubs_score = self._win_info.get("cubs_score", 0)
            opp_score  = self._win_info.get("opp_score", 0)

            line1 = f"{cubs_abbr} {cubs_score}"
            line2 = f"{opp_abbr} {opp_score}"

            b1 = draw.textbbox((0, 0), line1, font=self.font)
            b2 = draw.textbbox((0, 0), line2, font=self.font)
            w1, w2 = b1[2] - b1[0], b2[2] - b2[0]

            x1 = w - w1 - 1
            x2 = w - w2 - 1

            y1 = 1
            y2 = self.font_size + 2

            # Black shadow
            self._draw_small_text(draw, line1, x1 + 1, y1 + 1, BLACK)
            self._draw_small_text(draw, line2, x2 + 1, y2 + 1, BLACK)
            # Cubs score in white, opponent in red
            self._draw_small_text(draw, line1, x1, y1, WHITE)
            self._draw_small_text(draw, line2, x2, y2, CUBS_RED)

    def _build_programmatic_frames(self, num_frames: int = 16) -> None:
        """
        Fallback: generate a waving flag animation entirely with PIL.
        Uses a fixed fps derived from self.animation_fps.
        """
        self._frames = []
        frame_duration = 1.0 / max(self.animation_fps, 1.0)
        self._frame_durations = [frame_duration] * num_frames
        w, h = self.display_width, self.display_height

        for frame_idx in range(num_frames):
            img = self._render_flag_frame(w, h, frame_idx, num_frames)
            self._frames.append(img)

        self.logger.debug("Built %d programmatic frames (%dx%d)", num_frames, w, h)

    def _render_flag_frame(
        self, w: int, h: int, frame_idx: int, num_frames: int
    ) -> Image.Image:
        """Render a single waving-flag frame."""
        img = Image.new("RGB", (w, h), BLACK)

        phase = (2 * math.pi * frame_idx) / num_frames

        # --- Flag body -------------------------------------------------------
        # The flag occupies the left ~60 % of the display; right side has text.
        flag_w = int(w * 0.6)
        flag_h = int(h * 0.75)
        flag_top = (h - flag_h) // 2

        # Build flag column by column with a sine-wave vertical offset
        amplitude = max(1, flag_h // 8)
        for col in range(flag_w):
            # Wave increases from left (pole) to right (free end)
            wave_factor = col / max(flag_w - 1, 1)
            offset = int(amplitude * wave_factor * math.sin(phase + col * 0.3))
            col_top = flag_top + offset
            col_bot = col_top + flag_h

            # Split flag horizontally: top half blue, bottom half red
            mid = col_top + flag_h // 2
            for row in range(col_top, col_bot):
                if 0 <= row < h:
                    color = CUBS_BLUE if row < mid else CUBS_RED
                    img.putpixel((col, row), color)

        # --- "W" letter on the flag ------------------------------------------
        w_center_x = flag_w // 2
        w_center_y = flag_top + flag_h // 2
        wave_offset = int(amplitude * 0.5 * math.sin(phase + w_center_x * 0.3))
        self._draw_w(img, w_center_x, w_center_y + wave_offset)

        # --- Flag pole (1-pixel wide on the left) ----------------------------
        pole_x = 0
        for row in range(flag_top - 2, flag_top + flag_h + 2):
            if 0 <= row < h:
                img.putpixel((pole_x, row), WHITE)

        # --- Text overlay (right side of display) ----------------------------
        draw = ImageDraw.Draw(img)
        text_x = flag_w + 2

        if self.show_text:
            self._draw_small_text(draw, "CUBS", text_x, 2, GOLD)
            self._draw_small_text(draw, "WIN!", text_x, 2 + self.font_size + 1, WHITE)

        if self.show_score and self.last_win_score:
            score_y = h - self.font_size - 2
            self._draw_small_text(draw, self.last_win_score, text_x, score_y, WHITE)

        return img

    def _draw_w(self, img: Image.Image, cx: int, cy: int) -> None:
        """
        Draw a blocky white 'W' centered at (cx, cy) using putpixel.
        Scales with the display size so it's always readable.
        """
        scale = max(1, self.display_height // 16)
        pts = self._w_pixels(scale)
        for dx, dy in pts:
            x, y = cx + dx, cy + dy
            if 0 <= x < img.width and 0 <= y < img.height:
                img.putpixel((x, y), WHITE)

    @staticmethod
    def _w_pixels(scale: int = 1) -> List[Tuple[int, int]]:
        """
        Return a list of (dx, dy) offsets that form a 'W' shape.
        The letter fits in a ~7×5 pixel grid, scaled by `scale`.
        """
        pattern = [
            # Two downward strokes on the outside, two upward in the middle
            (0, 0), (0, 1), (0, 2), (0, 3),
            (1, 3), (1, 4),
            (2, 2), (2, 3),
            (3, 3), (3, 4),
            (4, 3), (4, 4),
            (5, 2), (5, 3),
            (6, 3), (6, 4),
            (7, 3),
            (8, 0), (8, 1), (8, 2), (8, 3),
        ]
        half_w = 4 * scale
        half_h = 2 * scale
        result = []
        for px, py in pattern:
            for sx in range(scale):
                for sy in range(scale):
                    result.append((px * scale + sx - half_w, py * scale + sy - half_h))
        return result

    def _draw_small_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        x: int,
        y: int,
        color: Tuple[int, int, int],
    ) -> None:
        """Draw text; clip gracefully if it exceeds the display width."""
        try:
            draw.text((x, y), text, font=self.font, fill=color)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Plugin lifecycle
    # ------------------------------------------------------------------

    def _trigger_simulation(self) -> None:
        """Activate a simulated Cubs win for testing purposes."""
        self.celebrating = True
        self.win_expires_at = datetime.now() + timedelta(hours=self.celebration_hours)
        self.last_win_score = "7-4"
        self._win_info = {
            "cubs_abbr":  "CHC",
            "opp_abbr":   "SIM",
            "cubs_score": 7,
            "opp_score":  4,
        }
        self._live_priority_fired = False   # Allow one-shot live takeover
        self._build_frames()
        self.logger.info(
            "Simulated Cubs win activated — celebrating for %.1f hours", self.celebration_hours
        )

    def update(self) -> None:
        """Poll the ESPN MLB API to detect a fresh Cubs win."""
        # Simulation mode — skip API and force celebration
        if self.simulate_win:
            if not self.celebrating:
                self._trigger_simulation()
            self._check_expiry()
            return

        # Throttle API calls to update_interval_seconds
        if self.last_update:
            elapsed = (datetime.now() - self.last_update).total_seconds()
            if elapsed < self.update_interval_seconds:
                self.logger.debug(
                    "Skipping update, last check was %.0fs ago", elapsed
                )
                # Still expire the celebration window if time ran out
                self._check_expiry()
                return

        try:
            data = self.api_helper.get(url=ESPN_MLB_URL)
            if data:
                self._process_scoreboard(data)
            else:
                self.logger.warning("No data returned from ESPN MLB API")
        except Exception as exc:
            self.logger.error("Error during update: %s", exc, exc_info=True)

        self.last_update = datetime.now()
        self._check_expiry()

    def _process_scoreboard(self, data: Dict[str, Any]) -> None:
        """
        Scan today's MLB scoreboard for a final Cubs win.

        Sets self.celebrating = True and records win expiry when a new win
        is detected. Existing celebrations are NOT reset if no new win is
        found (the expiry window handles cleanup).
        """
        events = data.get("events", [])
        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue

            competition = competitions[0]
            status = competition.get("status", {})
            state = status.get("type", {}).get("state", "")

            # Only care about completed games
            if state != "post":
                continue

            competitors = competition.get("competitors", [])
            home_team = next(
                (c for c in competitors if c.get("homeAway") == "home"), None
            )
            away_team = next(
                (c for c in competitors if c.get("homeAway") == "away"), None
            )

            if not home_team or not away_team:
                continue

            home_abbr = home_team.get("team", {}).get("abbreviation", "")
            away_abbr = away_team.get("team", {}).get("abbreviation", "")
            home_score = int(home_team.get("score", 0) or 0)
            away_score = int(away_team.get("score", 0) or 0)

            # Is this a Cubs game?
            if CUBS_ABBR not in (home_abbr, away_abbr):
                continue

            # Did the Cubs win?
            if home_abbr == CUBS_ABBR:
                cubs_won = home_score > away_score
                cubs_score, opp_score = home_score, away_score
                opp_abbr = away_abbr
            else:
                cubs_won = away_score > home_score
                cubs_score, opp_score = away_score, home_score
                opp_abbr = home_abbr

            if not cubs_won:
                self.logger.debug("Cubs lost to %s (%d-%d) — no celebration", opp_abbr, cubs_score, opp_score)
                continue

            # Build score string
            score_str = f"{cubs_score}-{opp_score}"

            # Only trigger a fresh celebration if we haven't already started one
            # for this game result (avoid re-triggering on every poll).
            if not self.celebrating or self.last_win_score != score_str:
                self.celebrating = True
                self.win_expires_at = datetime.now() + timedelta(
                    hours=self.celebration_hours
                )
                self.last_win_score = score_str
                self._win_info = {
                    "cubs_abbr":  CUBS_ABBR,
                    "opp_abbr":   opp_abbr,
                    "cubs_score": cubs_score,
                    "opp_score":  opp_score,
                }
                self._live_priority_fired = False   # Allow one-shot live takeover
                self._build_frames()

                self.logger.info(
                    "Cubs win detected! %s %d – %s %d. Celebrating for %.1f hours.",
                    CUBS_ABBR, cubs_score, opp_abbr, opp_score, self.celebration_hours,
                )
            return  # Found the Cubs game; stop iterating

    def _check_expiry(self) -> None:
        """Deactivate celebration if the configured window has passed."""
        if self.celebrating and self.win_expires_at:
            if datetime.now() >= self.win_expires_at:
                self.celebrating = False
                self.logger.info("Cubs win celebration window expired")

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def display(self, force_clear: bool = False) -> bool:
        """Render the current animation frame to the LED matrix.

        Returns False when not celebrating so the display controller skips
        this plugin and advances to the next one in the rotation.
        """
        if not self.celebrating:
            return False

        if not self._frames:
            self._build_frames()

        # Signal that we have been shown at least once — disables the
        # live-priority re-takeover so normal rotation can resume afterward.
        self._live_priority_fired = True

        try:
            now = time.monotonic()

            # Advance GIF frame
            if self._frame_durations:
                frame_duration = self._frame_durations[self._frame_index % len(self._frame_durations)]
            else:
                frame_duration = 1.0 / max(self.animation_fps, 1.0)

            if now - self._last_frame_time >= frame_duration:
                self._frame_index = (self._frame_index + 1) % len(self._frames)
                self._last_frame_time = now

            # Advance flash state
            if now - self._flash_last_toggle >= self._flash_period:
                self._flash_on = not self._flash_on
                self._flash_last_toggle = now

            # Copy base frame and draw live overlays (text + score)
            frame = self._frames[self._frame_index].copy()
            self._draw_overlays(frame)

            self.display_manager.image = frame
            self.display_manager.update_display()
            return True

        except Exception as exc:
            self.logger.error("Error in display(): %s", exc, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Live priority — take over display right after a win
    # ------------------------------------------------------------------

    def has_live_content(self) -> bool:
        """
        Returns True exactly once per new win to trigger an immediate takeover.
        After display() is first called, _live_priority_fired is set and this
        returns False, allowing normal rotation to resume.
        """
        return self.celebrating and not self._live_priority_fired

    def get_live_modes(self) -> List[str]:
        return ["fly_the_w"]

    # ------------------------------------------------------------------
    # Vegas scroll mode support
    # ------------------------------------------------------------------

    def get_vegas_content_type(self) -> str:
        return "static" if self.celebrating else "none"

    def get_vegas_display_mode(self) -> VegasDisplayMode:
        if self.celebrating:
            return VegasDisplayMode.STATIC
        return VegasDisplayMode.FIXED_SEGMENT

    def get_vegas_content(self) -> Optional[Image.Image]:
        """Return the current animation frame as a static Vegas block."""
        if not self.celebrating or not self._frames:
            return None
        return self._frames[self._frame_index % len(self._frames)]

    # ------------------------------------------------------------------
    # Configuration & lifecycle helpers
    # ------------------------------------------------------------------

    def validate_config(self) -> bool:
        if not super().validate_config():
            return False

        hours = self.config.get("celebration_hours", 1.0)
        try:
            hours = float(hours)
            if hours <= 0 or hours > 24:
                self.logger.error("celebration_hours must be between 0 and 24")
                return False
        except (TypeError, ValueError):
            self.logger.error("celebration_hours must be a number")
            return False

        fps = self.config.get("animation_fps", 12.0)
        try:
            fps = float(fps)
            if fps <= 0 or fps > 60:
                self.logger.error("animation_fps must be between 0 and 60")
                return False
        except (TypeError, ValueError):
            self.logger.error("animation_fps must be a number")
            return False

        return True

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        was_simulating = self.simulate_win
        super().on_config_change(new_config)
        self._load_config()
        self._load_fonts()
        self._build_frames()

        # Trigger immediately when simulate_win is turned on via the web UI
        if self.simulate_win and not was_simulating:
            self._trigger_simulation()
        # Cancel simulation when turned off
        elif not self.simulate_win and was_simulating and self.celebrating:
            self.celebrating = False
            self.logger.info("Simulation cancelled")

        self.logger.info("Configuration updated")

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update(
            {
                "celebrating": self.celebrating,
                "win_expires_at": (
                    self.win_expires_at.isoformat() if self.win_expires_at else None
                ),
                "last_win_score": self.last_win_score,
                "last_update": (
                    self.last_update.isoformat() if self.last_update else None
                ),
            }
        )
        return info

    def cleanup(self) -> None:
        self._frames = []
        self._frame_durations = []
        self._win_info = {}
        self.celebrating = False
        self._live_priority_fired = False
        self.logger.info("Fly the W plugin cleaned up")
