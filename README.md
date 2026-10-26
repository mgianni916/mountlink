# MountLink

**A lightweight SSHFS connection manager for Linux.**

MountLink is a Python-based system tray application that makes managing remote SSHFS filesystems simple. Designed primarily for the XFCE desktop environment, it provides a convenient way to mount, unmount, monitor, and access remote directories without repeatedly opening a terminal.

Built with **Python, PySide6, SSHFS, and FUSE**.

**Current version:** 1.0  
**Platform:** Linux  
**Desktop environment:** XFCE (primary target)  
**License:** To be determined

---

## Features

- **System tray integration** — Manage remote filesystems directly from your desktop's notification area.
- **One-click mounting** — Connect and disconnect SSHFS filesystems without entering terminal commands.
- **Connection status indicators** — Quickly identify connected, disconnected, connecting, or unavailable servers using colored status indicators.
- **Connection diagnostics** — Test SSH authentication, SFTP functionality, and remote directory accessibility.
- **Thunar integration** — Open mounted directories directly in the Thunar file manager.
- **Multiple server profiles** — Save and manage multiple SSHFS connections.
- **Automatic mounting** — Optionally mount selected connections when MountLink starts.
- **XFCE autostart** — Launch MountLink automatically when logging into your desktop.
- **Background monitoring** — Periodically check remote connection availability.
- **Desktop notifications** — Receive notifications when operations succeed or fail.
- **Error logging** — Rotating log files help diagnose connection problems.
- **Configuration migration** — Automatically migrate saved connections from the earlier SSHFS Mount Manager application.

MountLink uses standard SSH authentication and supports SSH keys and SSH agents. It does not store SSH passwords.

## Requirements

MountLink requires:

- Linux with an available system tray
- Python 3
- PySide6
- OpenSSH client
- SSHFS
- FUSE 3
- Thunar file manager

On Debian or Ubuntu-based systems, install the system dependencies:

```bash
sudo apt update
sudo apt install python3 python3-venv sshfs fuse3 openssh-client thunar
```

