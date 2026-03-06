# InkyPi LCD Setup Guide

This guide covers setting up InkyPi with an HDMI LCD display instead of an e-ink screen.

## Hardware Requirements

| Component | Details |
|-----------|---------|
| **Raspberry Pi** | Pi Zero 2 W (512MB RAM) or any Pi with HDMI |
| **Display** | 5" HDMI LCD, 800×480 resolution |
| **Adapter** | Mini HDMI to HDMI adapter (for Pi Zero 2 W) |
| **Power** | 5V 2.5A+ micro USB power supply |
| **Storage** | 8GB+ microSD card |

> **Note:** The Pi Zero 2 W has a mini HDMI port. You'll need a mini HDMI to standard HDMI adapter or cable to connect the LCD.

## 1. Flash Raspberry Pi OS Lite

1. Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi OS Lite (64-bit)** — no desktop environment needed
3. Click the gear icon (⚙️) to configure:
   - **Hostname:** `inkypi.local`
   - **Enable SSH:** Yes (use password or key)
   - **Set username/password:** Your choice
   - **Configure WiFi:** Enter your SSID and password
   - **Locale:** Set your timezone
4. Flash to your microSD card

## 2. HDMI LCD Configuration

After flashing, mount the boot partition and edit `config.txt` (or let the installer handle it):

### Manual Configuration

Edit `/boot/firmware/config.txt` and add at the end:

```ini
# InkyPi LCD Display Configuration
hdmi_group=2
hdmi_mode=87
hdmi_cvt 800 480 60 6 0 0 0
hdmi_force_hotplug=1
```

**What these settings do:**
- `hdmi_group=2` — Use DMT (Display Monitor Timings) mode
- `hdmi_mode=87` — Custom mode (uses hdmi_cvt values)
- `hdmi_cvt 800 480 60 6 0 0 0` — 800×480 at 60Hz, reduced blanking
- `hdmi_force_hotplug=1` — Force HDMI output even if no display detected at boot

### Disable Console Blanking

Edit `/boot/firmware/cmdline.txt` and append to the existing line (do NOT add a new line):

```
consoleblank=0
```

## 3. Installation

### Connect and Install

1. Insert the microSD card, connect the LCD via HDMI, and power on
2. SSH into the Pi:
   ```bash
   ssh pi@inkypi.local
   ```
3. Clone InkyPi:
   ```bash
   git clone https://github.com/YOUR_USER/InkyPi.git
   cd InkyPi
   ```
4. Run the installer with the **`-L` flag** for LCD mode:
   ```bash
   sudo bash install/install.sh -L
   ```

The `-L` flag automatically:
- Adds HDMI configuration to `config.txt`
- Sets `display_type: lcd` and `resolution: [800, 480]` in device config
- Disables console blanking and screen saver
- Installs `fbi` as a framebuffer fallback tool
- Hides the cursor

5. Reboot when prompted:
   ```bash
   sudo reboot now
   ```

## 4. Camera Plugin Setup (Web UI)

After reboot, access the InkyPi web UI:

1. Open a browser and navigate to:
   ```
   http://inkypi.local:8080
   ```
   Or use the Pi's IP address: `http://<PI_IP>:8080`

2. **Add a Reolink Camera plugin instance:**
   - Go to the plugin list
   - Find "Reolink Camera" and click to add
   - Configure your cameras:
     - **Camera Name:** Friendly name (e.g., "Front Door")
     - **IP Address:** Your Reolink camera's IP (e.g., `192.168.1.100`)
     - **Username:** Camera login (default: `admin`)
     - **Password:** Camera password
     - **Channel:** `0` for single-lens cameras
   - Choose a **layout:**
     - `single` — One camera, full screen
     - `grid_2x1` — Two cameras side by side
     - `grid_1x2` — Two cameras stacked
     - `grid_2x2` — Four camera grid
   - Enable timestamp and camera name overlays
   - Click **Save** and **Refresh**

3. **Set the refresh interval:**
   - LCD supports intervals as low as **10 seconds** (e-ink typically needs 60+ seconds)
   - Go to Settings → Plugin Cycle Interval
   - Set to desired interval (e.g., 30 seconds for near-real-time camera views)

