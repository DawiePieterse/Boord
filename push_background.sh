#!/bin/bash

# Push this farm's own background/logo image to an Android device via ADB,
# for use as the wallpaper on field phones.
#
# The image is NOT part of this repository. It is one farm's branding, so it
# lives in data/imports/ - gitignored, per-farm - alongside that farm's other
# own material. Drop yours in as data/imports/background.jpg, or pass a path
# as the first argument.
#
# Requirements: ADB installed and phone connected with USB debugging enabled

DEFAULT_IMAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/data/imports/background.jpg"
IMAGE_PATH="${1:-$DEFAULT_IMAGE}"
IMAGE_FILE="$(basename "$IMAGE_PATH")"
DESTINATION="/sdcard/Download/$IMAGE_FILE"

# Check if ADB is available
if ! command -v adb &> /dev/null; then
    echo "Error: ADB (Android Debug Bridge) is not installed or not in PATH."
    echo "Install Android Platform Tools from: https://developer.android.com/tools/releases/platform-tools"
    exit 1
fi

if [ ! -f "$IMAGE_PATH" ]; then
    echo "No image found at: $IMAGE_PATH"
    echo
    echo "This is normal on a new install - the image is this farm's own"
    echo "branding and is not shipped with the app. Put yours at"
    echo "data/imports/background.jpg, or pass a path as the first argument."
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
