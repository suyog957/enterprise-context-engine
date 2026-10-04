"""Reproducibility metadata recorded with every evaluation run."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from enterprise_context.config import Settings

PROMPT_VERSION = "2026-10-04.1"  # NL-to-SPARQL and intent-fallback prompt templates


def _hash_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def git_revision(root: Path) -> str:
    """Read HEAD without a git binary (the tools image does not ship one)."""
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    head = root / ".git" / "HEAD"
    if not head.exists():
        return "unknown"
    reference = head.read_text(encoding="utf-8").strip()
    if not reference.startswith("ref: "):
        return reference[:12]
    ref_name = reference[5:]
    ref_path = root / ".git" / ref_name
    if ref_path.exists():
        return ref_path.read_text(encoding="utf-8").strip()[:12]
    packed = root / ".git" / "packed-refs"
    if packed.exists():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(ref_name):
                return line.split()[0][:12]
    return "unknown"


def run_metadata(
    root: Path, settings: Settings, *, suite: str, dataset_seed: int
) -> dict[str, Any]:
    policy_files = sorted((root / "policies").glob("*.rego"))
    policy_version = "unknown"
    for line in (root / "policies" / "procurement.rego").read_text(encoding="utf-8").splitlines():
        if line.startswith("policy_version :="):
            policy_version = line.split('"')[1]
    return {
        "suite": suite,
        "run_at": datetime.now(UTC).isoformat(),
        "dataset_seed": dataset_seed,
        "code_revision": git_revision(root),
        "ontology_hash": _hash_files(sorted((root / "ontology").glob("*.ttl"))),
        "policy_version": policy_version,
        "policy_bundle_hash": _hash_files(policy_files),
        "prompt_version": PROMPT_VERSION,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model or "mock-deterministic-v1",
        "embedding_provider": settings.embedding_provider,
        "embedding_model": (
            settings.embedding_model
            if settings.embedding_provider != "feature_hash"
            else f"feature-hash-{settings.embedding_dimensions}d"
        ),
    }
