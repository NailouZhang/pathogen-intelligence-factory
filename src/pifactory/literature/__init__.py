"""v15 literature lifecycle architecture.

The package separates retrieval identity, metadata verification, content completion,
primary-report selection, supplementary retention, and audit generation.  Public
helpers are intentionally small so the top-level pipeline remains an orchestrator.
"""

from .profile import (
    CORE_SEARCH_TERM_COUNT,
    build_post_retrieval_vocabulary,
    validate_frozen_core_terms,
)
from .normalization import (
    canonical_identifiers,
    metadata_verification,
    normalize_literature_record,
    verified_evidence_status,
)
from .identity import (
    IDENTITY_POLICY_VERSION,
    assess_completion_identity,
    merge_verified_candidate,
    register_identity_assessment,
)
from .enrichment import (
    complete_literature_catalog,
    classify_scholarly_payload,
)
from .selection import (
    build_supplementary_view,
    select_primary_and_supplementary,
)

__all__ = [
    "CORE_SEARCH_TERM_COUNT",
    "build_post_retrieval_vocabulary",
    "validate_frozen_core_terms",
    "canonical_identifiers",
    "metadata_verification",
    "normalize_literature_record",
    "verified_evidence_status",
    "IDENTITY_POLICY_VERSION",
    "assess_completion_identity",
    "merge_verified_candidate",
    "register_identity_assessment",
    "complete_literature_catalog",
    "classify_scholarly_payload",
    "build_supplementary_view",
    "select_primary_and_supplementary",
]
