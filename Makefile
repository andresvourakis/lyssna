# Lyssna: common commands. Run `make` to list them.
.DEFAULT_GOAL := help
.PHONY: help test run doctor app app-intel release open quit restart clean

APP := /Applications/Lyssna.app

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

test:            ## run the test suite (on a Mac, the legacy AppKit tests in a second process)
	uv run pytest -q
	@if [ "$$(uname)" = Darwin ]; then uv run pytest -q tests/legacy; fi

run:             ## run the menu bar app from source (Accessibility goes to your terminal)
	uv run lyssna

doctor:          ## check keys, permissions, and do a test synthesis
	uv run lyssna doctor

app:             ## build the shareable zip and install to /Applications
	./scripts/build_app.sh

app-intel:       ## build the Intel (x86_64) zip for Intel Macs
	./scripts/build_app.sh x86_64

release:         ## build both release zips (not installed) and print the gh command
	./scripts/release.sh

open:            ## launch the installed Lyssna.app
	open "$(APP)"

quit:            ## quit the running Lyssna (any instance)
	-uv run lyssna quit

restart: quit    ## quit, then launch the installed Lyssna.app
	sleep 1
	open "$(APP)"

clean:           ## remove build output
	rm -rf build dist scripts/Lyssna.icns scripts/Lyssna.png
