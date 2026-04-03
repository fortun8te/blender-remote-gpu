#!/bin/bash
# Build script for Blender Remote GPU addon
# Creates versioned ZIP file for distribution
# Auto-increments build number on each build

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Extract current build info from addon/__init__.py
CURRENT_BUILD=$(grep -E "^BUILD = " addon/__init__.py | cut -d'"' -f2)
VERSION=$(grep -E "^__version__ = " addon/__init__.py | cut -d'"' -f2)

# Extract build number (b4 -> 4)
BUILD_NUM=${CURRENT_BUILD#b}

# Increment build number
NEW_BUILD_NUM=$((BUILD_NUM + 1))
NEW_BUILD="b${NEW_BUILD_NUM}"
NEW_VERSION="1.0.${NEW_BUILD_NUM}"
BUILD_DATE=$(date +"%Y-%m-%d")

# Update __init__.py with new build info
sed -i '' "s/^__version__ = .*/\__version__ = \"${NEW_VERSION}\"/" addon/__init__.py
sed -i '' "s/^BUILD = .*/BUILD = \"${NEW_BUILD}\"/" addon/__init__.py
sed -i '' "s/^BUILD_DATE = .*/BUILD_DATE = \"${BUILD_DATE}\"/" addon/__init__.py

echo "Building Blender Remote GPU addon"
echo "   Old build: $CURRENT_BUILD -> New build: $NEW_BUILD"
echo "   Version: $NEW_VERSION"
echo "   Date: $BUILD_DATE"

# Clean old builds
rm -f blender_remote_gpu_addon*.zip

# Create ZIP — NO external modules needed (zero dependencies)
ZIP_NAME="blender_remote_gpu_addon_${NEW_BUILD}.zip"
echo "Creating: $ZIP_NAME"

zip -r "$ZIP_NAME" addon/ \
    -x "addon/__pycache__/*" \
    -x "addon/*.pyc" \
    -x "addon/modules/*" \
    > /dev/null 2>&1

# Verify
if [ -f "$ZIP_NAME" ]; then
    SIZE=$(du -h "$ZIP_NAME" | cut -f1)
    echo "Build successful: $ZIP_NAME ($SIZE)"
    echo ""
    echo "Contents:"
    unzip -l "$ZIP_NAME"
else
    echo "Build failed"
    exit 1
fi

# Create latest symlink
ln -sf "$ZIP_NAME" blender_remote_gpu_addon_latest.zip
echo "Symlink: blender_remote_gpu_addon_latest.zip -> $ZIP_NAME"
