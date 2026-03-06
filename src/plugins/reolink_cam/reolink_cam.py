"""
Reolink Camera Plugin for InkyPi.

Captures snapshots from Reolink IP cameras via their HTTP API and displays
them in single or grid layouts with optional timestamp and camera name overlays.

Designed for Pi Zero 2 W (512MB RAM) — images are resized immediately after
capture to minimize memory usage.
"""

import logging
import math
from datetime import datetime
from io import BytesIO

import pytz
import requests
from PIL import Image, ImageColor, ImageDraw, ImageFont
from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

# Reolink API timeout (seconds)
REQUEST_TIMEOUT = 8

# Placeholder color for offline cameras
PLACEHOLDER_BG = (40, 40, 40)
PLACEHOLDER_TEXT_COLOR = (160, 160, 160)

# Maximum dimension for a single snapshot before compositing (saves RAM)
MAX_SNAPSHOT_DIM = 1024


class ReolinkCamPlugin(BasePlugin):
    """Plugin to display Reolink camera snapshots."""

    def generate_image(self, settings, device_config):
        """
        Generate the display image from camera snapshots.

        Args:
            settings (dict): Plugin instance settings (cameras, layout, etc.).
            device_config: Device configuration object.

        Returns:
            PIL.Image: The composited image ready for display.
        """
        cameras = settings.get("cameras", [])
        layout = settings.get("layout", "single")
        show_timestamp = settings.get("show_timestamp", True)
        show_camera_name = settings.get("show_camera_name", True)
        overlay_position = settings.get("overlay_position", "bottom-left")
        bg_color = settings.get("background_color", "#000000")

        # Get display dimensions
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        # Parse background color
        try:
            bg = ImageColor.getcolor(bg_color, "RGB")
        except Exception:
            bg = (0, 0, 0)

        # Get timezone for timestamps
        tz_str = device_config.get_config("timezone", default="UTC")
        try:
            tz = pytz.timezone(tz_str)
        except Exception:
            tz = pytz.UTC

        now = datetime.now(tz)

        # Capture snapshots from all configured cameras
        snapshots = []
        for cam_config in cameras:
            snap = self._capture_snapshot(cam_config)
            cam_name = cam_config.get("name", "Camera")
            snapshots.append({"image": snap, "name": cam_name})

        if not snapshots:
            # No cameras configured — return placeholder
            return self._create_placeholder(dimensions, "No cameras configured", bg)

        # Compose the final image based on layout
        image = self._compose_layout(snapshots, layout, dimensions, bg)

        # Add overlays
        if show_timestamp or show_camera_name:
            self._add_overlays(
                image,
                snapshots,
                layout,
                dimensions,
                now,
                show_timestamp,
                show_camera_name,
                overlay_position,
            )

        return image

    # -------------------------------------------------------------------------
    # Snapshot capture
    # -------------------------------------------------------------------------

    def _capture_snapshot(self, cam_config):
        """
        Capture a snapshot from a Reolink camera via HTTP API.

        Args:
            cam_config (dict): Camera config with ip, username, password, channel.

        Returns:
            PIL.Image or None: The captured image, or None if capture failed.
        """
        ip = cam_config.get("ip", "").strip()
        username = cam_config.get("username", "admin")
        password = cam_config.get("password", "")
        channel = cam_config.get("channel", 0)

        if not ip:
            logger.warning("Camera IP not configured")
            return None

        # Reolink HTTP API snapshot endpoint
        url = f"http://{ip}/cgi-bin/api.cgi"
        params = {
            "cmd": "Snap",
            "channel": channel,
            "rs": "inkypi",
            "user": username,
            "password": password,
        }

        try:
            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()

            # Check content type — should be image
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type and "octet-stream" not in content_type:
                # Might be a JSON error response
                logger.warning(
                    "Camera %s returned non-image content: %s",
                    ip,
                    content_type,
                )
                try:
                    error_data = response.json()
                    logger.warning("Camera API error: %s", error_data)
                except Exception:
                    pass
                return None

            # Read image data and immediately resize to save RAM
            image_data = BytesIO(response.content)
            img = Image.open(image_data)
            img = self._constrain_size(img, MAX_SNAPSHOT_DIM)
            # Force load into memory so we can close the BytesIO
            img.load()
            return img

        except requests.exceptions.ConnectionError:
            logger.warning("Camera %s: connection refused or unreachable", ip)
        except requests.exceptions.Timeout:
            logger.warning("Camera %s: request timed out after %ds", ip, REQUEST_TIMEOUT)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "unknown"
            if status == 401:
                logger.warning("Camera %s: authentication failed (401)", ip)
            else:
                logger.warning("Camera %s: HTTP error %s", ip, status)
        except Exception as e:
            logger.warning("Camera %s: unexpected error: %s", ip, e)

        return None

    @staticmethod
    def _constrain_size(image, max_dim):
        """Resize image so the largest dimension is at most max_dim."""
        w, h = image.size
        if max(w, h) <= max_dim:
            return image
        scale = max_dim / max(w, h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        return image.resize((new_w, new_h), Image.LANCZOS)

    # -------------------------------------------------------------------------
    # Layout composition
    # -------------------------------------------------------------------------

    def _compose_layout(self, snapshots, layout, dimensions, bg_color):
        """
        Compose snapshots into the specified layout.

        Args:
            snapshots: List of {"image": PIL.Image|None, "name": str}.
            layout: One of "single", "grid_2x1", "grid_2x2", "grid_1x2".
            dimensions: (width, height) tuple.
            bg_color: RGB tuple for background.

        Returns:
            PIL.Image: The composited image.
        """
        width, height = dimensions
        canvas = Image.new("RGB", (width, height), bg_color)

        if layout == "single":
            # Show the first camera only
            snap = snapshots[0]["image"]
            if snap:
                snap = self._fit_image(snap, width, height)
                x = (width - snap.width) // 2
                y = (height - snap.height) // 2
                canvas.paste(snap, (x, y))
            else:
                canvas = self._create_placeholder(
                    dimensions, f"{snapshots[0]['name']}\nOffline", bg_color
                )

        elif layout == "grid_2x1":
            # 2 cameras side by side
            cell_w = width // 2
            cell_h = height
            for i, snap_data in enumerate(snapshots[:2]):
                cell_img = self._get_cell_image(snap_data, cell_w, cell_h, bg_color)
                canvas.paste(cell_img, (i * cell_w, 0))

        elif layout == "grid_1x2":
            # 2 cameras stacked vertically
            cell_w = width
            cell_h = height // 2
            for i, snap_data in enumerate(snapshots[:2]):
                cell_img = self._get_cell_image(snap_data, cell_w, cell_h, bg_color)
                canvas.paste(cell_img, (0, i * cell_h))

        elif layout == "grid_2x2":
            # 4 cameras in a 2x2 grid
            cell_w = width // 2
            cell_h = height // 2
            for i, snap_data in enumerate(snapshots[:4]):
                row = i // 2
                col = i % 2
                cell_img = self._get_cell_image(snap_data, cell_w, cell_h, bg_color)
                canvas.paste(cell_img, (col * cell_w, row * cell_h))

        else:
            logger.warning("Unknown layout '%s', defaulting to single", layout)
            return self._compose_layout(snapshots, "single", dimensions, bg_color)

        return canvas

    def _get_cell_image(self, snap_data, cell_w, cell_h, bg_color):
        """Get a cell image for grid layout — either the snapshot or a placeholder."""
        img = snap_data["image"]
        if img:
            fitted = self._fit_image(img, cell_w, cell_h)
            cell = Image.new("RGB", (cell_w, cell_h), bg_color)
            x = (cell_w - fitted.width) // 2
            y = (cell_h - fitted.height) // 2
            cell.paste(fitted, (x, y))
            return cell
        else:
            return self._create_placeholder(
                (cell_w, cell_h),
                f"{snap_data['name']}\nOffline",
                bg_color,
            )

    @staticmethod
    def _fit_image(image, max_w, max_h):
        """Resize image to fit within max_w x max_h while maintaining aspect ratio."""
        img_w, img_h = image.size
        scale = min(max_w / img_w, max_h / img_h)
        if scale >= 1.0:
            return image
        new_w = int(img_w * scale)
        new_h = int(img_h * scale)
        return image.resize((new_w, new_h), Image.LANCZOS)

    @staticmethod
    def _create_placeholder(dimensions, text, bg_color):
        """Create a placeholder image with centered text."""
        img = Image.new("RGB", dimensions, PLACEHOLDER_BG if bg_color == (0, 0, 0) else bg_color)
        draw = ImageDraw.Draw(img)

        # Use a reasonable font size
        font_size = max(dimensions[1] // 15, 14)
        try:
            font = get_font("DejaVuSans", font_size)
        except Exception:
            font = ImageFont.load_default()

        # Draw each line centered
        lines = text.split("\n")
        total_height = font_size * len(lines) * 1.3
        start_y = (dimensions[1] - total_height) / 2

        for i, line in enumerate(lines):
            y = start_y + i * font_size * 1.3
            draw.text(
                (dimensions[0] / 2, y),
                line,
                font=font,
                fill=PLACEHOLDER_TEXT_COLOR,
                anchor="mt",
            )

        return img

    # -------------------------------------------------------------------------
    # Overlays
    # -------------------------------------------------------------------------

    def _add_overlays(
        self, image, snapshots, layout, dimensions, now,
        show_timestamp, show_camera_name, position,
    ):
        """Add timestamp and camera name overlays to the image."""
        draw = ImageDraw.Draw(image)
        width, height = dimensions

        font_size = max(height // 30, 12)
        try:
            font = get_font("DejaVuSans", font_size)
        except Exception:
            font = ImageFont.load_default()

        # For single layout, add one overlay
        if layout == "single":
            lines = []
            if show_camera_name and snapshots:
                lines.append(snapshots[0]["name"])
            if show_timestamp:
                lines.append(now.strftime("%Y-%m-%d %H:%M:%S"))
            if lines:
                self._draw_overlay_text(draw, "\n".join(lines), font, font_size, width, height, position)
        else:
            # For grid layouts, add per-cell overlays
            cells = self._get_cell_positions(layout, width, height, len(snapshots))
            for i, (cx, cy, cw, ch) in enumerate(cells):
                if i >= len(snapshots):
                    break
                lines = []
                if show_camera_name:
                    lines.append(snapshots[i]["name"])
                if show_timestamp:
                    lines.append(now.strftime("%H:%M:%S"))
                if lines:
                    # Create a sub-region overlay
                    self._draw_overlay_text(
                        draw, "\n".join(lines), font, font_size, cw, ch, position, offset=(cx, cy)
                    )

    @staticmethod
    def _get_cell_positions(layout, width, height, num_cameras):
        """Return list of (x, y, w, h) for each cell in the layout."""
        if layout == "grid_2x1":
            cw = width // 2
            return [(0, 0, cw, height), (cw, 0, cw, height)]
        elif layout == "grid_1x2":
            ch = height // 2
            return [(0, 0, width, ch), (0, ch, width, ch)]
        elif layout == "grid_2x2":
            cw, ch = width // 2, height // 2
            return [
                (0, 0, cw, ch),
                (cw, 0, cw, ch),
                (0, ch, cw, ch),
                (cw, ch, cw, ch),
            ]
        return [(0, 0, width, height)]

    @staticmethod
    def _draw_overlay_text(draw, text, font, font_size, region_w, region_h, position, offset=(0, 0)):
        """Draw text with a semi-transparent background at the specified position."""
        padding = 6
        ox, oy = offset

        # Measure text
        lines = text.split("\n")
        line_height = font_size * 1.3
        text_height = int(line_height * len(lines))
        text_width = 0
        for line in lines:
            try:
                bbox = font.getbbox(line)
                lw = bbox[2] - bbox[0]
            except Exception:
                lw = len(line) * font_size * 0.6
            text_width = max(text_width, int(lw))

        # Calculate position
        if "left" in position:
            x = ox + padding
        else:
            x = ox + region_w - text_width - padding * 2

        if "top" in position:
            y = oy + padding
        else:
            y = oy + region_h - text_height - padding * 2

        # Draw semi-transparent background
        bg_rect = [
            (x - padding, y - padding),
            (x + text_width + padding, y + text_height + padding),
        ]
        draw.rectangle(bg_rect, fill=(0, 0, 0, 160))

        # Draw text
        for i, line in enumerate(lines):
            draw.text(
                (x, y + i * line_height),
                line,
                font=font,
                fill=(255, 255, 255),
            )
