.PHONY: lock-check sync quality test render smoke cluster-bootstrap

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

cluster-bootstrap:
	@mkdir -p "$(CURDIR)/.cache/ansible/home"
	@if [ "$(APPLY)" = "1" ]; then \
		case "$(CONTEXT)" in \
			dev|dev-*|development|development-*|test|test-*|testing|testing-*|\
			staging|staging-*|stage|stage-*|sandbox|sandbox-*|local|local-*|ci|ci-*) ;; \
			*) \
			printf '%s\n' production_apply_forbidden >&2; exit 2;; \
		esac; \
		ANSIBLE_HOME="$(CURDIR)/.cache/ansible/home" \
		ANSIBLE_COLLECTIONS_PATH="$(CURDIR)/.cache/ansible/collections" \
		ANSIBLE_CONFIG="$(CURDIR)/ansible/ansible.cfg" \
		uv run ansible-playbook -i "$(or $(INVENTORY),ansible/inventory/example.yml)" \
		ansible/playbooks/bootstrap.yml; \
	else \
		ANSIBLE_HOME="$(CURDIR)/.cache/ansible/home" \
		ANSIBLE_COLLECTIONS_PATH="$(CURDIR)/.cache/ansible/collections" \
		ANSIBLE_CONFIG="$(CURDIR)/ansible/ansible.cfg" \
		uv run ansible-playbook -i "$(or $(INVENTORY),ansible/inventory/example.yml)" \
		ansible/playbooks/preflight.yml --check; \
	fi
