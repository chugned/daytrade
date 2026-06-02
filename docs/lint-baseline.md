# Lint baseline — daytrade

Captured on 2026-06-02 as the starting point. Burn these down
as we touch the relevant files. Do NOT do a mass cleanup PR — those
produce noise reviewers can't reason about.

## Ruff counts

```
456	UP006 	non-pep585-annotation
149	UP045 	non-pep604-annotation-optional
112	UP035 	deprecated-import
  6	F841  	unused-variable
  6	SIM105	suppressible-exception
  4	E741  	ambiguous-variable-name
  2	SIM115	open-file-with-context-handler
  1	B007  	unused-loop-control-variable
  1	B017  	assert-raises-exception
  1	SIM102	collapsible-if
  1	SIM103	needless-bool
Found 739 errors.
No fixes available (614 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

## Mypy counts

```
      28
errors total
```

## Tooling

- ruff config: `pyproject.toml::[tool.ruff]`
- black config: `pyproject.toml::[tool.black]`
- mypy config: `pyproject.toml::[tool.mypy]`

## Run locally

```
python3 -m ruff check src/ tests/
python3 -m black --check src/ tests/
python3 -m mypy src/daytrade
```
