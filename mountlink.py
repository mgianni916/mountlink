
#!/usr/bin/env python3
"""
MountLink 1.0
SSHFS Connection Manager for Linux / XFCE

Requires:
    Python 3
    PySide6
    sshfs, openssh-client, fuse3, thunar

Uses SSH keys / SSH agent. Does not store passwords.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import (
    QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
)
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QStyle,
    QSystemTrayIcon, QWidget, QWidgetAction
)


# ============================================================
# Application
# ============================================================

APP_NAME = "MountLink"
APP_VERSION = "1.0"
APP_DESCRIPTION = "SSHFS Connection Manager for Linux"

HOME = Path.home()

CONFIG_DIR = HOME / ".config" / "mountlink"
CONNECTIONS_FILE = CONFIG_DIR / "connections.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

STATE_DIR = HOME / ".local" / "state" / "mountlink"
LOG_FILE = STATE_DIR / "mountlink.log"

AUTOSTART_FILE = (
    HOME / ".config" / "autostart" / "mountlink.desktop"
)

OLD_CONFIG_DIR = HOME / ".config" / "sshfs-tray"
OLD_STATE_DIR = HOME / ".local" / "state" / "sshfs-tray"
OLD_AUTOSTART = (
    HOME / ".config" / "autostart" / "sshfs-tray.desktop"
)

CHECK_INTERVAL = 30000
UI_INTERVAL = 5000

COLORS = {
    "connected": "#2eaf55",
    "lost": "#e05252",
    "unmounted": "#9299a1",
    "connecting": "#e5a32d",
    "disconnecting": "#e5a32d",
    "checking": "#e5a32d",
}

SSHFS_TYPES = {"fuse.sshfs", "fuse.sshfs3"}


# ============================================================
# Migration and configuration
# ============================================================

def migrate_files():
    """Copy legacy data without overwriting MountLink data.

    Never delete old configuration or logs. Autostart migration
    is handled separately after the application initializes.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    files = [
        (
            OLD_CONFIG_DIR / "connections.json",
            CONNECTIONS_FILE
        ),
        (
            OLD_CONFIG_DIR / "settings.json",
            SETTINGS_FILE
        ),
        (
            OLD_STATE_DIR / "sshfs-tray.log",
            LOG_FILE
        ),
    ]

    for source, destination in files:
        if source.is_file() and not destination.exists():
            shutil.copy2(source, destination)

            if destination in (CONNECTIONS_FILE, SETTINGS_FILE):
                destination.chmod(0o600)

    # Copy existing rotated logs, if present.
    for number in range(1, 4):
        source = OLD_STATE_DIR / f"sshfs-tray.log.{number}"
        destination = STATE_DIR / f"mountlink.log.{number}"

        if source.is_file() and not destination.exists():
            shutil.copy2(source, destination)


def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(data, file, indent=2)
            file.write("\n")

        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def setup_logging():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("mountlink")
    logger.setLevel(logging.INFO)

    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )

    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))

    logger.addHandler(handler)
    return logger


# ============================================================
# Filesystem and SSH helpers
# ============================================================

def local_path(connection):
    return os.path.abspath(
        os.path.expanduser(connection["local"])
    )


def decode_mount_path(value):
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value
    )


def mount_info(path):
    """Read mount status without accessing the remote filesystem."""
    target = os.path.abspath(os.path.expanduser(path))

    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as file:
            for line in file:
                fields = line.split()

                if "-" not in fields or len(fields) < 7:
                    continue

                separator = fields.index("-")

                if separator + 2 >= len(fields):
                    continue

                mountpoint = os.path.normpath(
                    decode_mount_path(fields[4])
                )

                if mountpoint == target:
                    return {
                        "type": fields[separator + 1],
                        "source": decode_mount_path(
                            fields[separator + 2]
                        )
                    }

    except OSError:
        pass

    return None


def is_sshfs(path):
    info = mount_info(path)
    return bool(info and info["type"] in SSHFS_TYPES)


def ssh_target(connection):
    target = connection["host"]

    if "@" in target:
        username, host = target.rsplit("@", 1)
        return username, host

    return None, target


def ssh_options():
    return [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=7",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=1",
    ]


