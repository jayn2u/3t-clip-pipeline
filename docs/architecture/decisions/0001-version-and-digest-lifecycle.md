# ADR 0001: Version and digest lifecycle

Status: Accepted

All deployed images use immutable digest references. Chart versions, source revisions, archive
SHA-256 values, schema sources, and rendered image digests live in the repository lock files.

An update is one reviewed change: select a released upstream version, download into the task-local
cache, verify it against an independently published checksum or reviewed source, update the lock,
render from the verified archive, run schema and ownership checks, inspect the manifest diff, and
run `make quality test render smoke`. Runtime publication produces a proposed digest artifact; a
reviewer promotes that digest in a separate commit. CI never silently rewrites a lock.

Rollback selects a previously reviewed complete lock set. Mixing chart, CRD, schema, or image
versions from different sets is unsupported.
