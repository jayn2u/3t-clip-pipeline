.PHONY: lock-check sync quality test render smoke

lock-check:
	uv lock --check

sync:
	uv sync --locked

quality: lock-check
	uv run ruff format --check .
	uv run ruff check .
	uv run basedpyright

test:
	uv run pytest -q

render:
	uv run 3t-pipeline --help >/dev/null

smoke: lock-check
	uv run 3t-pipeline version --short
