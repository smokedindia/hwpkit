HWPKIT_JAR ?= $(HOME)/.local/share/hwpkit/hwp2hwpx-all.jar
HWPKIT_CACHE ?= $(HOME)/.cache/hwpkit

.PHONY: install clean

## install: fetch pinned hwp2hwpx + deps, build and smoke-test the headless converter JAR
install:
	HWPKIT_JAR="$(HWPKIT_JAR)" HWPKIT_CACHE="$(HWPKIT_CACHE)" bash "$(CURDIR)/java/build.sh"

## clean: remove build outputs (keeps downloaded sources and jars in the cache)
clean:
	rm -rf "$(HWPKIT_CACHE)/build"
	rm -f "$(HWPKIT_JAR)"
