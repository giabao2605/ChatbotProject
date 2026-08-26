"""Render one canonical RAG profile into a process environment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mech_chatbot.rag.feature_activation import (
    ACTIVATION_PROFILE_NAMES,
    FEATURE_FLAGS,
    SELECTIVE_PROFILE,
    VERSION_DEFAULTS,
    profile_environment,
)


def build_profile_environment(
    *, profile: str, scope: str,
    activation_bundle: str | Path | None = None,
    activation_bundle_sha256: str | None = None,
    runtime_consumption_authorization: str | Path | None = None,
    runtime_consumption_authorization_sha256: str | None = None,
    enabled_features: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    if profile not in ACTIVATION_PROFILE_NAMES:
        raise ValueError(f"unknown activation profile: {profile}")
    if scope not in {"evaluation", "controlled_demo", "default_rollout"}:
        raise ValueError(f"unknown activation scope: {scope}")
    runtime_path_given = runtime_consumption_authorization is not None
    runtime_sha_given = bool(
        str(runtime_consumption_authorization_sha256 or "").strip()
    )
    if runtime_path_given != runtime_sha_given:
        raise ValueError(
            "runtime consumption authorization path and sha256 must be provided together"
        )
    if runtime_path_given and scope != "controlled_demo":
        raise ValueError("runtime consumption authorization is controlled_demo only")
    if scope == "evaluation":
        return {
            **profile_environment(profile, enabled_features),
            **VERSION_DEFAULTS,
            "RAG_ACTIVATION_PROFILE": profile,
            "RAG_ACTIVATION_SCOPE": scope,
            "RAG_EXECUTION_CONTEXT": "evaluation",
        }
    if profile == "all_off":
        if (
            activation_bundle is not None
            or str(activation_bundle_sha256 or "").strip()
            or runtime_path_given
        ):
            raise ValueError("all_off does not use an activation bundle")
        return {
            **profile_environment(profile),
            **VERSION_DEFAULTS,
            "RAG_ACTIVATION_PROFILE": profile,
            "RAG_ACTIVATION_SCOPE": scope,
            "RAG_EXECUTION_CONTEXT": "production",
        }
    if enabled_features:
        raise ValueError("live selective flags must come from the activation bundle")
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
    bundle_flags = bundle.get("feature_flags")
    if not (
        isinstance(bundle_flags, dict)
        and set(bundle_flags) == set(profile_environment("all_off"))
        and all(isinstance(value, bool) for value in bundle_flags.values())
    ):
        raise ValueError("activation bundle flags are invalid")
    enabled = {name for name, value in bundle_flags.items() if value}
    expected_flags = {
        name: value == "true"
        for name, value in profile_environment(
            profile, enabled if profile == SELECTIVE_PROFILE else None
        ).items()
    }
    if bundle.get("feature_flags") != expected_flags:
        raise ValueError("activation bundle flags do not match profile")
    versions = bundle.get("versions")
    if not isinstance(versions, dict) or set(versions) != set(VERSION_DEFAULTS):
        raise ValueError("activation bundle versions are incomplete")
    environment = {
        **{name: str(value).lower() for name, value in bundle_flags.items()},
        **{name: str(value) for name, value in versions.items()},
        "RAG_ACTIVATION_PROFILE": profile,
        "RAG_ACTIVATION_SCOPE": scope,
        "RAG_EXECUTION_CONTEXT": "production",
    }
    bindings = {
        "RAG_ACTIVATION_BUNDLE_PATH": str(bundle_path),
        "RAG_ACTIVATION_BUNDLE_SHA256": digest,
        "RAG_DEPLOYMENT_GIT_SHA": str(bundle.get("source_commit") or ""),
    }
    graph_fingerprint = str(bundle.get("graph_fingerprint") or "").strip()
    if graph_fingerprint:
        bindings = {**bindings, "RAG_GRAPH_FINGERPRINT": graph_fingerprint}
    if runtime_path_given:
        runtime_path = Path(runtime_consumption_authorization).resolve()
        runtime_digest = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
        if runtime_digest != str(
            runtime_consumption_authorization_sha256
        ).strip().casefold():
            raise ValueError(
                "runtime consumption authorization sha256 does not match"
            )
        bindings = {**bindings,
            "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH": str(runtime_path),
            "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256": runtime_digest,
        }
    return {**environment, **bindings}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=ACTIVATION_PROFILE_NAMES, required=True)
    parser.add_argument(
        "--enable-feature", action="append", default=[], choices=FEATURE_FLAGS,
    )
    parser.add_argument(
        "--scope", choices=("evaluation", "controlled_demo", "default_rollout"),
        required=True,
    )
    parser.add_argument("--activation-bundle", type=Path)
    parser.add_argument("--activation-bundle-sha256")
    parser.add_argument("--runtime-consumption-authorization", type=Path)
    parser.add_argument("--runtime-consumption-authorization-sha256")
    args = parser.parse_args(argv)
    print(json.dumps(build_profile_environment(
        profile=args.profile,
        scope=args.scope,
        activation_bundle=args.activation_bundle,
        activation_bundle_sha256=args.activation_bundle_sha256,
        runtime_consumption_authorization=(
            args.runtime_consumption_authorization
        ),
        runtime_consumption_authorization_sha256=(
            args.runtime_consumption_authorization_sha256
        ),
        enabled_features=set(args.enable_feature),
    ), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
