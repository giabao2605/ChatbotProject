"""Process adapter for controlled CRAG pilot replay execution."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from typing import Any, Callable, Mapping

from mech_chatbot.config.logging import logger, log_trace


def iter_sse_events(response):
    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        line = (
            raw_line.decode("utf-8")
            if isinstance(raw_line, bytes)
            else str(raw_line or "")
        )
        if not line:
            if data_lines:
                raw_data = "\n".join(data_lines)
                try:
                    payload = json.loads(raw_data)
                except Exception:
                    payload = {"message": raw_data}
                yield event, payload
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        raw_data = "\n".join(data_lines)
        try:
            payload = json.loads(raw_data)
        except Exception:
            payload = {"message": raw_data}
        yield event, payload


def pilot_route(profile: Mapping[str, Any], request_id: str):
    from mech_chatbot.evaluation.crag_pilot import assign_pilot_route, load_pilot_config

    config = load_pilot_config()
    if config is None:
        return None
    return assign_pilot_route(
        config,
        user_id=str(profile.get("user_id") or ""),
        department=str(profile.get("department") or ""),
        request_id=request_id,
        sites=profile.get("allowed_sites") or [],
    )


def pilot_outcome(
    answer: str,
    debug: Mapping[str, Any],
    *,
    provider_error: bool = False,
) -> dict[str, Any]:
    from mech_chatbot.evaluation.outcomes import REFUSAL_OUTCOMES, classify_actual_outcome

    actual = classify_actual_outcome(answer)
    generation = debug.get("generation_metrics") or {}
    return {
        "refusal": actual in REFUSAL_OUTCOMES,
        "access_denied": actual == "access_denied",
        "provider_error": bool(provider_error),
        "correction_count": int(debug.get("correction_count") or 0),
        "repair_count": int(
            debug.get("repair_count") or generation.get("repair_count") or 0
        ),
        "query_type": str(
            debug.get("route") or debug.get("evaluation_group") or "unknown"
        )[:50],
    }


class PilotReplayExecutor:
    """Own bounded background replay resources and replay transport behavior."""

    def __init__(
        self,
        *,
        post: Callable[..., Any],
        headers: Callable[[], Mapping[str, str]],
        workers: Callable[[], int],
        queue_size: Callable[[], int],
        timeout_seconds: Callable[[], int],
    ) -> None:
        self._post = post
        self._headers = headers
        self._workers = workers
        self._queue_size = queue_size
        self._timeout_seconds = timeout_seconds
        self.executor: Any = None
        self.capacity: Any = None

    def start(self) -> None:
        workers = max(1, int(self._workers()))
        queue_size = max(0, int(self._queue_size()))
        self.executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="crag-pilot-replay",
        )
        self.capacity = BoundedSemaphore(workers + queue_size)

    def stop(self) -> None:
        executor = self.executor
        self.executor = None
        self.capacity = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def execute(self, replay) -> None:
        from mech_chatbot.evaluation.crag_pilot import (
            load_pilot_config,
            refresh_replay_auth,
        )

        try:
            current = load_pilot_config()
        except ValueError:
            current = None
        expected_target_id = (
            current.control_deployment_id
            if current is not None and replay.target_arm == "control"
            else current.candidate_deployment_id
            if current is not None
            else None
        )
        if (
            current is None
            or current.experiment_id != replay.metadata["experiment_id"]
            or current.snapshot_fingerprint != replay.metadata["snapshot_fingerprint"]
            or expected_target_id != replay.target_deployment_id
        ):
            log_trace(
                "pilot_replay_result",
                replay.metadata["matched_pair_id"],
                matched_pair_id=replay.metadata["matched_pair_id"],
                experiment_id=replay.metadata["experiment_id"],
                assigned_arm=replay.metadata["assigned_arm"],
                target_arm=replay.target_arm,
                target_deployment_id=replay.target_deployment_id,
                status="dropped",
                fallback_reason="pilot_disabled_or_contract_changed",
            )
            return
        replay = refresh_replay_auth(replay)
        headers = {**self._headers(), **replay.headers}
        started = time.perf_counter()
        replay_status = "error"
        replay_trace_id = None
        error_type = None
        try:
            with self._post(
                f"{replay.target_url}/chat/stream",
                headers=headers,
                json=replay.payload,
                timeout=(
                    10,
                    int(self._timeout_seconds()),
                ),
                stream=True,
            ) as response:
                response.raise_for_status()
                for event, payload in iter_sse_events(response):
                    if event == "done" and isinstance(payload, dict):
                        replay_trace_id = payload.get("trace_id")
                        replay_status = "success"
                    elif event == "error":
                        error_type = "replay_stream_error"
                        break
        except Exception as exc:
            error_type = type(exc).__name__
            logger.warning("CRAG pilot replay failed: %s", exc)
        finally:
            log_trace(
                "pilot_replay_result",
                replay_trace_id or replay.metadata["matched_pair_id"],
                matched_pair_id=replay.metadata["matched_pair_id"],
                experiment_id=replay.metadata["experiment_id"],
                assigned_arm=replay.metadata["assigned_arm"],
                target_arm=replay.target_arm,
                target_deployment_id=replay.target_deployment_id,
                snapshot_fingerprint=replay.metadata["snapshot_fingerprint"],
                status=replay_status,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_type=error_type,
            )

    def schedule(self, route, payload, outcome, trace_id, profile) -> bool:
        from mech_chatbot.evaluation.crag_pilot import (
            build_replay_request,
            should_sample_for_adjudication,
        )

        if route is None or not route.eligible:
            return False
        sampled = should_sample_for_adjudication(
            route.experiment_id,
            route.matched_pair_id,
            outcome,
        )
        log_trace(
            "pilot_assignment",
            trace_id,
            experiment_id=route.experiment_id,
            matched_pair_id=route.matched_pair_id,
            assignment_version=route.assignment_version,
            assigned_arm=route.arm,
            deployment_id=route.deployment_id,
            snapshot_fingerprint=route.snapshot_fingerprint,
            cohort_sha256=route.cohort_sha256,
            actor_hash=route.actor_hash,
            department=str(profile.get("department") or "")[:100],
            roles=sorted(str(role)[:50] for role in (profile.get("roles") or [])),
            sites=sorted(str(site)[:100] for site in (profile.get("allowed_sites") or [])),
            query_type=outcome.get("query_type"),
            refusal=bool(outcome.get("refusal")),
            access_denied=bool(outcome.get("access_denied")),
            provider_error=bool(outcome.get("provider_error")),
            correction_count=int(outcome.get("correction_count") or 0),
            repair_count=int(outcome.get("repair_count") or 0),
            sampled_for_adjudication=sampled,
        )
        if not sampled:
            return False
        replay = build_replay_request(route, payload, original_trace_id=trace_id)
        if (
            self.executor is None
            or self.capacity is None
            or not self.capacity.acquire(blocking=False)
        ):
            log_trace(
                "pilot_replay_result",
                trace_id,
                matched_pair_id=route.matched_pair_id,
                experiment_id=route.experiment_id,
                assigned_arm=route.arm,
                target_arm=replay.target_arm,
                target_deployment_id=replay.target_deployment_id,
                status="dropped",
                fallback_reason="replay_queue_full_or_stopped",
            )
            return False
        try:
            future = self.executor.submit(self.execute, replay)
        except Exception:
            self.capacity.release()
            raise
        future.add_done_callback(lambda _future: self.capacity.release())
        return True


__all__ = [
    "PilotReplayExecutor",
    "iter_sse_events",
    "pilot_outcome",
    "pilot_route",
]
