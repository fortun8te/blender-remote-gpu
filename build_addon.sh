#!/bin/bash
# Build script for Blender Remote GPU addon
# Creates versioned ZIP file for distribution

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Extract build info from addon/__init__.py
BUILD=$(grep -E "^BUILD = " addon/__init__.py | cut -d'"' -f2)
VERSION=$(grep -E "^__version__ = " addon/__init__.py | cut -d'"' -f2)

echo "🔨 Building Blender Remote GPU addon"
echo "   Version: $VERSION"
echo "   Build: $BUILD"

# Clean old builds
rm -f blender_remote_gpu_addon*.zip

# Create ZIP with version in filename
ZIP_NAME="blender_remote_gpu_addon_${BUILD}.zip"
echo "📦 Creating: $ZIP_NAME"

# Zip only the addon folder (not server/ or shared/)
zip -r "$ZIP_NAME" addon/ -x "addon/__pycache__/*" "addon/*.pyc" "addon/modules/*" > /dev/null 2>&1

# Verify
if [ -f "$ZIP_NAME" ]; then
    SIZE=$(du -h "$ZIP_NAME" | cut -f1)
    echo "✅ Build successful: $ZIP_NAME ($SIZE)"

    # Show contents
    echo ""
    echo "📋 Contents:"
    unzip -l "$ZIP_NAME" | head -15
else
    echo "❌ Build failed"
    exit 1
fi

# Also create latest symlink
ln -sf "$ZIP_NAME" blender_remote_gpu_addon_latest.zip
echo "🔗 Symlink: blender_remote_gpu_addon_latest.zip → $ZIP_NAME"
