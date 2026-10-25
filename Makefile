# One command for everything: `make all`  (needs only Python 3.9+; `zip` is optional, see package target)
PY ?= python3

.PHONY: all run validate test eval stress package clean

all: test package                     ## tests, then regenerate output.csv, reports, audit trail and code.zip

run:                                  ## python3 code/main.py  -> output.csv
	$(PY) code/main.py

validate:                             ## structural validation of output.csv
	$(PY) code/evaluation/validate_output.py

test:                                 ## regression tests (samples, linking, parsers, contract)
	$(PY) -m unittest discover -s code/tests

eval:                                 ## accuracy vs samples + reports + audit trail + ceiling analysis
	$(PY) code/evaluation/main.py --reports
	$(PY) code/evaluation/ceiling_analysis.py

stress:                               ## 29 adversarial input scenarios (code/tools/stress.py, ~40 s)
	$(PY) code/tools/stress.py

package:                              ## code.zip as the starter README prescribes (also reruns run, validate, reports)
	./package.sh

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f code.zip
