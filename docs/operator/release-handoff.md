# Release and pull-request handoff

The delivery interface has two modes. On a clean workstation, explicitly populate the pinned,
task-local Ansible cache first; this setup step may download only the sources in
`ansible/requirements.yml` and is not part of offline release delivery:

```bash
make ansible-sync
```

After that explicit setup, this plan uses only local mode:

```bash
make release-handoff DELIVERY_MODE=local
```

It verifies the task-local collection marker before running `make quality test render smoke`,
performs no network call or remote write, and ends with exactly `LOCAL_COMPLETE_REMOTE_DEFERRED`.
If the cache is absent or stale it fails closed with `ANSIBLE_CACHE_REQUIRED` and never installs
implicitly; run the explicit setup while network access is separately permitted, then retry.

Authorized PR delivery is **REQUIRES_SEPARATE_AUTHORIZATION** and is documented for a later external
owner. This repository never creates the authority artifact:

```bash
make release-handoff DELIVERY_MODE=authorized-pr AUTHORIZATION_FILE=.omo/authorizations/remote-delivery.json
```

The external file must be regular mode `0600` and schema-valid JSON containing exactly:

```json
{"schema":"three-t-pipeline/remote-delivery-v1","repository":"jayn2u/3t-clip-pipeline","branch":"codex/next-generation-pipeline-platform","base":"develop","allowPush":true,"allowPullRequest":true}
```

Missing, malformed, over-permissive, or incorrectly permissioned authority exits 78 before a
network call. Dirty local state also exits 78. The authorized path then checks remote HEAD and
`refs/heads/develop`. Missing remote state reports `REMOTE_AUTHORITY_REQUIRED`, exits 78 at the gate,
and writes nothing. When present, it fetches only
`+refs/heads/develop:refs/remotes/origin/develop`, records the task SHA, rebases orphan-root history
onto that ref, reruns all gates on the rewritten SHA, pushes only
`codex/next-generation-pipeline-platform`, and opens a ready-for-review PR against `develop`.

On conflict the interface aborts the rebase, verifies restoration of the recorded SHA, reports
`REMOTE_BASE_CONFLICT`, and never pushes. `PR_READY` is printed only after push and PR creation both
succeed. This procedure does not initialize remote HEAD or `develop`, fabricate authority, choose a
merge policy, merge the PR, or authorize a live cutover.