def run_command(command, timeout=20, input_text=None):
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL if input_text is None else None
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Operation timed out after {timeout} seconds"
        )
    except OSError as exc:
        raise RuntimeError(str(exc))

    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout).strip()
            or f"Command exited with status {result.returncode}"
        )

    return result.stdout.strip()


def sftp_command(connection):
    username, host = ssh_target(connection)

    command = ["sftp", "-q", "-b", "-"] + ssh_options()

    if username:
        command.extend(["-o", f"User={username}"])

    command.append(host)
    return command


def check_connection(connection):
    """Check SSH login, SFTP and remote directory access."""
    username, host = ssh_target(connection)

    command = ["ssh"] + ssh_options()

    if username:
        command.extend(["-l", username])

    command.extend([host, "true"])

    run_command(command, timeout=12)

    # Test the SFTP subsystem.
    run_command(
        sftp_command(connection),
        timeout=12,
        input_text="pwd\n"
    )

    # Test access to the configured remote directory.
    remote = connection["remote"]

    if "\n" in remote or "\r" in remote:
        raise RuntimeError("Invalid remote directory")

    escaped = remote.replace("\\", "\\\\").replace('"', '\\"')

    run_command(
        sftp_command(connection),
        timeout=12,
        input_text=f'cd "{escaped}"\npwd\n'
    )

    return (
        "SSH authentication: Passed\n"
        "SFTP subsystem: Passed\n"
        "Remote directory: Accessible"
    )


# ============================================================
# Background worker
# ============================================================

class WorkerSignals(QObject):
    finished = Signal(str, str, bool, str)


class Worker(QRunnable):
    def __init__(self, connection, operation):
        super().__init__()

        self.connection = connection.copy()
        self.operation = operation
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        connection = self.connection
        operation = self.operation
        name = connection["name"]
        path = local_path(connection)

        try:
            if operation == "mount":
                if mount_info(path):
                    raise RuntimeError(
                        "Mount directory is already in use"
                    )

                Path(path).mkdir(parents=True, exist_ok=True)

                if any(Path(path).iterdir()):
                    raise RuntimeError(
                        "Mount directory must be empty"
                    )

                command = [
                    "sshfs",
                    f"{connection['host']}:{connection['remote']}",
                    path,
                    "-o", "reconnect",
                    "-o", "ServerAliveInterval=15",
                    "-o", "ServerAliveCountMax=3",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=10",
                ]

                run_command(command, timeout=30)

                if not is_sshfs(path):
                    raise RuntimeError(
                        "SSHFS mount was not established"
                    )

                message = "Mounted successfully"

            elif operation == "unmount":
                if not is_sshfs(path):
                    raise RuntimeError(
                        "Directory is not an SSHFS mount"
                    )

                run_command(
                    ["fusermount3", "-u", path],
                    timeout=15
                )

                message = "Unmounted successfully"

            elif operation in ("test", "probe"):
                message = check_connection(connection)

            else:
                raise RuntimeError("Unknown operation")

            LOG.info("%s: %s successful", name, operation)

            self.signals.finished.emit(
                name, operation, True, message
            )

        except Exception as exc:
            message = str(exc)

            LOG.error(
                "%s: %s failed: %s",
                name, operation, message
            )

            self.signals.finished.emit(
                name, operation, False, message
            )


# ============================================================
# Connection editor
# ============================================================

class ConnectionDialog(QDialog):
    def __init__(self, data=None):
        super().__init__()

        data = data or {}

        self.setWindowTitle("MountLink — Connection")
        self.setMinimumWidth(440)

        layout = QFormLayout(self)
        self.fields = {}

        fields = [
            ("name", "Display name", "Home Server"),
            ("host", "SSH target", "user@server or SSH alias"),
            ("remote", "Remote directory", "/home/user"),
            ("local", "Local mount point", "~/Mounts/server"),
        ]

        for key, label, placeholder in fields:
            field = QLineEdit(data.get(key, ""))
            field.setPlaceholderText(placeholder)
            layout.addRow(label, field)
            self.fields[key] = field

        self.auto = QCheckBox(
            "Mount automatically when MountLink starts"
        )
        self.auto.setChecked(data.get("auto", False))
        layout.addRow(self.auto)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )

        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)

        layout.addRow(buttons)

    def validate(self):
        values = {
            key: field.text().strip()
            for key, field in self.fields.items()
        }

        if not all(values.values()):
            QMessageBox.warning(
                self, "Missing information",
                "All fields are required."
            )
            return

        host = values["host"]

        if (
            host.startswith("-")
            or host.count("@") > 1
            or host.startswith("@")
            or host.endswith("@")
            or ":" in host
            or any(ch.isspace() for ch in host)
        ):
            QMessageBox.warning(
                self, "Invalid SSH target",
                "Use user@server or an SSH configuration alias."
            )
            return

        if (
            values["remote"].startswith("-")
            or "\n" in values["remote"]
            or "\r" in values["remote"]
        ):
            QMessageBox.warning(
                self, "Invalid remote directory",
                "Please enter a valid remote directory."
            )
            return

        self.accept()

    def value(self):
        return {
            **{
                key: field.text().strip()
                for key, field in self.fields.items()
            },
            "auto": self.auto.isChecked()
        }


