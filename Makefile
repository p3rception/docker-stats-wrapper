PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin
REPO   := $(abspath $(CURDIR))

.PHONY: install uninstall test

install:
	@mkdir -p $(BIN)
	@printf '#!/bin/sh\nexec python3 "%s" "$$@"\n' '$(REPO)' > $(BIN)/dsw
	@chmod +x $(BIN)/dsw
	@echo "Installed: $(BIN)/dsw -> $(REPO)"
	@case ":$$PATH:" in *":$(BIN):"*) ;; *) \
		echo "Note: $(BIN) is not on your PATH - add it to use 'dsw' directly.";; esac

uninstall:
	@rm -f $(BIN)/dsw && echo "Removed: $(BIN)/dsw"

test:
	@python3 -m pytest -q
