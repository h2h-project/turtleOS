# scripts/tools_venv.sh — sourced by sync_turtleOS.sh and sync_airOS.sh
# ------------------------------------------------------------
# The tools venv (mpremote, esptool) that install_turtleOS.sh /
# install_airOS.sh create at $VENV_DIR. The installers can't source this file
# (they run before the repo is cloned), so they carry an identical inline
# copy — keep the two in step.
#
# Requires VENV_DIR to be set by the caller.

# A venv counts as ready only if its own python can run pip. Checking just for
# the folder misses a half-made venv — e.g. Debian/Ubuntu without the
# python3-venv package, where `python3 -m venv` fails after creating the
# folder but before installing pip — which then breaks every later run.
tools_venv_ready() {
    [[ -x "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1
}

# Create (or repair) the venv and bootstrap pip, with visible progress.
# Falls back to get-pip.py when this Python has no ensurepip, so no sudo is
# needed. Returns non-zero (after explaining why) if it still can't be done.
tools_ensure_venv() {
    tools_venv_ready && return 0

    if [[ -d "$VENV_DIR" ]]; then
        echo "  Found an incomplete Python environment — rebuilding it."
        rm -rf "$VENV_DIR"
    fi

    echo "  [1/2] Creating Python environment at $VENV_DIR ..."
    if ! { python3 -c "import ensurepip" >/dev/null 2>&1 && python3 -m venv "$VENV_DIR"; }; then
        rm -rf "$VENV_DIR"
        echo "  This Python has no built-in pip installer — downloading pip instead."
        if ! { python3 -m venv --without-pip "$VENV_DIR" \
               && curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$VENV_DIR/bin/python" - --quiet; }; then
            rm -rf "$VENV_DIR"
            local pyver
            pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 3)"
            echo
            echo "Could not set up a Python environment for the installer tools."
            echo "Install Python's venv support, then run this again:"
            echo "  Ubuntu / Debian:  sudo apt install python3-venv python${pyver}-venv"
            echo "  Fedora:           sudo dnf install python3-pip"
            echo "  Mac:              brew install python"
            return 1
        fi
    fi

    echo "  [2/2] Upgrading pip inside the environment..."
    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    echo "  Environment ready."
    echo
}

# Install a package into the venv with visible pip output.
tools_venv_install() {
    local pkg="$1"
    echo "  Installing $pkg (this may take a minute on a slow connection)..."
    "$VENV_DIR/bin/python" -m pip install "$pkg"
}

# Find mpremote: PATH, then the installer's venv, then ~/venvs/micropython;
# otherwise install it into the venv. Sets MPREMOTE, returns 1 on failure.
find_or_install_mpremote() {
    if command -v mpremote >/dev/null 2>&1; then
        MPREMOTE="mpremote"
    elif [[ -x "$VENV_DIR/bin/mpremote" ]]; then
        MPREMOTE="$VENV_DIR/bin/mpremote"
    elif [[ -x "$HOME/venvs/micropython/bin/mpremote" ]]; then
        MPREMOTE="$HOME/venvs/micropython/bin/mpremote"
    else
        echo "mpremote not found — setting up a local Python environment."
        echo "(Tools stay in $VENV_DIR and do not affect your system Python.)"
        echo
        tools_ensure_venv || return 1
        tools_venv_install mpremote || return 1
        MPREMOTE="$VENV_DIR/bin/mpremote"
        echo "  ✓  mpremote ready"
    fi
}
