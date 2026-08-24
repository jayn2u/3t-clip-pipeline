.PHONY: lock-check sync ansible-sync quality test render smoke cluster-bootstrap release-handoff

ANSIBLE_REQUIREMENTS_SHA256 = 70d34763e23d33b90d322bcb89f4ebfef1889b7bef5411725b622631b7360bc6
ANSIBLE_COLLECTIONS_ROOT = $(CURDIR)/.cache/ansible/collections
ANSIBLE_SYNC_MARKER = $(ANSIBLE_COLLECTIONS_ROOT)/.requirements.sha256
ANSIBLE_SYNC_MODE ?= online

lock-check:
	uv lock --check

sync:
	uv sync --locked

ansible-sync: lock-check
	@set -eu; \
	actual=$$(sha256sum ansible/requirements.yml | awk '{print $$1}'); \
	if [ "$$actual" != "$(ANSIBLE_REQUIREMENTS_SHA256)" ]; then \
		printf '%s\n' ansible_requirements_pin_mismatch >&2; exit 1; \
	fi; \
	marker=''; \
	if [ -f "$(ANSIBLE_SYNC_MARKER)" ]; then marker=$$(cat "$(ANSIBLE_SYNC_MARKER)"); fi; \
	if [ "$$marker" = "$$actual" ] && \
	   [ -d "$(ANSIBLE_COLLECTIONS_ROOT)/ansible_collections/k3s/orchestration" ] && \
	   [ -d "$(ANSIBLE_COLLECTIONS_ROOT)/ansible_collections/kubernetes/core" ] && \
	   [ -d "$(ANSIBLE_COLLECTIONS_ROOT)/ansible_collections/ansible/posix" ] && \
	   [ -d "$(ANSIBLE_COLLECTIONS_ROOT)/ansible_collections/community/general" ]; then \
		exit 0; \
	fi; \
	if [ "$(ANSIBLE_SYNC_MODE)" = offline ]; then \
		printf '%s\n' 'ANSIBLE_CACHE_REQUIRED: run make ansible-sync before offline release handoff' >&2; exit 78; \
	fi; \
	mkdir -p "$(CURDIR)/.cache/ansible/home" "$(ANSIBLE_COLLECTIONS_ROOT)"; \
	ANSIBLE_HOME="$(CURDIR)/.cache/ansible/home" \
	ANSIBLE_COLLECTIONS_PATH="$(ANSIBLE_COLLECTIONS_ROOT)" \
	uv run ansible-galaxy collection install -r ansible/requirements.yml \
		-p "$(ANSIBLE_COLLECTIONS_ROOT)" --force; \
	printf '%s\n' "$$actual" > "$(ANSIBLE_SYNC_MARKER).tmp"; \
	mv "$(ANSIBLE_SYNC_MARKER).tmp" "$(ANSIBLE_SYNC_MARKER)"

quality: lock-check
	uv run ruff format --check .
	uv run ruff check .
	uv run basedpyright

test: ansible-sync
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

DELIVERY_MODE ?= local
AUTHORIZATION_FILE ?=
export DELIVERY_MODE AUTHORIZATION_FILE
REMOTE_AUTHORITY_MESSAGE = REMOTE_AUTHORITY_REQUIRED: an external owner must initialize origin/develop and remote HEAD; local task worktree and commits are complete and preserved