# ============================================================
# Main application
# ============================================================

class MountLink(QObject):
    def __init__(self, app):
        super().__init__()

        self.app = app
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(5)

        self.busy = {}
        self.health = {}
        self.workers = {}

        try:
            self.connections = load_json(
                CONNECTIONS_FILE, []
            )

            if not isinstance(self.connections, list):
                raise ValueError(
                    "Connection configuration must be a list"
                )

            names = set()
            paths = set()

            for connection in self.connections:
                if not isinstance(connection, dict):
                    raise ValueError("Invalid connection record")

                for field in ("name", "host", "remote", "local"):
                    if (
                        not isinstance(connection.get(field), str)
                        or not connection[field].strip()
                    ):
                        raise ValueError(
                            f"Invalid connection field: {field}"
                        )

                name = connection["name"]
                path = local_path(connection)

                if name in names or path in paths:
                    raise ValueError(
                        "Duplicate connection names or mount paths"
                    )

                names.add(name)
                paths.add(path)

        except (OSError, ValueError, TypeError) as exc:
            LOG.error("Configuration error: %s", exc)

            QMessageBox.warning(
                None, "MountLink Configuration Error",
                f"{exc}\n\n"
                "The existing configuration has not been changed."
            )

            self.connections = []

        self.tray = QSystemTrayIcon(
            QIcon.fromTheme(
                "folder-remote-symbolic",
                QIcon.fromTheme("folder-remote")
            ),
            app
        )

        self.left_menu = QMenu()
        self.right_menu = QMenu()

        self.tray.setContextMenu(self.right_menu)
        self.tray.activated.connect(self.tray_activated)

        self.left_menu.aboutToHide.connect(
            lambda: QTimer.singleShot(0, self.refresh)
        )
        self.right_menu.aboutToHide.connect(
            lambda: QTimer.singleShot(0, self.refresh)
        )

        self.refresh()
        self.tray.show()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.refresh)
        self.ui_timer.start(UI_INTERVAL)

        self.probe_timer = QTimer(self)
        self.probe_timer.timeout.connect(self.check_connections)
        self.probe_timer.start(CHECK_INTERVAL)

        QTimer.singleShot(1000, self.automount)
        QTimer.singleShot(5000, self.check_connections)

        LOG.info("MountLink %s started", APP_VERSION)

    # --------------------------------------------------------
    # Persistence
    # --------------------------------------------------------

    def save(self):
        try:
            save_json(CONNECTIONS_FILE, self.connections)
        except OSError as exc:
            LOG.error("Cannot save configuration: %s", exc)
            QMessageBox.warning(
                None, "Save Error", str(exc)
            )

    # --------------------------------------------------------
    # Tray menus
    # --------------------------------------------------------

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.refresh()
            self.left_menu.popup(QCursor.pos())

    def status(self, connection):
        name = connection["name"]

        if name in self.busy:
            operation = self.busy[name]

            if operation in ("mount", "unmount"):
                return {
                    "mount": "connecting",
                    "unmount": "disconnecting"
                }[operation]

        if not is_sshfs(local_path(connection)):
            return "unmounted"

        return self.health.get(name, "checking")

    def refresh(self):
        mounted_count = sum(
            is_sshfs(local_path(c))
            for c in self.connections
        )

        lost_count = sum(
            self.status(c) == "lost"
            for c in self.connections
        )

        tooltip = (
            f"{APP_NAME} {APP_VERSION}\n"
            f"{mounted_count} mounted"
        )

        if lost_count:
            tooltip += f"\n{lost_count} connection(s) unavailable"

        self.tray.setToolTip(tooltip)

        if not self.left_menu.isVisible():
            self.build_left_menu()

        if not self.right_menu.isVisible():
            self.build_right_menu()

    def build_left_menu(self):
        self.left_menu.clear()

        if not self.connections:
            action = self.left_menu.addAction(
                "No connections configured"
            )
            action.setEnabled(False)

        for connection in self.connections:
            name = connection["name"]
            state = self.status(connection)
            mounted_now = is_sshfs(
                local_path(connection)
            )

            row = QWidget(self.left_menu)
            layout = QHBoxLayout(row)

            layout.setContentsMargins(6, 2, 6, 2)
            layout.setSpacing(5)

            dot = QLabel("●", row)
            dot.setStyleSheet(
                f"color: {COLORS[state]}; font-size: 17px;"
            )
            dot.setToolTip(state.capitalize())
            layout.addWidget(dot)

            label = name

            if state == "connecting":
                label += " — Connecting…"
            elif state == "disconnecting":
                label += " — Disconnecting…"
            elif state == "lost":
                label += " — Connection lost"
            elif state == "checking":
                label += " — Checking…"

            button = QPushButton(label, row)
            button.setFlat(True)
            button.setStyleSheet(
                "QPushButton {"
                "text-align: left;"
                "padding: 5px;"
                "}"
            )

            button.setEnabled(name not in self.busy)

            operation = (
                "unmount" if mounted_now else "mount"
            )

            button.setToolTip(
                "Unmount SSHFS" if mounted_now
                else "Mount SSHFS"
            )

            button.clicked.connect(
                lambda checked=False, c=connection, op=operation:
                    self.connection_clicked(c, op)
            )

            layout.addWidget(button, 1)

            folder = QPushButton(row)

            folder.setIcon(
                QIcon.fromTheme(
                    "folder-open",
                    self.app.style().standardIcon(
                        QStyle.StandardPixmap.SP_DirOpenIcon
                    )
                )
            )

            folder.setToolTip("Open in Thunar")
            folder.setAccessibleName("Open in Thunar")
            folder.setFixedSize(30, 28)

            folder.setEnabled(
                mounted_now and state == "connected"
            )

            folder.clicked.connect(
                lambda checked=False, c=connection:
                    self.open_folder(c)
            )

            layout.addWidget(folder)

            action = QWidgetAction(self.left_menu)
            action.setDefaultWidget(row)
            self.left_menu.addAction(action)

        self.left_menu.addSeparator()

        self.left_menu.addAction(
            "Refresh Status", self.check_connections
        )

        self.left_menu.addAction(
            "Quit", self.app.quit
        )

    def build_right_menu(self):
        self.right_menu.clear()

        self.right_menu.addAction(
            "Add Connection…", self.add
        )
        self.right_menu.addAction(
            "Edit Connection…", self.choose_edit
        )
        self.right_menu.addAction(
            "Remove Connection…", self.choose_remove
        )

        self.right_menu.addSeparator()

        self.right_menu.addAction(
            "Test Connection…", self.choose_test
        )

        self.right_menu.addSeparator()

        startup = self.right_menu.addAction(
            "Start automatically with XFCE"
        )
        startup.setCheckable(True)
        startup.setChecked(AUTOSTART_FILE.exists())
        startup.triggered.connect(self.set_autostart)

        self.right_menu.addAction(
            "Open Log File", self.open_log
        )

        self.right_menu.addSeparator()

        self.right_menu.addAction(
            f"About MountLink {APP_VERSION}",
            self.show_about
        )

        self.right_menu.addSeparator()

        self.right_menu.addAction(
            "Quit", self.app.quit
        )

    # --------------------------------------------------------
    # Background operations
    # --------------------------------------------------------

    def run(self, connection, operation):
        name = connection["name"]

        if name in self.busy:
            return

        self.busy[name] = operation

        if operation == "mount":
            self.health.pop(name, None)

        worker = Worker(connection, operation)
        self.workers[name] = worker

        worker.signals.finished.connect(self.worker_done)

        self.pool.start(worker)
        self.refresh()

    def worker_done(self, name, operation, success, message):
        self.busy.pop(name, None)
        self.workers.pop(name, None)

        connection = next(
            (
                c for c in self.connections
                if c["name"] == name
            ),
            None
        )

        if operation == "probe":
            if connection and is_sshfs(
                local_path(connection)
            ):
                previous = self.health.get(name)

                self.health[name] = (
                    "connected" if success else "lost"
                )

                if not success and previous != "lost":
                    self.notify(
                        name,
                        "Connection check failed: " + message,
                        warning=True
                    )

            self.refresh()
            return

        if operation == "test":
            if success:
                QMessageBox.information(
                    None,
                    "MountLink — Connection Test",
                    f"{name}\n\n{message}"
                )
            else:
                QMessageBox.warning(
                    None,
                    "MountLink — Connection Test",
                    f"{name}\n\n{message}"
                )

            self.refresh()
            return

        if operation == "mount" and success:
            self.health[name] = "checking"

        elif operation == "unmount" and success:
            self.health.pop(name, None)

        self.notify(
            name, message, warning=not success
        )

        if operation == "mount" and success and connection:
            self.run(connection, "probe")

        self.refresh()

    def check_connections(self):
        for connection in self.connections:
            name = connection["name"]

            if name in self.busy:
                continue

            if is_sshfs(local_path(connection)):
                self.run(connection, "probe")
            else:
                self.health.pop(name, None)

        self.refresh()

    def notify(self, title, message, warning=False):
        self.tray.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Warning
            if warning else
            QSystemTrayIcon.MessageIcon.Information,
            4000
        )

    def connection_clicked(self, connection, operation):
        self.left_menu.hide()
        self.run(connection, operation)

    def open_folder(self, connection):
        self.left_menu.hide()

        if self.status(connection) != "connected":
            QMessageBox.warning(
                None, "MountLink",
                "The connection is not confirmed healthy."
            )
            return

        try:
            subprocess.Popen(
                ["thunar", local_path(connection)],
                start_new_session=True
            )
        except OSError as exc:
            QMessageBox.warning(
                None, "Thunar Error", str(exc)
            )

    # --------------------------------------------------------
    # Connection management
    # --------------------------------------------------------

    def choose_connection(self, title, unmounted_only=False):
        eligible = [
            c for c in self.connections
            if c["name"] not in self.busy
            and (
                not unmounted_only
                or not mount_info(local_path(c))
            )
        ]

        if not eligible:
            QMessageBox.information(
                None, title,
                "No eligible connections available."
            )
            return None

        name, ok = QInputDialog.getItem(
            None, title, "Connection:",
            [c["name"] for c in eligible],
            0, False
        )

        if not ok:
            return None

        return next(
            (c for c in eligible if c["name"] == name),
            None
        )

    def choose_edit(self):
        connection = self.choose_connection(
            "Edit Connection", unmounted_only=True
        )
        if connection:
            self.edit(connection)

    def choose_remove(self):
        connection = self.choose_connection(
            "Remove Connection", unmounted_only=True
        )
        if connection:
            self.remove(connection)

    def choose_test(self):
        connection = self.choose_connection(
            "Test Connection"
        )
        if connection:
            self.run(connection, "test")

    def duplicate(self, value, exclude=None):
        path = local_path(value)

        return any(
            c is not exclude and (
                c["name"] == value["name"]
                or local_path(c) == path
            )
            for c in self.connections
        )

    def add(self):
        dialog = ConnectionDialog()

        if dialog.exec():
            value = dialog.value()

            if self.duplicate(value):
                QMessageBox.warning(
                    None, "Duplicate Connection",
                    "Connection name and mount point "
                    "must be unique."
                )
                return

            self.connections.append(value)
            self.save()

            LOG.info("Added connection: %s", value["name"])
            self.refresh()

    def edit(self, connection):
        dialog = ConnectionDialog(data=connection)

        if dialog.exec():
            value = dialog.value()

            if self.duplicate(value, exclude=connection):
                QMessageBox.warning(
                    None, "Duplicate Connection",
                    "Connection name and mount point "
                    "must be unique."
                )
                return

            old_name = connection["name"]
            connection.update(value)

            self.health.pop(old_name, None)
            self.save()

            LOG.info("Edited connection: %s", value["name"])
            self.refresh()

    def remove(self, connection):
        answer = QMessageBox.question(
            None,
            "Remove Connection",
            f"Remove '{connection['name']}'?\n"
            "This will not delete any local files."
        )

        if answer == QMessageBox.StandardButton.Yes:
            self.connections.remove(connection)
            self.health.pop(connection["name"], None)
            self.save()

            LOG.info(
                "Removed connection: %s",
                connection["name"]
            )

            self.refresh()

    def automount(self):
        for connection in self.connections:
            if (
                connection.get("auto")
                and not mount_info(local_path(connection))
            ):
                self.run(connection, "mount")

    # --------------------------------------------------------
    # XFCE autostart
    # --------------------------------------------------------

    @staticmethod
    def desktop_quote(value):
        """Quote an argument for an XDG desktop Exec line."""
        value = str(value)
        value = value.replace("\\", "\\\\")
        value = value.replace('"', '\\"')
        value = value.replace("$", "\\$")
        value = value.replace("`", "\\`")
        value = value.replace("%", "%%")
        return f'"{value}"'

    def write_autostart(self):
        AUTOSTART_FILE.parent.mkdir(
            parents=True, exist_ok=True
        )

        python = Path(sys.executable).resolve()
        script = Path(__file__).resolve()

        command = (
            self.desktop_quote(python)
            + " "
            + self.desktop_quote(script)
        )

        contents = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=MountLink\n"
            "Comment=SSHFS Connection Manager for Linux\n"
            f"Exec={command}\n"
            "Icon=folder-remote-symbolic\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "X-XFCE-Autostart-enabled=true\n"
        )

        AUTOSTART_FILE.write_text(
            contents, encoding="utf-8"
        )

    def migrate_autostart(self):
        if not OLD_AUTOSTART.exists():
            return

        try:
            # Preserve the old enabled/disabled state.
            contents = OLD_AUTOSTART.read_text(
                encoding="utf-8"
            )

            disabled = (
                "Hidden=true" in contents
                or "X-GNOME-Autostart-enabled=false" in contents
                or "X-XFCE-Autostart-enabled=false" in contents
            )

            if not disabled and not AUTOSTART_FILE.exists():
                self.write_autostart()
                LOG.info("Migrated XFCE autostart")

            # Remove the legacy autostart entry only after
            # the replacement is present, or if disabled.
            if AUTOSTART_FILE.exists() or disabled:
                OLD_AUTOSTART.unlink()
                LOG.info("Removed legacy autostart entry")

        except OSError as exc:
            LOG.error("Autostart migration failed: %s", exc)

    def set_autostart(self, enabled):
        try:
            if enabled:
                self.write_autostart()
                LOG.info("XFCE autostart enabled")
            else:
                AUTOSTART_FILE.unlink(missing_ok=True)
                LOG.info("XFCE autostart disabled")

        except OSError as exc:
            QMessageBox.warning(
                None, "Autostart Error", str(exc)
            )

        self.refresh()

    # --------------------------------------------------------
    # Utilities
    # --------------------------------------------------------

    def open_log(self):
        try:
            subprocess.Popen(
                ["xdg-open", str(LOG_FILE)],
                start_new_session=True
            )
        except OSError as exc:
            QMessageBox.warning(
                None, "Log Error", str(exc)
            )

    def show_about(self):
        QMessageBox.about(
            None,
            f"About {APP_NAME}",
            f"{APP_NAME} {APP_VERSION}\n\n"
            f"{APP_DESCRIPTION}\n\n"
            "Manage SSHFS mounts from your Linux system tray.\n\n"
            "Built with Python and PySide6.\n"
            "Designed for XFCE."
        )


# ============================================================
# Entry point
# ============================================================

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("MountLink")

    try:
        migrate_files()
    except OSError as exc:
        QMessageBox.critical(
            None,
            "MountLink Migration Error",
            f"Could not migrate existing files:\n{exc}\n\n"
            "Your original files have not been deleted."
        )
        return 1

    global LOG
    LOG = setup_logging()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "MountLink",
            "No system tray detected.\n"
            "Enable the XFCE Status Tray plugin."
        )
        return 1

    manager = MountLink(app)
    manager.migrate_autostart()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
