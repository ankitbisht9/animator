# Root task runner for the polyglot monorepo (Python via uv, TypeScript via pnpm).
# Recipes only invoke pnpm / uv / node / git so they behave the same under
# cmd.exe (GNU make on Windows), PowerShell-launched make, and POSIX sh.

UV ?= uv
PNPM ?= pnpm

.PHONY: help setup dev build build-worker build-web test test-ts test-py test-e2e \
        lint typecheck format codegen check-codegen export-demo eval spikes clean

help:
	@echo setup         install JS + Python deps, build the engine worker, install Chromium
	@echo dev           run the web app (Vite) and the API (FastAPI) together
	@echo test          all offline tests: TypeScript, Python, browser golden frames
	@echo lint          Biome + tsc, Ruff + mypy
	@echo codegen       regenerate contracts/ and generated docs
	@echo check-codegen fail if codegen output differs from what is committed
	@echo export-demo   render the Archimedes lesson to MP4 on this machine
	@echo eval          run the live eval set (calls OpenAI; ARGS="--only archimedes")
	@echo export        render LESSON=slug to MP4 on this machine
	@echo export-demo-docker  same as export-demo, inside the pinned Docker image (TODO: not yet verified)
	@echo docker-build  build the pinned export image
	@echo test-docker   run the offline test suite inside the pinned image

setup:
	$(PNPM) install --frozen-lockfile
	$(PNPM) run build:worker
	$(UV) sync --all-packages --frozen
	$(UV) run playwright install chromium

dev: build-worker
	$(PNPM) run dev

build: build-worker build-web

build-worker:
	$(PNPM) run build:worker

build-web:
	$(PNPM) run build:web

test: test-ts test-py test-e2e

test-ts:
	$(PNPM) run test

test-py: build-worker
	$(UV) run pytest -m "not e2e and not live"

test-e2e: build
	$(UV) run pytest -m "e2e and not live"

lint:
	$(PNPM) run lint
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

typecheck:
	$(PNPM) run typecheck
	$(UV) run mypy

format:
	$(PNPM) run format
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

codegen:
	$(PNPM) run codegen
	$(UV) run python scripts/codegen_python.py

check-codegen: codegen
	git diff --exit-code -- contracts docs/DSL.md docs/COMPONENTS.md

docker-build:
	$(DOCKER) build -f docker/export.Dockerfile -t $(EXPORT_IMAGE) .

export-demo: build
	$(UV) run python -m animator.export.demo

export: build
	$(UV) run python -m animator.export --lesson $(LESSON)

# TODO(Phase 6, ADR-026): the pinned image is written but has not been built and verified yet.
export-demo-docker: docker-build
	$(DOCKER_RUN) -v "$(CURDIR)/var/exports:/app/var/exports" $(EXPORT_IMAGE) python -m animator.export.demo

test-docker: docker-build
	$(DOCKER_RUN) $(EXPORT_IMAGE) python -m pytest -m "not live"

goldens-docker: docker-build
	$(DOCKER_RUN) -e UPDATE_GOLDENS=1 -v "$(CURDIR)/apps/api/tests/golden:/app/apps/api/tests/golden" 		$(EXPORT_IMAGE) python -m pytest -m e2e apps/api/tests/test_render_e2e.py

# Live: calls OpenAI (needs OPENAI_API_KEY). Pass options with ARGS, e.g. ARGS="--only archimedes".
eval: build-worker
	$(UV) run python -m animator.eval $(ARGS)

spikes: build
	$(UV) run python scripts/spikes/export_throughput.py
	$(PNPM) --filter @animator/engine exec tsx scripts/spikes/mathjax_determinism.ts
	$(PNPM) --filter @animator/engine exec tsx scripts/spikes/strict_schema_check.ts

clean:
	$(PNPM) -r exec node -e "require('fs').rmSync('dist',{recursive:true,force:true})"
