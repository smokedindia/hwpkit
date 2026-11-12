#!/usr/bin/env bash
# Build the headless HWP->HWPX converter JAR (neolord0/hwp2hwpx + hwplib + hwpxlib + kr.hwpkit.Convert).
# Idempotent: safe to re-run. Needs git, curl, unzip, python3 and a JDK (Homebrew openjdk by default).
# No Maven/Gradle: plain javac + jar.
#
# Env overrides:
#   JAVA_HOME          JDK to use (default: $(brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home)
#   HWPKIT_CACHE       download/build cache   (default: ~/.cache/hwpkit)
#   HWPKIT_JAR         output JAR             (default: ~/.local/share/hwpkit/hwp2hwpx-all.jar)
#   HWPKIT_SMOKE_HWP   .hwp used for the post-build smoke test (default: upstream test/table/from.hwp)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHA="$(tr -d '[:space:]' < "$HERE/UPSTREAM_SHA")"
CACHE="${HWPKIT_CACHE:-$HOME/.cache/hwpkit}"
JAR_OUT="${HWPKIT_JAR:-$HOME/.local/share/hwpkit/hwp2hwpx-all.jar}"
SRC="$CACHE/src/hwp2hwpx"
LIB="$CACHE/lib"
BUILD="$CACHE/build/$SHA"
MAVEN="https://repo1.maven.org/maven2"
UPSTREAM_URL="https://github.com/neolord0/hwp2hwpx.git"

log() { printf 'build.sh: %s\n' "$*" >&2; }
die() { printf 'build.sh: ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "UPSTREAM_SHA must be a full 40-char SHA, got '$SHA'"
for tool in git curl unzip python3 shasum; do
  command -v "$tool" >/dev/null || die "missing required tool: $tool"
done

# --- JDK -------------------------------------------------------------------
if [[ -z "${JAVA_HOME:-}" ]]; then
  command -v brew >/dev/null || die "JAVA_HOME unset and Homebrew not found; install a JDK 17+ (brew install openjdk)"
  JAVA_HOME="$(brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home"
fi
for b in javac jar java; do
  [[ -x "$JAVA_HOME/bin/$b" ]] || die "$JAVA_HOME/bin/$b not found; run 'brew install openjdk' or set JAVA_HOME"
done
export JAVA_HOME
log "JDK: $("$JAVA_HOME/bin/javac" -version 2>&1) at $JAVA_HOME"

# --- upstream source at the pinned SHA (shallow, blob-filtered, sparse) ----
mkdir -p "$CACHE/src" "$LIB"
if [[ "$(git -C "$SRC" rev-parse -q --verify HEAD 2>/dev/null || true)" != "$SHA" ]]; then
  log "fetching neolord0/hwp2hwpx @ $SHA"
  if [[ ! -d "$SRC/.git" ]]; then
    rm -rf "$SRC"
    git init -q "$SRC"
    git -C "$SRC" remote add origin "$UPSTREAM_URL"
  fi
  # Only what we build plus one small fixture; the repo's test corpus is large.
  git -C "$SRC" sparse-checkout set --no-cone '/pom.xml' '/license*' '/LICENSE*' '/src/main/java/' '/test/table/'
  git -C "$SRC" fetch -q --depth 1 --filter=blob:none origin "$SHA"
  git -C "$SRC" -c advice.detachedHead=false checkout -q --force --detach FETCH_HEAD
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SHA" ]] || die "upstream checkout is not at $SHA"
[[ -d "$SRC/src/main/java" ]] || die "upstream checkout has no src/main/java"

# --- dependency versions from the pinned pom.xml ----------------------------
read -r HWPLIB_V HWPXLIB_V < <(python3 -I - "$SRC/pom.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
ns = {"m": "http://maven.apache.org/POM/4.0.0"}
root = ET.parse(sys.argv[1]).getroot()
v = {}
for d in root.iterfind("m:dependencies/m:dependency", ns):
    if d.findtext("m:groupId", "", ns) == "kr.dogfoot":
        v[d.findtext("m:artifactId", "", ns)] = d.findtext("m:version", "", ns)
print(v["hwplib"], v["hwpxlib"])
PY
)
log "deps from pom.xml: hwplib $HWPLIB_V, hwpxlib $HWPXLIB_V"

# Download + verify one artifact file (jar or pom) against Maven Central's .sha1.
fetch_verified() { # $1 group path, $2 artifact, $3 version, $4 ext
  local name="$2-$3.$4" url="$MAVEN/$1/$2/$3/$2-$3.$4" want got
  want="$(curl -fsSL "$url.sha1" | awk '{print $1}')" || die "cannot fetch $url.sha1"
  [[ "$want" =~ ^[0-9a-f]{40}$ ]] || die "bad sha1 file for $name: '$want'"
  if [[ ! -f "$LIB/$name" ]] || [[ "$(shasum -a 1 "$LIB/$name" | awk '{print $1}')" != "$want" ]]; then
    curl -fsSL -o "$LIB/$name.part" "$url" || die "download failed: $url"
    mv "$LIB/$name.part" "$LIB/$name"
  fi
  got="$(shasum -a 1 "$LIB/$name" | awk '{print $1}')"
  [[ "$got" == "$want" ]] || { rm -f "$LIB/$name"; die "sha1 mismatch for $name: got $got want $want"; }
  log "verified $name sha1=$got"
}

DEP_JARS=()
for spec in "hwplib:$HWPLIB_V" "hwpxlib:$HWPXLIB_V"; do
  a="${spec%%:*}"; v="${spec##*:}"
  fetch_verified kr/dogfoot "$a" "$v" pom
  fetch_verified kr/dogfoot "$a" "$v" jar
  # Fail loudly if a dependency gains runtime (non-test/provided) transitive deps we do not bundle.
  extra="$(python3 -I - "$LIB/$a-$v.pom" <<'PY'
import sys, xml.etree.ElementTree as ET
ns = {"m": "http://maven.apache.org/POM/4.0.0"}
root = ET.parse(sys.argv[1]).getroot()
for d in root.iterfind("m:dependencies/m:dependency", ns):
    if d.findtext("m:scope", "compile", ns) not in ("test", "provided") and d.findtext("m:optional", "false", ns) != "true":
        print(f'{d.findtext("m:groupId", "", ns)}:{d.findtext("m:artifactId", "", ns)}:{d.findtext("m:version", "", ns)}')
PY
)"
  [[ -z "$extra" ]] || die "$a-$v has runtime dependencies not handled by build.sh: $extra"
  DEP_JARS+=("$LIB/$a-$v.jar")
