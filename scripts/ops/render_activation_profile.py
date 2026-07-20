"""Render one canonical RAG profile into a process environment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mech_chatbot.rag.feature_activation import (
    ACTIVATION_PROFILES,
    VERSION_DEFAULTS,
    profile_environment,
)


def build_profile_environment(
    *, profile: str, scope: str,
    activation_bundle: str | Path | None = None,
    activation_bundle_sha256: str | None = None,
) -> dict[str, str]:
    if profile not in ACTIVATION_PROFILES:
        raise ValueError(f"unknown activation profile: {profile}")
    if scope not in {"evaluation", "controlled_demo", "default_rollout"}:
        raise ValueError(f"unknown activation scope: {scope}")
    environment = {
        **profile_environment(profile),
        **VERSION_DEFAULTS,
        "RAG_ACTIVATION_SCOPE": scope,
        "RAG_EXECUTION_CONTEXT": (
            "evaluation" if scope == "evaluation" else "production"
        ),
    }
    if scope == "evaluation":
        return environment
    if profile == "all_off" and activation_bundle is None:
        return environment
    if activation_bundle is None or not str(activation_bundle_sha256 or "").strip():
        raise ValueError("activation bundle is required for live scopes")
    bundle_path = Path(activation_bundle).resolve()
    raw = bundle_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != str(activation_bundle_sha256).strip().casefold():
        raise ValueError("activation bundle sha256 does not match")
    bundle = json.loads(raw.decode("utf-8"))
    if bundle.get("schema") != "rag-activation-bundle-v1":
        raise ValueError("activation bundle schema is invalid")
    if bundle.get("scope") != scope:
        raise ValueError("activation bundle scope does not match")
    if bundle.get("activation_profile") != profile:
        raise ValueError("activation bundle profile does not match")
    expected_flags = {
        name: value == "true"
        for name, value in profile_environment(profile).items()
    }
    if bundle.get("feature_flags") != expected_flags:
        raise ValueError("activation bundle flags do not match profile")
    versions = bundle.get("versions")
    if not isinstance(versions, dict) or set(versions) != set(VERSION_DEFAULTS):
        raise ValueError("activation bundle versions are incomplete")
    environment.update({name: str(value) for name, value in versions.items()})
    environment.update({
        "RAG_ACTIVATION_BUNDLE_PATH": str(bundle_path),
        "RAG_ACTIVATION_BUNDLE_SHA256": digest,
        "RAG_DEPLOYMENT_GIT_SHA": str(bundle.get("source_commit") or ""),
    })
    graph_fingerprint = str(bundle.get("graph_fingerprint") or "").strip()
    if graph_fingerprint:
        environment["RAG_GRAPH_FINGERPRINT"] = graph_fingerprint
    return environment


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=tuple(ACTIVATION_PROFILES), required=True)
    parser.add_argument(
        "--scope", choices=("evaluation", "controlled_demo", "default_rollout"),
        required=True,
    )
    parser.add_argument("--activation-bundle", type=Path)
    parser.add_argument("--activation-bundle-sha256")
    args = parser.parse_args(argv)
    print(json.dumps(build_profile_environment(
        profile=args.profile,
        scope=args.scope,
        activation_bundle=args.activation_bundle,
        activation_bundle_sha256=args.activation_bundle_sha256,
    ), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
