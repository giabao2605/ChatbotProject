"""Occurrence-aware Graph v2; the legacy two-hop contract remains unchanged."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from time import monotonic

from mech_chatbot.domain.graph_policy import RELATION_ONTOLOGY


CONTRACT_VERSION = "graph-v2"
RELATION_ONTOLOGY_V2 = RELATION_ONTOLOGY | {"PRECEDES_OPERATION", "HAS_DRAWING"}
MAX_LEVELS = 8
MAX_RELATIONS = 500
MAX_GENERATION_EDGES = 50


def _valid_source(source):
    try:
        return (
            int(source["doc_id"]) > 0 and int(source["version"]) > 0
            and int(source["page"]) > 0
            and bool(str(source["source_quote"]).strip())
            and len(str(source["source_hash"])) == 64
            and all(char in "0123456789abcdef" for char in str(source["source_hash"]).lower())
        )
    except (KeyError, TypeError, ValueError):
        return False


def _valid_occurrence(row):
    try:
        quantity = Decimal(str(row["quantity"]))
        return (
            row.get("relation_type") == "CONTAINS_PART"
            and bool(row["occurrence_id"]) and bool(row["part_key"])
            and bool(row["unit"])
            and row.get("quantity_basis") == "per_parent"
            and quantity.is_finite() and quantity >= 0
            and bool(row.get("sources"))
            and all(_valid_source(source) for source in row["sources"])
            and not (row.get("is_leaf") and row.get("child_doc_id"))
        )
    except (KeyError, TypeError, InvalidOperation, ValueError):
        return False


def traverse_graph_v2(root_doc_id, reader, *, product_scope, deadline=None, max_relations=MAX_RELATIONS, max_generation_edges=MAX_GENERATION_EDGES):
    """Traverse a governed reader, retaining every physical occurrence path.

    ``reader.assembly(doc_id, remaining)`` must return ACL-filtered current rows
    and reviewed declarations. SQL supplies that trust boundary; this function
    never accepts a client-provided manifest as proof of completeness.
    """
    if isinstance(root_doc_id, bool) or int(root_doc_id) <= 0 or not str(product_scope).strip():
        raise ValueError("positive root document and product scope required")
    validate_graph_limits(max_relations,max_generation_edges)
    stack = [(int(root_doc_id), (), (), (), (), 1, None)]
    reasons, edges, occurrences, revisions = set(), [], [], set()
    pins = {}
    assembly_sources = {}
    examined = 0
    cache = {}
    while stack:
        if deadline is not None and monotonic() >= deadline:
            reasons.add("deadline")
            break
        doc_id, ids, quantities, path_sources, ancestors, level, expected_version = stack.pop()
        if doc_id in ancestors:
            reasons.add("cycle")
            continue
        if level > MAX_LEVELS:
            reasons.add("depth_limit")
            continue
        if examined >= max_relations:
            reasons.add("relation_limit")
            break
        try:
            if doc_id not in cache:
                cache[doc_id] = reader.assembly(doc_id, max_relations - examined)
            batch = cache[doc_id]
        except TimeoutError:
            reasons.add("deadline")
            break
        if deadline is not None and monotonic() >= deadline:
            reasons.add("deadline")
            break
        if not batch.get("available") or not _valid_source(batch.get("source", {})):
            reasons.add("coverage_unverified")
            continue
        document_source = {**batch["source"], "snapshot_id": batch.get("snapshot_id")}
        if expected_version is not None and int(document_source["version"]) != expected_version:
            reasons.add("revision_mismatch")
            continue
        revisions.add((int(document_source["doc_id"]), int(document_source["version"])))
        if not batch.get("snapshot_id"):
            reasons.add("coverage_unverified")
        key = (document_source['doc_id'], document_source['version'])
        pin = (document_source['snapshot_id'], document_source['source_hash'])
        if key in pins and pins[key] != pin:
            reasons.add("snapshot_mismatch")
            continue
        pins[key] = pin
        assembly_sources[doc_id]=document_source
        rows = batch.get("rows", ())
        if (not batch.get("rows_complete") or not batch.get("tree_complete")
                or batch.get("expected_rows") != len(rows)):
            reasons.add("coverage_unverified")
        seen_rows = set()
        for row in rows:
            if deadline is not None and monotonic() >= deadline:
                reasons.add("deadline")
                break
            if examined >= max_relations:
                reasons.add("relation_limit")
                break
            examined += 1
            if not _valid_occurrence(row) or row["occurrence_id"] in seen_rows:
                reasons.add("invalid_occurrence")
                continue
            seen_rows.add(row["occurrence_id"])
            row_ids = (*ids, str(row["occurrence_id"]))
            row_quantities = (*quantities, str(row["quantity"]))
            row_sources = tuple({**source, "snapshot_id": source.get('snapshot_id') or batch.get('snapshot_id')}
                                for source in row["sources"])
            for source in row_sources:
                source_key = (source['doc_id'], source['version'])
                source_pin = (source['snapshot_id'], source['source_hash'])
                if source_key in pins and pins[source_key] != source_pin:
                    reasons.add('snapshot_mismatch')
                pins[source_key] = source_pin
                revisions.add(source_key)
            revisions.update((int(source["doc_id"]), int(source["version"])) for source in row_sources)
            sources = (*path_sources, document_source, *row_sources)
            occurrence_id = "/".join(row_ids)
            if len(edges) < max_generation_edges:
                edges.append({"occurrence_id": occurrence_id,
                              "source_key": f"document:{doc_id}",
                              "target_key": row["part_key"],
                              "quantity": str(row["quantity"]), "unit": row["unit"],
                              "quantity_basis": "per_parent", "quantity_path": row_quantities,
                              "relation_type": "CONTAINS_PART", "sources": sources})
            if row.get("is_leaf"):
                occurrences.append({
                    "occurrence_id": occurrence_id, "part_key": row["part_key"],
                    "unit": row["unit"], "currency": str(row.get("currency") or ""),
                    "quantity_path": row_quantities, "quantity_basis": "per_parent",
                    "sources": sources, "approved": True, "current": True,
                    "authorized": True,
                })
            elif not row.get("child_doc_id"):
                reasons.add("binding_unresolved")
            else:
                stack.append((int(row["child_doc_id"]), row_ids, row_quantities,
                              sources, (*ancestors, doc_id), level + 1,
                              row.get("child_version")))
        if examined >= max_relations:
            reasons.add("relation_limit")
    status = "complete" if not reasons else ("partial" if edges or revisions else "unknown")
    source_snapshots = tuple((doc, version, pin[0], pin[1]) for (doc, version), pin in sorted(pins.items()))
    snapshot = hashlib.sha256(json.dumps(source_snapshots).encode('utf-8')).hexdigest() if pins else None
    def frozen_source(source):
        return {**source, 'source_snapshot_id': source['snapshot_id'], 'snapshot_id': snapshot}
    edges = [{**edge, 'sources': tuple(frozen_source(s) for s in edge['sources'])} for edge in edges]
    occurrences = [{**item, 'sources': tuple(frozen_source(s) for s in item['sources'])} for item in occurrences]
    manifest = {
        "contract_version": CONTRACT_VERSION, "status": status,
        "reasons": tuple(sorted(reasons)), "product_scope": str(product_scope),
        "snapshot_id": snapshot, "source_revisions": tuple(sorted(revisions)),
        "source_snapshots": source_snapshots,
        'assembly_sources':tuple(assembly_sources.values()),
        "expected_occurrence_ids": tuple(item["occurrence_id"] for item in occurrences),
        "occurrences": tuple(occurrences), "completeness_reviewed": status == "complete",
        "authorized": status == "complete", "current": status == "complete",
    }
    return {"contract_version": CONTRACT_VERSION, "status": status,
            "reasons": manifest["reasons"], "edges": tuple(edges), "manifest": manifest,
            "examined_relations":examined}


def assembly_sequence_result(sequence, *, deadline=None):
    """A reviewed order is separate from physical BOM occurrences and v1 edges."""
    if deadline is not None and monotonic() >= deadline:
        return {'contract_version': CONTRACT_VERSION, 'status': 'unknown', 'reasons': ('deadline',), 'edges': (), 'manifest': None}
    manifest = sequence.get('manifest')
    steps = manifest.get('steps', ()) if manifest else ()
    if sequence.get('status') != 'complete' or not steps:
        return {'contract_version': CONTRACT_VERSION, 'status': 'unknown', 'reasons': ('coverage_unverified',), 'edges': (), 'manifest': None}
    if len(steps) > MAX_RELATIONS:
        return {'contract_version': CONTRACT_VERSION, 'status': 'partial', 'reasons': ('relation_limit',), 'edges': (), 'manifest': None}
    def source(step):
        return {'doc_id': int(step['locator']['doc_id']), 'version': int(step['locator']['version']),
                'page': step['locator']['page'], 'source_quote': step['raw_value'][:2000],
                'source_hash': step['source_hash'], 'snapshot_id': manifest['snapshot_id'],
                'bbox': step['locator'].get('bbox'), 'row_key': step['locator'].get('row_key'),
                'note_id' if step.get('kind') == 'drawing_note' else 'step_id': step['fact_id']}
    edges = tuple({'relation_type': 'PRECEDES_OPERATION', 'source_step': first['sequence_number'],
                   'target_step': second['sequence_number'],
                   'source_key': first['fact_id'], 'target_key': second['fact_id'],
                   'sources': tuple(source(item) for step in (first, second) for item in (step, *step.get('notes', ())))}
                  for first, second in zip(steps, steps[1:]))
    return {'contract_version': CONTRACT_VERSION, 'status': 'complete', 'reasons': (),
            'edges': edges[:MAX_GENERATION_EDGES], 'manifest': manifest,
            'context_truncated': len(edges) > MAX_GENERATION_EDGES, 'examined_relations': len(edges)}


def traverse_where_used_v2(root_key, reader, *, deadline=None, max_relations=MAX_RELATIONS, max_generation_edges=MAX_GENERATION_EDGES):
    """Follow reviewed incoming bindings without claiming corpus completeness."""
    validate_graph_limits(max_relations,max_generation_edges)
    stack = [(str(root_key), (), 1)]
    reasons, edges, visited, edge_ids = {"coverage_unverified"}, [], set(), set()
    examined = 0
    while stack:
        if deadline is not None and monotonic() >= deadline:
            reasons.add("deadline")
            break
        key, ancestors, level = stack.pop()
        if key in ancestors:
            reasons.add("cycle")
            continue
        if level > MAX_LEVELS:
            reasons.add("depth_limit")
            continue
        if key in visited:
            continue
        if examined >= max_relations:
            reasons.add("relation_limit")
            break
        try:
            rows = reader.parents(key, max_relations - examined)
        except TimeoutError:
            reasons.add("deadline")
            break
        if deadline is not None and monotonic() >= deadline:
            reasons.add("deadline")
            break
        visited.add(key)
        for row in rows:
            if examined >= max_relations:
                reasons.add("relation_limit")
                break
            examined += 1
            if (row.get("relation_type") != "CONTAINS_PART" or not row.get("occurrence_id")
                    or not str(row.get("source_key", "")).startswith("document:")
                    or not row.get("sources") or not all(_valid_source(s) for s in row["sources"])):
                reasons.add("invalid_occurrence")
                continue
            if row["occurrence_id"] not in edge_ids:
                edge_ids.add(row["occurrence_id"])
                if len(edges) < max_generation_edges:
                    edges.append(row)
            stack.append((row["source_key"], (*ancestors, key), level + 1))
        if examined >= max_relations:
            reasons.add("relation_limit")
    # A product-wide corpus declaration is required to prove negative results.
    return {"contract_version": CONTRACT_VERSION, "status": "partial" if edges else "unknown",
            "reasons": tuple(sorted(reasons)), "edges": tuple(edges), "manifest": None,'examined_relations':examined}


def validate_graph_limits(relations,generation):
    if type(relations) is not int or not 0<=relations<=MAX_RELATIONS or type(generation) is not int or not 0<=generation<=MAX_GENERATION_EDGES:
        raise ValueError("Graph limits must stay within v2 bounds")