Other Linux distributions may use different package names.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/mgianni916/mountlink.git
cd mountlink
```

### 2. Install MountLink

If the repository includes the installation script:

```bash
chmod +x install.sh
./install.sh
```

The installer places MountLink in your home directory and creates a launcher.

### Manual installation

You can also run MountLink directly from its source directory.

Create a Python virtual environment:

```bash
python3 -m venv .venv
```

Install PySide6:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Start MountLink:

```bash
.venv/bin/python mountlink.py
```

No root privileges are required to run the application itself.

## Using MountLink

Once launched, MountLink appears in the XFCE system tray using a remote-folder icon.

### Left-click menu

Left-click the tray icon to display your configured connections.

- Click a connection name to mount or unmount its remote filesystem.
- Click the folder icon beside a connected server to open its mount directory in Thunar.
- Select **Refresh Status** to check connections.
- Select **Quit** to exit MountLink.

### Right-click menu

Right-click the tray icon to access connection management and application settings.

| Option | Description |
|---|---|
| Add Connection | Create a new SSHFS connection |
| Edit Connection | Modify a saved connection |
| Remove Connection | Delete a saved connection profile |
| Test Connection | Check SSH, SFTP, and remote directory access |
| Start automatically with XFCE | Enable or disable desktop autostart |
| Open Log File | View application logs |
| About MountLink | Display application information |
| Quit | Close MountLink |

### Connection status indicators

| Color | Meaning |
|---|---|
| 🟢 Green | Connection is mounted and diagnostics succeeded |
| ⚪ Gray | Connection is not mounted |
| 🟠 Orange | Connection is mounting, unmounting, or awaiting a health check |
| 🔴 Red | Connection is mounted, but its connectivity check failed |

**Note:** Connection monitoring checks SSH/SFTP availability. It does not directly verify the health of every operation performed through an existing FUSE mount.

## Adding a connection

Select **Add Connection** from the right-click menu.

Enter the following information:

| Field | Example | Description |
|---|---|---|
| Display name | Home Server | Name shown in the tray |
| SSH target | `user@192.168.1.100` | SSH username and hostname, IP address, or configured SSH alias |
| Remote directory | `/home/user/shared` | Directory on the remote server |
| Local mount point | `~/Mounts/home-server` | Directory on your local computer |
| Mount automatically | Optional | Mount when MountLink starts |

MountLink creates the local mount directory if it does not already exist.

The mount directory must be empty and cannot already be occupied by another filesystem.

## SSH authentication

MountLink is designed to work with SSH keys and SSH agents.

To generate an SSH key, if you do not already have one:

```bash
ssh-keygen -t ed25519
```

To install your public key on the remote server:

```bash
ssh-copy-id user@server
```

Test the connection:

```bash
ssh user@server
```

MountLink uses noninteractive SSH authentication, so your SSH key must be usable without an interactive password prompt, typically through an SSH agent.

You can also configure hosts in `~/.ssh/config` and use their aliases in MountLink.

## Configuration and logging

MountLink follows standard Linux user-directory conventions.

| Purpose | Location |
|---|---|
| Saved connections | `~/.config/mountlink/connections.json` |
| Application settings (reserved) | `~/.config/mountlink/settings.json` |
| Application log | `~/.local/state/mountlink/mountlink.log` |
| XFCE autostart | `~/.config/autostart/mountlink.desktop` |

Configuration files are stored separately from the application source, allowing MountLink to be updated without deleting saved connections.

### View logs

To monitor the log in real time:

```bash
tail -f ~/.local/state/mountlink/mountlink.log
```

MountLink uses rotating logs with a maximum size of 1 MB per file and three backup files.

## Updating MountLink

If installed from a cloned Git repository:

```bash
cd mountlink
git pull --ff-only
./install.sh
```

Quit MountLink before updating the installed application, then restart it.

Saved connections and logs are retained during updates.

## Troubleshooting

### MountLink does not appear in the system tray

Make sure your desktop environment has a working system tray. In XFCE, check that the Status Tray plugin is enabled.

### A connection fails to mount

First test SSH access manually:

```bash
ssh user@server
```

Then test SSHFS directly:

```bash
sshfs user@server:/remote/path ~/Mounts/test
```

Check the MountLink log for additional details.

### SSH authentication fails

Ensure that:

- Your SSH key is installed on the remote server.
- Your SSH agent has access to the key, if necessary.
- The remote SSH server is running.
- The SSH hostname and username are correct.
- The remote directory exists and is accessible.

### A mount cannot be disconnected

Check the mount status:

```bash
findmnt -t fuse.sshfs
```

If necessary, try a manual unmount:

```bash
fusermount3 -u ~/Mounts/server
```

A busy mount may need applications accessing it to close before it can be unmounted.

## Project goals

MountLink is intended to remain:

- **Lightweight** — A small desktop utility rather than a full network management suite.
- **Simple** — Common SSHFS operations should require minimal interaction.
- **Transparent** — Clear connection indicators, diagnostic results, and readable logs.
- **Secure** — Use established SSH authentication without storing passwords.
- **Linux-friendly** — Follow familiar filesystem conventions and desktop integration practices.

## Version history

### v1.0 — Initial branded release

- Renamed SSHFS Mount Manager to MountLink.
- Introduced the new application configuration and logging directories.
- Added migration support for existing connection profiles.
- Preserved the dual-menu system tray interface.
- Included SSH/SFTP diagnostics, background monitoring, status indicators, and XFCE autostart support.

### v0.6 — Diagnostics and monitoring

- Added background connection checks.
- Added connection testing and diagnostics.
- Added rotating error logs.
- Added XFCE autostart management.
- Added connecting and disconnecting indicators.

### v0.5 — Tray interface

- Introduced separate left-click and right-click menus.
- Added per-connection mount controls and Thunar shortcuts.
- Improved tray menu usability.

## Contributing

MountLink is a small Python project focused on practical Linux desktop functionality.

Bug reports, feature suggestions, and contributions are welcome if the repository is made public.

## License

A license has not yet been selected. Until a license is added, the repository should not be assumed to grant permission to redistribute or modify the software.

---

**MountLink 1.0**  
*Remote filesystems, one click away.*
