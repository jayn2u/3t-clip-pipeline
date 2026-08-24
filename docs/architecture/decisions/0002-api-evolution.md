# ADR 0002: Workload API evolution

Status: Accepted

`platform.three-t.dev/v1alpha1` is the initial workload contract. The schema is strict: unknown
fields fail, defaults are explicit, and canonical serialization is deterministic. Compatible
additions require schema, model, CLI, golden, rejection, and consumer-fixture coverage in one
change. A semantic or destructive change requires a new API version and an explicit converter;
the renderer never guesses intent.

Deprecation is announced in the changelog with replacement, owner, first deprecated release, and
removal milestone. At least one supported release reads both versions before removal. Stored run
evidence retains the submitted API version and renderer commit. Migration tooling does not rewrite
source workloads in place.