## 5. Display Configuration

The device config is stored at `src/config/device.json`. Key LCD settings:

```json
{
    "name": "InkyPi",
    "display_type": "lcd",
    "resolution": [800, 480],
    "orientation": "horizontal",
    "plugin_cycle_interval_seconds": 30,
    "timezone": "US/Eastern"
}
```

### Configuration Options

| Key | Values | Description |
|-----|--------|-------------|
| `display_type` | `lcd`, `inky`, `mock`, `epd*` | Display driver to use |
| `resolution` | `[width, height]` | Display resolution in pixels |
| `orientation` | `horizontal`, `vertical` | Screen orientation |
| `plugin_cycle_interval_seconds` | `10+` | How often to refresh (LCD allows 10s+) |
| `fb_device` | `/dev/fb0` | Framebuffer device path (advanced) |

## 6. How the LCD Driver Works

The LCD display driver (`src/display/lcd_display.py`) works in two modes:

### Primary: Direct Framebuffer Write
- Detects framebuffer parameters (resolution, pixel format) from `/sys/class/graphics/fb0/` or via `ioctl`
- Converts PIL images to the correct pixel format (RGB565, RGB888, or BGRA8888)
- Writes raw pixel data directly to `/dev/fb0`

### Fallback: fbi Tool
- If direct framebuffer write fails (e.g., permissions), uses the `fbi` command-line tool
- Requires `fbi` package (`sudo apt-get install fbi`)

### Permissions

The user running InkyPi needs write access to `/dev/fb0`. Either:
- Run as root (the systemd service does this)
- Add the user to the `video` group: `sudo usermod -aG video pi`

## Troubleshooting

### Display shows nothing
1. Check HDMI connection and adapter
2. Verify `config.txt` settings: `cat /boot/firmware/config.txt | grep hdmi`
3. Check if framebuffer exists: `ls -la /dev/fb0`
4. Test manually: `echo "hello" | sudo fbi -T 1 --noverbose /usr/share/pixmaps/debian-logo.png`

### "Permission denied" writing to framebuffer
```bash
sudo usermod -aG video $USER
# Then log out and back in
```

### Display is blank/wrong resolution
1. Ensure the LCD is connected **before** powering on
2. Check `hdmi_force_hotplug=1` is in `config.txt`
3. Try a different `hdmi_cvt` line for your specific LCD panel
4. Check `tvservice -s` for current display status

### Screen goes blank after inactivity
```bash
# Disable blanking immediately
sudo sh -c 'echo 0 > /sys/module/kernel/parameters/consoleblank'
sudo setterm --blank 0 --powerdown 0

# Permanent fix — ensure consoleblank=0 is in cmdline.txt
cat /boot/firmware/cmdline.txt
```

### Camera snapshots fail
1. Verify camera IP is reachable: `ping 192.168.1.100`
2. Test the API endpoint in a browser:
   ```
   http://192.168.1.100/cgi-bin/api.cgi?cmd=Snap&channel=0&user=admin&password=YOUR_PASS
   ```
3. Check credentials — a 401 error means wrong username/password
4. Some Reolink cameras require HTTPS — try `https://` in the IP field
5. Check firewall rules on your network

### High memory usage on Pi Zero 2 W
- The LCD driver resizes images immediately to keep RAM usage low
- If running multiple camera plugins, use `grid_2x2` layout (processes one image at a time)
- Monitor with: `free -h` and `htop`
- The install script sets up `zramswap` for additional virtual memory

### Web UI not accessible
1. Check if InkyPi service is running: `sudo systemctl status inkypi`
2. Check the port: `ss -tlnp | grep 8080`
3. View logs: `sudo journalctl -u inkypi -f`

## Switching Between LCD and E-ink

To switch back to e-ink, edit `src/config/device.json`:

```json
{
    "display_type": "inky"
}
```

And remove or comment out the HDMI LCD lines in `/boot/firmware/config.txt`. The e-ink functionality is fully preserved — the `display_type` config flag controls which driver is used.
