# Reproduce every RCAEval number in the README with one command:
#
#   make eval          # ~3.3 GB download the first time, then signals, Faultline, RCAEval's baselines, the comparison
#   make eval-quick    # data, signals and baselines already there: re-run Faultline and the comparison (~10 min)
#
# Everything runs in Docker. Results: data/rcaeval/*.json and docs/rcaeval-results.md.

APP     = docker compose run --rm app
SUITES  = --suites re2,re3 --systems ob,ss,tt
BASE    = docker run --rm -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 -v "$(PWD)/data:/data" -v "$(PWD)/baselines:/b:ro" faultline-baselines

.PHONY: eval eval-quick data signals faultline baselines compare up test synthetic scale

eval: up data signals faultline baselines compare

eval-quick: up faultline compare

up:
	docker compose up -d --build

data:
	$(APP) python -m faultline.rcaeval $(SUITES) --download

signals:
	$(APP) python -W ignore -m faultline.rcaeval $(SUITES) --extract
	$(APP) python -W ignore -m faultline.rcaeval --suites re2,re3 --systems ob,tt --profile

faultline:
	$(APP) python -W ignore -m faultline.rcaeval $(SUITES) --signals metrics --json data/rcaeval/faultline_metrics.json
	$(APP) python -W ignore -m faultline.rcaeval $(SUITES) --signals all --json data/rcaeval/faultline_all.json

baselines:
	docker build -q -t faultline-baselines baselines
	$(BASE) python /b/run_baselines.py --methods baro,nsigma
	$(BASE) python /b/run_baselines.py --methods circa,pc_randomwalk --systems ob,ss

compare:
	@{ echo "# RCAEval: Faultline against RCAEval's baselines"; echo; \
	  echo 'Output of `make eval` (`python -m faultline.compare`): AC@1 / Avg@5. See the README, "Public data", for how each number was produced.'; echo; \
	  $(APP) python -m faultline.compare 2>/dev/null; } > docs/rcaeval-results.md
	@cat docs/rcaeval-results.md

test:
	$(APP) pytest -q

synthetic:
	$(APP) python -m faultline.eval --n 400 --seed 3

scale:
	$(APP) python -W ignore -m faultline.scale --services 3000
