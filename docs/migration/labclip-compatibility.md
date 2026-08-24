# LabCLIP compatibility boundary

The platform consumes the versioned workload contract and S3-compatible object boundaries. It does
not import the LabCLIP training package, assume its host paths, or take ownership of its cluster.
Consumer tests are classified in the adjacent migration map; classification is evidence, not an
authorization to migrate a consumer.

Before a consumer moves, prove offline contract validation, digest-pinned images, portable input
and output keys, completion-marker semantics, resource/GPU fit, and isolated execution. Record any
temporary adapter with an owner and removal condition. LabCLIP-specific datasets, model behavior,
training correctness, and historical output equivalence remain consumer responsibilities.

Live compatibility must be assessed while resources remain `legacy-owned`. A compatible result can
advance a separately approved handoff to `frozen` and then `ready`; it cannot claim `new-owned`.
