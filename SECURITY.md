# Security policy

Report vulnerabilities privately to the repository owner. Do not include credentials, kubeconfig
contents, signed URLs, Secret values, private endpoints, or production object keys in an issue.

Supported development is the current `develop` line. Security fixes pin immutable dependencies and
images, preserve the workload API boundary, and include regression evidence. The platform rejects
secret-bearing CLI flags, production submission, mutable images, overlapping field ownership, and
remote delivery without an exact external authorization artifact.

Operators must use explicit kubeconfig contexts and environment classes, store Vault inputs and
delivery authorization with restricted permissions, and retain only redacted evidence. This
repository does not authorize credential rotation, production cutover, destructive recovery, or
remote initialization.
