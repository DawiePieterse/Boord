#!/bin/bash

# Script to push Boord background image to Android phone via ADB
# Requirements: ADB installed and phone connected with USB debugging enabled

IMAGE_FILE="boord-background.jpg"
DESTINATION="/sdcard/Download/boord-background.jpg"

# Check if ADB is available
if ! command -v adb &> /dev/null; then
    echo "Error: ADB (Android Debug Bridge) is not installed or not in PATH."
    echo "Install Android Platform Tools from: https://developer.android.com/tools/releases/platform-tools"
    exit 1
fi

# Check if the image file exists in the same folder as this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_PATH="${SCRIPT_DIR}/${IMAGE_FILE}"

if [ ! -f "$IMAGE_PATH" ]; then
    echo "Error: Image file not found: $IMAGE_PATH"
    echo "Make sure 'boord-background.jpg' is in the same folder as this script."
    exit 1
fi

echo "Checking for connected Android devices..."
adb devices

# Check if a device is connected
DEVICE_COUNT=$(adb devices | grep -w "device" | wc -l)
if [ "$DEVICE_COUNT" -eq 0 ]; then
    echo ""
    echo "No Android device detected."
    echo "1. Connect your phone via USB"
    echo "2. Enable USB debugging (Settings > Developer options)"
    echo "3. Allow the computer when prompted on the phone"
    echo "4. Run this script again"
    exit 1
fi

echo ""
echo "Pushing image to phone..."
adb push "$IMAGE_PATH" "$DESTINATION"

if [ $? -eq 0 ]; then
    echo ""
    echo "Success! Image copied to: $DESTINATION"
    echo "You can find it in the Downloads folder on your phone."
    echo ""
    echo "Optional: Open the Downloads folder on the phone or use a file manager app."
else
    echo ""
    echo "Failed to push the file. Check the connection and try again."
    exit 1
fi
