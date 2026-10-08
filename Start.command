#!/bin/zsh
# Double-click to open the controller. Terminal: ./Start.command --flash
cd "${0:A:h}" || exit 1
project="$PWD"
export PLATFORMIO_SETTING_ENABLE_TELEMETRY=no
if [[ "$1" == "--flash" || "$1" == "--build" ]]; then
    action=()
    [[ "$1" == "--flash" ]] && action=(-t upload)
    if command -v pio >/dev/null 2>&1; then
        pio run -d "$project" "${action[@]}"
    elif [[ -x "$project/.venv/bin/python" ]]; then
        PLATFORMIO_CORE_DIR="$project/.platformio" "$project/.venv/bin/python" -m platformio run -d "$project" "${action[@]}"
    else
        print "PlatformIO is required. Install with: python3 -m pip install platformio"
        exit 1
    fi
    exit $?
fi
for interpreter in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$project/.gui-venv/bin/python" python3; do
    if "$interpreter" -c 'import tkinter' >/dev/null 2>&1; then
        if ! "$interpreter" -c 'import serial' >/dev/null 2>&1; then
            print "Offline preview available. For hardware connection install: $interpreter -m pip install pyserial"
        fi
        "$interpreter" "$project/controller.py"
        result=$?
        if (( result != 0 )); then
            read "reply?Controller stopped. Press Enter to close. "
        fi
        exit $result
    fi
done
print "Python with Tkinter is required. See README.md for setup."
read "reply?Press Enter to close. "
exit 1
