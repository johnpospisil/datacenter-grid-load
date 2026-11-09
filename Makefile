.PHONY: explorer social report data era5 era5-status era5-watch era5-stop features models figures test lint all

# Runs in the ~/tf virtualenv (override: make PYTHON=/path/to/python)
PYTHON ?= $(HOME)/tf/bin/python
PY := $(PYTHON) -m

data:
	$(PY) glg.ingest.eia930
	$(PY) glg.ingest.pjm_load
	$(PY) glg.ingest.ferc714
	$(PY) glg.ingest.eia861
	$(PY) glg.ingest.census
	$(PY) glg.ingest.isd
	$(PY) glg.ingest.employment
	$(PY) glg.ingest.eia860m
	$(PY) glg.ingest.queues
	$(PY) glg.ingest.iso_markets

# ERA5 weather: a long, queued download, so it runs detached (survives closing the terminal).
era5:
	@mkdir -p data/raw/era5
	nohup $(PY) glg.ingest.weather download >> data/raw/era5/nohup.out 2>&1 &
	@echo "ERA5 download started in the background. Watch it with: make era5-watch"

era5-status:
	@$(PY) glg.ingest.weather status

era5-watch:
	@$(PY) glg.ingest.weather status --watch

era5-stop:
	-pkill -f "glg.ingest.weather download" && echo "Stopped (rerun 'make era5' to resume)."

features:
	$(PY) glg.build.clean
	$(PY) glg.build.benchmarks
	$(PY) glg.build.ba_weather_map
	$(PY) glg.build.weather
	$(PY) glg.build.weather_check
	$(PY) glg.build.features

models:
	$(PY) glg.models.validate
	$(PY) glg.models.growth
	$(PY) glg.models.load_shape
	$(PY) glg.models.supply_gap
	$(PY) glg.models.queue_summary
	$(PY) glg.models.robustness

figures:
	$(PY) glg.viz.figures

explorer:
	$(PY) glg.viz.explorer

social:
	$(PY) glg.viz.social

report:
	rm -rf report/_freeze   # always re-run the report's code against current outputs
	cd report && quarto render
	rm -rf report/_site/explorer && mkdir -p report/_site/explorer
	cp -R app/index.html app/app.js app/style.css app/data report/_site/explorer/

test:
	$(PY) pytest

lint:
	$(PY) ruff check src tests
	$(PY) ruff format --check src tests

all: data features models figures explorer social report test
