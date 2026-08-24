# Contributing

Create a dedicated branch and keep changes scoped and reviewable. Use Python 3.12 and the checked-in
`uv.lock`:

```bash
make sync
make quality test render smoke
```

Contract or API changes require schema, typed model, CLI, golden, rejection, and consumer-fixture
coverage plus a changelog entry. Deployment updates require immutable versions/digests, lock-file
provenance, rendered ownership checks, and an inspected diff. Never commit credentials, plaintext
Vault input, kubeconfigs, generated evidence containing private data, or authorization artifacts.

Local completion uses `make release-handoff DELIVERY_MODE=local`. Push and PR delivery require the
separate external authorization described in the operator release handoff. A contribution is not
authority to mutate LabCLIP or migrate a consumer.