done
CP="$(IFS=:; echo "${DEP_JARS[*]}")"

# --- compile ----------------------------------------------------------------
rm -rf "$BUILD"
mkdir -p "$BUILD/classes" "$BUILD/fat/kr/hwpkit"
find "$SRC/src/main/java" "$HERE/src" -name '*.java' > "$BUILD/sources.txt"
log "compiling $(wc -l < "$BUILD/sources.txt" | tr -d ' ') sources with --release 17"
"$JAVA_HOME/bin/javac" --release 17 -encoding UTF-8 -nowarn -Xlint:none -cp "$CP" \
  -d "$BUILD/classes" @"$BUILD/sources.txt" 2> "$BUILD/javac.log" || { cat "$BUILD/javac.log" >&2; die "javac failed"; }
grep -v '^Note:' "$BUILD/javac.log" >&2 || true

# --- fat jar ----------------------------------------------------------------
for j in "${DEP_JARS[@]}"; do
  unzip -q -o "$j" -d "$BUILD/fat" -x 'META-INF/MANIFEST.MF'
done
# Drop any jar-signature files: they would invalidate the merged jar.
find "$BUILD/fat/META-INF" -maxdepth 1 \( -name '*.SF' -o -name '*.RSA' -o -name '*.DSA' -o -name '*.EC' \) -delete
cp -R "$BUILD/classes/." "$BUILD/fat/"
find "$SRC" -maxdepth 1 -iname "license*" -exec cp {} "$BUILD/fat/META-INF/hwp2hwpx-LICENSE.txt" \;
cat > "$BUILD/fat/kr/hwpkit/build-info.properties" <<EOF
upstream.sha=$SHA
hwplib.version=$HWPLIB_V
hwpxlib.version=$HWPXLIB_V
EOF
cat > "$BUILD/manifest.txt" <<EOF
Main-Class: kr.hwpkit.Convert
Hwpkit-Upstream-Sha: $SHA
Hwplib-Version: $HWPLIB_V
Hwpxlib-Version: $HWPXLIB_V
EOF
mkdir -p "$(dirname "$JAR_OUT")"
TMP_JAR="$JAR_OUT.tmp-$$"
trap 'rm -f "$TMP_JAR"' EXIT
"$JAVA_HOME/bin/jar" --create --file "$TMP_JAR" --manifest "$BUILD/manifest.txt" -C "$BUILD/fat" .

# --- smoke test (on the temp jar, before it replaces the installed one) -----
SMOKE_IN="${HWPKIT_SMOKE_HWP:-$SRC/test/table/from.hwp}"
[[ -f "$SMOKE_IN" ]] || die "smoke fixture missing: $SMOKE_IN"
SMOKE_OUT="$BUILD/smoke.hwpx"
log "smoke test: $SMOKE_IN"
"$JAVA_HOME/bin/java" -jar "$TMP_JAR" "$SMOKE_IN" "$SMOKE_OUT" || die "smoke conversion failed (exit $?)"
unzip -Z1 "$SMOKE_OUT" > "$BUILD/smoke.list" || die "smoke output is not a valid zip"
grep -qx 'Contents/section0.xml' "$BUILD/smoke.list" || die "smoke output has no Contents/section0.xml"
[[ "$("$JAVA_HOME/bin/java" -jar "$TMP_JAR" --version)" == "$SHA" ]] || die "--version does not report $SHA"

mv -f "$TMP_JAR" "$JAR_OUT"
trap - EXIT
log "OK: $JAR_OUT ($(du -h "$JAR_OUT" | awk '{print $1}')) upstream=$SHA hwplib=$HWPLIB_V hwpxlib=$HWPXLIB_V"
