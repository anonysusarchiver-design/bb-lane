#!/usr/bin/env bash
# emz5/build-jsshell.sh — build a standalone SpiderMonkey js shell from source
# on an ubuntu GitHub runner (public mirror, no auth). Places the binary at
# <workdir>/jsshell-source/js and writes jsshell-source/js-path.txt.
#
# usage: build-jsshell.sh [configure-flags-string]
#   default flags: --disable-debug --enable-optimize
#   fuzzing flavor: --enable-fuzzing --enable-address-sanitizer --disable-debug
set -euo pipefail

FLAGS="${1:---disable-debug --enable-optimize}"
WORK="$(pwd)"

echo "== free disk (pre-clean) =="
df -h / | tail -1
sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL /usr/local/share/boost || true
sudo apt-get clean || true
echo "== free disk (post-clean) =="
df -h / | tail -1

echo "== install build deps =="
sudo apt-get update -qq
sudo apt-get install -y -qq clang lld m4 autoconf2.13 zlib1g-dev
(clang --version | head -1) || true

echo "== clone mozilla-central (shallow, public GitHub mirror) =="
git clone --depth 1 https://github.com/mozilla-firefox/firefox.git mc
git -C mc rev-parse HEAD | tee "$WORK/jsshell-source-rev.txt"

echo "== configure + build js/src =="
cd mc/js/src
export MOZBUILD_STATE_PATH="$HOME/.mozbuild"
# shellcheck disable=SC2086
./mach configure $FLAGS
./mach build
cd "$WORK"

echo "== locate shell =="
JS=$(find mc/js/src -type f -path "*/dist/bin/js" | head -1 || true)
if [ -z "$JS" ]; then JS=$(find mc/js/src -type f -name js -perm -u+x | head -1 || true); fi
if [ -z "$JS" ]; then echo "BUILD-ERROR: js binary not found"; exit 1; fi

mkdir -p jsshell-source
cp "$JS" jsshell-source/js
chmod +x jsshell-source/js
{
  echo "$WORK/jsshell-source/js"
  echo "source=$(cat "$WORK/jsshell-source-rev.txt")"
} > jsshell-source/js-path.txt
ls -l jsshell-source/js
echo "BUILD-OK bin=$WORK/jsshell-source/js"
