#!/usr/bin/env bash
# Installs two LaunchAgents: a daily pipeline run (06:30) and the always-on web app (http://127.0.0.1:7777).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/data/logs"

cat > "$AGENTS/com.frontier.daily.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.frontier.daily</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run_daily.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/data/logs/daily.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/logs/daily.log</string>
</dict></plist>
PLIST

cat > "$AGENTS/com.frontier.web.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.frontier.web</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-m</string><string>radar</string><string>serve</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/data/logs/web.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/logs/web.log</string>
</dict></plist>
PLIST

for job in daily web; do
  launchctl bootout "gui/$(id -u)/com.frontier.$job" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/com.frontier.$job.plist"
done
echo "Installed. Web app: http://127.0.0.1:7777 · logs: $ROOT/data/logs/"