release-handoff:
	@set -eu; \
	case "$${DELIVERY_MODE:-local}" in \
		local) \
			$(MAKE) ansible-sync ANSIBLE_SYNC_MODE=offline; \
			$(MAKE) quality test render smoke ANSIBLE_SYNC_MODE=offline; \
			printf '%s\n' LOCAL_COMPLETE_REMOTE_DEFERRED; \
			;; \
		authorized-pr) \
			authorization_file="$${AUTHORIZATION_FILE:-}"; \
			if [ -z "$$authorization_file" ] || [ ! -f "$$authorization_file" ]; then \
				printf '%s\n' 'AUTHORIZATION_REQUIRED: supply an externally created mode-0600 remote-delivery-v1 JSON artifact' >&2; exit 78; \
			fi; \
			if [ "$$(stat -c '%a' "$$authorization_file")" != 600 ]; then \
				printf '%s\n' 'AUTHORIZATION_INVALID: authorization artifact permissions must be 0600' >&2; exit 78; \
			fi; \
			python3 -c 'import json,sys; expected={"schema":"three-t-pipeline/remote-delivery-v1","repository":"jayn2u/3t-clip-pipeline","branch":"codex/next-generation-pipeline-platform","base":"develop","allowPush":True,"allowPullRequest":True}; hook=lambda pairs: dict(pairs) if len(pairs)==len(dict(pairs)) else None; actual=json.load(open(sys.argv[1], encoding="utf-8"), object_pairs_hook=hook); raise SystemExit(0 if actual == expected else 78)' "$$authorization_file" 2>/dev/null || { \
				printf '%s\n' 'AUTHORIZATION_INVALID: artifact must contain exactly the remote-delivery-v1 authorization object' >&2; exit 78; \
			}; \
			if [ -n "$$(git status --porcelain)" ]; then \
				printf '%s\n' 'LOCAL_STATE_NOT_READY: commit or remove task-worktree changes before authorized delivery' >&2; exit 78; \
			fi; \
			if [ "$$(git branch --show-current)" != codex/next-generation-pipeline-platform ]; then \
				printf '%s\n' 'LOCAL_STATE_NOT_READY: authorized delivery requires codex/next-generation-pipeline-platform' >&2; exit 78; \
			fi; \
			if ! remote_head=$$(git ls-remote --exit-code --symref origin HEAD 2>/dev/null) || \
			   ! printf '%s\n' "$$remote_head" | grep -q '^ref: refs/heads/develop[[:space:]]HEAD$$' || \
			   ! git ls-remote --exit-code origin refs/heads/develop >/dev/null 2>&1; then \
				printf '%s\n' '$(REMOTE_AUTHORITY_MESSAGE)' >&2; exit 78; \
			fi; \
			git fetch origin +refs/heads/develop:refs/remotes/origin/develop; \
			pre_rebase_sha=$$(git rev-parse HEAD); \
			if ! git rebase --onto refs/remotes/origin/develop --root; then \
				git rebase --abort >/dev/null 2>&1 || true; \
				restored_sha=$$(git rev-parse HEAD); \
				if [ "$$restored_sha" != "$$pre_rebase_sha" ]; then \
					printf '%s\n' 'REMOTE_BASE_CONFLICT_RESTORE_FAILED' >&2; exit 1; \
				fi; \
				printf '%s\n' REMOTE_BASE_CONFLICT >&2; exit 78; \
			fi; \
			rewritten_sha=$$(git rev-parse HEAD); \
			$(MAKE) quality test render smoke; \
			if [ "$$(git rev-parse HEAD)" != "$$rewritten_sha" ]; then \
				printf '%s\n' 'REWRITTEN_SHA_CHANGED_DURING_GATES' >&2; exit 1; \
			fi; \
			git push origin HEAD:refs/heads/codex/next-generation-pipeline-platform; \
			gh pr create --repo jayn2u/3t-clip-pipeline --base develop --head codex/next-generation-pipeline-platform --title '운영자 마이그레이션 및 릴리스 인계' --body '변경: 독립형 파이프라인 플랫폼과 운영자 인계를 제공합니다.\n\n이유: 재현 가능한 검증 및 명시적 권한 경계가 필요합니다.\n\n영향: 로컬 검증과 승인된 PR 전달만 포함합니다.\n\n검증: make quality test render smoke\n\n제외: 실제 LabCLIP 전환, 원격 초기화, 데이터 복원 및 자격 증명 교체'; \
			printf '%s\n' PR_READY; \
			;; \
		*) \
			printf '%s\n' 'DELIVERY_MODE_INVALID: use local or authorized-pr' >&2; exit 64; \
			;; \
	esac
