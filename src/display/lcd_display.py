"""
LCD Display driver for HDMI-connected LCD screens (e.g., 5" 800x480).

Writes images to the Linux framebuffer (/dev/fb0) for direct display output.
Falls back to the `fbi` command-line tool if direct framebuffer access fails.
Designed for Pi Zero 2 W with 512MB RAM — images are resized immediately.
"""

import os
import struct
import subprocess
import logging
import tempfile
from .abstract_display import AbstractDisplay

logger = logging.getLogger(__name__)

# Default framebuffer device
DEFAULT_FB_DEVICE = "/dev/fb0"


class LCDDisplay(AbstractDisplay):
    """Display driver for HDMI LCD screens using the Linux framebuffer."""

    def __init__(self, device_config):
        self.device_config = device_config
        self.fb_device = device_config.get_config("fb_device", default=DEFAULT_FB_DEVICE)
        self.fb_info = None
        self.initialize_display()

    def initialize_display(self):
        """Detect framebuffer parameters and validate the device is available."""
        self.fb_info = self._detect_fb_info()
        if self.fb_info:
            logger.info(
                "LCD display initialized: %dx%d, %d bpp, pixel format: %s, device: %s",
                self.fb_info["width"],
                self.fb_info["height"],
                self.fb_info["bpp"],
                self.fb_info["pixel_format"],
                self.fb_device,
            )
        else:
            logger.warning(
                "Could not detect framebuffer info for %s — will use fbi fallback",
                self.fb_device,
            )

    def display_image(self, image, image_settings=[]):
        """
        Render a PIL Image to the LCD screen.

        Attempts direct framebuffer write first; falls back to `fbi` tool.
        """
        # Ensure image fits the target resolution to keep RAM usage low
        if self.fb_info:
            target_size = (self.fb_info["width"], self.fb_info["height"])
        else:
            target_size = self.device_config.get_resolution()

        if image.size != target_size:
            image = image.resize(target_size)

        # Try direct framebuffer write
        if self.fb_info and self._write_framebuffer(image):
            logger.info("Image written to framebuffer successfully")
            return

        # Fallback: use fbi
        logger.info("Falling back to fbi tool for display")
        self._write_fbi(image)

    # -------------------------------------------------------------------------
    # Framebuffer detection
    # -------------------------------------------------------------------------

    def _detect_fb_info(self):
        """
        Read framebuffer geometry from /sys/class/graphics/fbN/.

        Returns a dict with width, height, bpp, stride, pixel_format or None.
        """
        fb_name = os.path.basename(self.fb_device)  # e.g. "fb0"
        sys_path = f"/sys/class/graphics/{fb_name}"

        if not os.path.isdir(sys_path):
            # Try ioctl-based detection as alternative
            return self._detect_fb_info_ioctl()

        try:
            width, height, bpp = None, None, None

            # Virtual size gives actual framebuffer dimensions
            vsize_path = os.path.join(sys_path, "virtual_size")
            if os.path.exists(vsize_path):
                with open(vsize_path) as f:
                    parts = f.read().strip().split(",")
                    width, height = int(parts[0]), int(parts[1])

            bpp_path = os.path.join(sys_path, "bits_per_pixel")
            if os.path.exists(bpp_path):
                with open(bpp_path) as f:
                    bpp = int(f.read().strip())

            if width and height and bpp:
                stride = width * (bpp // 8)
                pixel_format = self._bpp_to_format(bpp)
                return {
                    "width": width,
                    "height": height,
                    "bpp": bpp,
                    "stride": stride,
                    "pixel_format": pixel_format,
                }
        except Exception as e:
            logger.warning("Failed to read framebuffer sysfs info: %s", e)

        return self._detect_fb_info_ioctl()

    def _detect_fb_info_ioctl(self):
        """
        Use FBIOGET_VSCREENINFO ioctl to detect framebuffer parameters.

        struct fb_var_screeninfo layout (first 40 bytes):
          uint32 xres, yres, xres_virtual, yres_virtual,
          uint32 xoffset, yoffset, bits_per_pixel, grayscale, ...
        """
        FBIOGET_VSCREENINFO = 0x4600

        try:
            import fcntl

            with open(self.fb_device, "rb") as fb:
                # Read 160 bytes to cover the full var screeninfo struct
                buf = bytearray(160)
                fcntl.ioctl(fb, FBIOGET_VSCREENINFO, buf)

                xres, yres = struct.unpack("II", buf[0:8])
                xres_v, yres_v = struct.unpack("II", buf[8:16])
                bpp = struct.unpack("I", buf[24:28])[0]

                width = xres_v if xres_v > 0 else xres
                height = yres_v if yres_v > 0 else yres

                if width > 0 and height > 0 and bpp > 0:
                    stride = width * (bpp // 8)
                    pixel_format = self._bpp_to_format(bpp)
                    return {
                        "width": width,
                        "height": height,
                        "bpp": bpp,
                        "stride": stride,
                        "pixel_format": pixel_format,
                    }
        except Exception as e:
            logger.warning("ioctl framebuffer detection failed: %s", e)

        return None

    @staticmethod
    def _bpp_to_format(bpp):
        """Map bits-per-pixel to a human-readable pixel format string."""
        formats = {
            16: "RGB565",
            24: "RGB888",
            32: "BGRA8888",
        }
        return formats.get(bpp, f"UNKNOWN_{bpp}bpp")

    # -------------------------------------------------------------------------
    # Direct framebuffer writing
    # -------------------------------------------------------------------------

    def _write_framebuffer(self, image):
        """
        Write a PIL Image directly to the framebuffer device.

        Converts the image to the correct pixel format based on detected bpp.
        Returns True on success, False on failure.
        """
        if not self.fb_info:
            return False

        try:
            bpp = self.fb_info["bpp"]
            width = self.fb_info["width"]
            height = self.fb_info["height"]

            # Convert image to raw pixel data in the framebuffer's format
            raw_data = self._image_to_raw(image, bpp, width, height)

            with open(self.fb_device, "wb") as fb:
                fb.write(raw_data)

            return True

        except PermissionError:
            logger.warning(
                "Permission denied writing to %s — try running as root or adding user to 'video' group",
                self.fb_device,
            )
            return False
        except Exception as e:
            logger.warning("Failed to write framebuffer: %s", e)
            return False

    @staticmethod
    def _image_to_raw(image, bpp, width, height):
        """
        Convert a PIL Image to raw framebuffer bytes.

        Supports 16-bit (RGB565), 24-bit (RGB888), and 32-bit (BGRA8888).
        Resizes the image to match framebuffer dimensions if needed.
        """
        if image.size != (width, height):
            image = image.resize((width, height))

        if bpp == 16:
            # RGB565
            image = image.convert("RGB")
            pixels = image.load()
            raw = bytearray(width * height * 2)
            idx = 0
            for y in range(height):
                for x in range(width):
                    r, g, b = pixels[x, y]
                    # Pack as RGB565 little-endian
                    val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                    raw[idx] = val & 0xFF
                    raw[idx + 1] = (val >> 8) & 0xFF
                    idx += 2
            return bytes(raw)

        elif bpp == 24:
            # RGB888
            image = image.convert("RGB")
            return image.tobytes("raw", "BGR")

        elif bpp == 32:
            # BGRA8888 (most common for HDMI displays)
            image = image.convert("RGBA")
            pixels = image.tobytes("raw", "BGRA")
            return pixels

        else:
            raise ValueError(f"Unsupported framebuffer bpp: {bpp}")

    # -------------------------------------------------------------------------
    # fbi fallback
    # -------------------------------------------------------------------------

    def _write_fbi(self, image):
        """
        Use the `fbi` tool to display an image on the framebuffer.

        This is the fallback when direct framebuffer writing isn't possible.
        """
        tmp_path = None
        try:
            # Write image to a temporary file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
                image.save(tmp, format="PNG")

            # Kill any existing fbi process
            subprocess.run(
                ["killall", "fbi"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Display with fbi
            cmd = [
                "fbi",
                "--device", self.fb_device,
                "--noverbose",
                "--once",
                "--fitwidth",
                tmp_path,
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
            )

            if result.returncode != 0:
                logger.error(
                    "fbi failed (code %d): %s",
                    result.returncode,
                    result.stderr.decode().strip(),
                )
            else:
                logger.info("Image displayed via fbi successfully")

        except FileNotFoundError:
            logger.error(
                "fbi tool not found — install with: sudo apt-get install fbi"
            )
        except subprocess.TimeoutExpired:
            logger.error("fbi command timed out")
        except Exception as e:
            logger.error("fbi fallback failed: %s", e)
        finally:
            # Clean up temp file
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
