from __future__ import annotations

import textwrap

BASELINE_POLICY = textwrap.dedent(
    """
    # AGENT Policy

    ## Core
    - Treat raw session data as immutable evidence.
    - Never promote policy changes without evaluation.
    - Keep safety constraints ahead of style or speed.

    ## Memory
    - Save sessions as structured records first.
    - Generate markdown as a derivative artifact, not canonical source.
    - Support rollback for every promoted policy change.
    """
).strip()

SUPPORTED_JOB_TYPES = {"memory.distill", "policy.evaluate"}
