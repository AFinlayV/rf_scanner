#!/usr/bin/env python3
"""
Build a macOS .app bundle for RF Scanner and install it to /Applications.

Run once:  python3 build_app.py
"""

import os
import shutil
import stat
import subprocess
import sys
import textwrap

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE        = os.path.dirname(os.path.abspath(__file__))
ICNS_SRC    = os.path.join(HERE, "assets", "AppIcon.icns")
APP_NAME    = "RF Scanner"
APP_DIR     = f"/Applications/{APP_NAME}.app"

# Use the windowed Framework Python — the CLI python3 binary doesn't register
# as a foreground GUI process when launched by Finder, so the window never
# appears on double-click. Python.app/Contents/MacOS/Python is the right one.
_fw = "/Library/Frameworks/Python.framework/Versions"
_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
_windowed = f"{_fw}/{_ver}/Resources/Python.app/Contents/MacOS/Python"
PYTHON_BIN = _windowed if os.path.isfile(_windowed) else sys.executable
ENTRY_POINT = os.path.join(HERE, "rf_scanner.py")


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if not os.path.isfile(ICNS_SRC):
    print("✗  assets/AppIcon.icns not found — run make_icon.py first")
    sys.exit(1)

if not os.path.isfile(ENTRY_POINT):
    print(f"✗  Entry point not found: {ENTRY_POINT}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Build directory structure
# ---------------------------------------------------------------------------
macos_dir     = os.path.join(APP_DIR, "Contents", "MacOS")
resources_dir = os.path.join(APP_DIR, "Contents", "Resources")

for d in (macos_dir, resources_dir):
    os.makedirs(d, exist_ok=True)

print(f"Building {APP_DIR} …")


# ---------------------------------------------------------------------------
# Info.plist
# ---------------------------------------------------------------------------
plist_path = os.path.join(APP_DIR, "Contents", "Info.plist")
plist_content = textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>CFBundleName</key>
        <string>{APP_NAME}</string>
        <key>CFBundleDisplayName</key>
        <string>{APP_NAME}</string>
        <key>CFBundleIdentifier</key>
        <string>com.localuser.rfscanner</string>
        <key>CFBundleVersion</key>
        <string>2.0</string>
        <key>CFBundleShortVersionString</key>
        <string>2.0</string>
        <key>CFBundlePackageType</key>
        <string>APPL</string>
        <key>CFBundleExecutable</key>
        <string>RF Scanner</string>
        <key>CFBundleIconFile</key>
        <string>AppIcon</string>
        <key>NSPrincipalClass</key>
        <string>NSApplication</string>
        <key>NSHighResolutionCapable</key>
        <true/>
        <key>LSMinimumSystemVersion</key>
        <string>10.14</string>
        <key>LSUIElement</key>
        <false/>
        <key>NSRequiresAquaSystemAppearance</key>
        <false/>
    </dict>
    </plist>
""")

with open(plist_path, "w") as f:
    f.write(plist_content)
print("  ✓  Info.plist")


# ---------------------------------------------------------------------------
# Launcher executable (shell script)
# ---------------------------------------------------------------------------
launcher_path = os.path.join(macos_dir, APP_NAME)
launcher_content = textwrap.dedent(f"""\
    #!/bin/bash
    # RF Scanner launcher
    # Runs the Python entry-point using the same interpreter that was used
    # when build_app.py was run (i.e. the one with all required packages).

    PYTHON="{PYTHON_BIN}"
    SCRIPT="{ENTRY_POINT}"

    # Activate the parent dir so relative imports resolve
    cd "$(dirname "$SCRIPT")"

    exec "$PYTHON" "$SCRIPT" "$@"
""")

with open(launcher_path, "w") as f:
    f.write(launcher_content)

# Make executable
st = os.stat(launcher_path)
os.chmod(launcher_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print("  ✓  MacOS/RF Scanner  (launcher)")


# ---------------------------------------------------------------------------
# Copy icon
# ---------------------------------------------------------------------------
icns_dst = os.path.join(resources_dir, "AppIcon.icns")
shutil.copy2(ICNS_SRC, icns_dst)
print("  ✓  Resources/AppIcon.icns")


# ---------------------------------------------------------------------------
# Touch the bundle so Finder / LaunchServices picks up the new icon
# ---------------------------------------------------------------------------
subprocess.run(["touch", APP_DIR], check=False)
subprocess.run(
    ["killall", "Dock"],
    check=False, capture_output=True,
)

print(f"\n✓  Installed → {APP_DIR}")
print("   You can now launch RF Scanner from Spotlight or the Applications folder.")
print("   (If the icon looks blank in Finder, log out and back in once to rebuild the icon cache.)")
