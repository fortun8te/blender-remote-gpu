#!/bin/bash
# Quick addon update script
# Run on remote computer: ./update.sh
# Then reload addon in Blender (Edit → Preferences → Add-ons, toggle off/on Remote GPU Renderer)

echo "🔄 Updating addon from GitHub..."
git pull

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Addon code updated successfully!"
    echo ""
    echo "📝 Next steps:"
    echo "  1. In Blender, go to: Edit → Preferences → Add-ons"
    echo "  2. Search for 'Remote GPU'"
    echo "  3. Toggle the addon OFF then back ON (to reload)"
    echo "  4. Check Preferences to verify connection status"
    echo ""
    echo "💡 Tip: Watch the System Console (Window → Toggle System Console) to see connection logs"
else
    echo "❌ Update failed. Check your git configuration."
    exit 1
fi
