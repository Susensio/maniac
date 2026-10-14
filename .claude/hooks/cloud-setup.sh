#!/bin/bash
# Make a Claude Code cloud container able to run `just check`.
#
# The image lacks `just` and `groff`, ships `man` as the minimized-image stub
# and pandoc older than the 3.1.10 floor (ADR-0064), so the integration suite
# cannot run. Each step checks first, so a container that already has
# everything pays well under a second. Does nothing outside a cloud session.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PANDOC_VERSION=3.6.4
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

apt_updated=""
apt_install() {
  if [ -z "$apt_updated" ]; then
    $SUDO apt-get update -qq
    apt_updated=1
  fi
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq "$@" >/dev/null
}

installed() {
  # `dpkg -s` also succeeds for a package removed with its config files kept.
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

command -v just >/dev/null || uv tool install -q --reinstall rust-just

# The minimized image diverts `man` to a stub that only prints a notice.
if dpkg-divert --list /usr/bin/man 2>/dev/null | grep -q /usr/bin/man; then
  $SUDO rm -f /usr/bin/man /etc/dpkg/dpkg.cfg.d/excludes
  $SUDO dpkg-divert --quiet --remove --rename /usr/bin/man
fi
installed man-db || apt_install man-db
installed groff || apt_install groff

pandoc_ok() {
  command -v pandoc >/dev/null || return 1
  local have
  have=$(pandoc --version | head -1 | cut -d' ' -f2)
  [ "$(printf '%s\n3.1.10\n' "$have" | sort -V | head -1)" = "3.1.10" ]
}
if ! pandoc_ok; then
  deb=$(mktemp --suffix=.deb)
  curl -fsSL -o "$deb" "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-$(dpkg --print-architecture).deb"
  $SUDO dpkg -i "$deb" >/dev/null
  rm -f "$deb"
fi

cd "$CLAUDE_PROJECT_DIR"
uv sync -q
