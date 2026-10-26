#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/.local/share/mountlink"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"

echo "Installing MountLink..."

# Required system packages
for command in python3 sshfs fusermount3 thunar; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing dependency: $command"
        echo "Install system dependencies with:"
        echo "sudo apt install python3-venv sshfs fuse3 thunar"
        exit 1
    fi
done

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"

# Copy application files
cp mountlink.py "$APP_DIR/"
cp requirements.txt "$APP_DIR/"

# Create Python environment if needed
if [[ ! -d "$APP_DIR/.venv" ]]; then
    python3 -m venv "$APP_DIR/.venv"
fi

# Install dependencies
"$APP_DIR/.venv/bin/python" -m pip install \
    -r "$APP_DIR/requirements.txt"

# Create launcher
cat > "$BIN_DIR/mountlink" <<EOF
#!/usr/bin/env bash
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/mountlink.py" "\$@"
EOF

chmod +x "$BIN_DIR/mountlink"

# Create desktop application entry
cat > "$DESKTOP_DIR/mountlink.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=MountLink
Comment=SSHFS Connection Manager
Exec=$BIN_DIR/mountlink
Icon=folder-remote-symbolic
Terminal=false
Categories=Network;Utility;
EOF

echo
echo "MountLink installed successfully!"
echo "Launch with: $BIN_DIR/mountlink"
