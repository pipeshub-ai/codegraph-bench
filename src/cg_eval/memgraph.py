"""An in-memory graph store for running PipesHub's code-graph pipeline offline.

It implements the ``IGraphDBProvider`` methods that block projection, the edge
builder and the agent tools call, with the same semantics production has where
the review found they matter:

* files with identical bytes share a ``virtualRecordId`` (dedup by content), and
  the accessible-set lookup maps each virtual id to one record (A-7);
* name search can mimic Neo4j (ranked full-text) or ArangoDB (unordered
  substring scan, capped at 400 rows before exact filtering) (SC-6).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

BLOCKS = "blocks"
CODE_FILES = "codeFiles"
RECORDS = "records"
RECORD_GROUPS = "recordGroups"


@dataclass
class FileRow:
    record_id: str
    path: str
    repo: str
    connector_id: str
    group_id: str
    content: bytes
    language: str | None
    role: str = "source"


@dataclass
class MemGraph:
    org_id: str
    search_backend: str = "neo4j"
    blocks: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    records: dict[str, dict] = field(default_factory=dict)
    code_files: dict[str, dict] = field(default_factory=dict)
    blobs: dict[str, dict] = field(default_factory=dict)          # virtualRecordId -> blob
    denied: set[str] = field(default_factory=set)                 # record ids the user can't read
    groups: dict[str, dict] = field(default_factory=dict)

    # -- setup ------------------------------------------------------------

    def add_record(self, row: FileRow) -> None:
        virtual = hashlib.md5(row.content).hexdigest()
        self.records[row.record_id] = {
            "_key": row.record_id, "id": row.record_id, "orgId": self.org_id,
            "connectorId": row.connector_id, "recordGroupId": row.group_id,
            "virtualRecordId": virtual, "recordName": row.path.rsplit("/", 1)[-1],
            "recordType": "CODE_FILE", "repo": row.repo,
        }
        # As GitHub files code: a "Code repository" group under the named repo group.
        self.groups.setdefault(row.group_id, {
            "_key": row.group_id, "orgId": self.org_id, "connectorId": row.connector_id,
            "groupName": "Code repository", "parentExternalGroupId": f"ext-{row.repo}",
            "externalGroupId": f"ext-{row.repo}-code-repository",
        })
        self.groups.setdefault(f"repo-{row.repo}", {
            "_key": f"repo-{row.repo}", "orgId": self.org_id, "connectorId": row.connector_id,
            "groupName": row.repo, "externalGroupId": f"ext-{row.repo}",
        })
        self.code_files[row.record_id] = {
            "_key": row.record_id, "id": row.record_id, "orgId": self.org_id,
            "filePath": row.path, "fileRole": row.role, "referencedNames": [],
        }

    def path_of(self, record_id: str) -> str | None:
        doc = self.code_files.get(record_id)
        return doc["filePath"] if doc else None

    # -- block projection / edge builder ----------------------------------

    async def load(self, org_id, record_ids):
        from app.modules.code_graph.facts_source import facts_from_blob_record

        out = {}
        for record_id in record_ids:
            record = self.records.get(record_id)
            blob = self.blobs.get(record["virtualRecordId"]) if record else None
            if blob is not None:
                out[record_id] = facts_from_blob_record(record_id, blob)
        return out

    async def get_file_paths_for_records(self, org_id, record_ids, transaction=None, raise_on_error=False):
        from app.services.graph_db.interface.graph_db_provider import CodeFileInfo

        return {
            rid: CodeFileInfo(file_path=self.code_files[rid]["filePath"],
                              referenced_names=tuple(self.code_files[rid].get("referencedNames") or ()),
                              **({"removed_names": tuple(self.code_files[rid]["removedNames"])}
                                 if self.code_files[rid].get("removedNames") else {}))
            for rid in record_ids if rid in self.code_files
        }

    async def get_document(self, key, collection, transaction=None, *, raise_on_error=False):
        if collection == CODE_FILES:
            return self.code_files.get(key)
        if collection == RECORDS:
            return self.records.get(key)
        if collection == BLOCKS:
            return self.blocks.get(key)
        return None

    async def update_node(self, key, collection, updates, transaction=None):
        target = {CODE_FILES: self.code_files, RECORDS: self.records, BLOCKS: self.blocks}.get(collection)
        if target is not None and key in target:
            target[key].update(updates)
        return True

    async def get_documents_paginated(self, collection, skip=0, limit=50, filters=None,
                                      sort_field=None, transaction=None, raise_on_error=False,
                                      after_key=None, return_fields=None):
        if collection != BLOCKS:
            return []
        rows = sorted(
            (d for d in self.blocks.values()
             if all(d.get(k) == v for k, v in (filters or {}).items())
             and (after_key is None or d["_key"] > after_key)),
            key=lambda d: d["_key"],
        )[skip: skip + limit]
        if return_fields:
            return [{f: d.get(f) for f in return_fields} for d in rows]
        return rows

    async def count_nodes_by_filters(self, collection, filters=None, in_filters=None,
                                     transaction=None):
        return sum(1 for d in self._docs(collection)
                   if all(d.get(k) == v for k, v in (filters or {}).items())
                   and all(d.get(k) in vs for k, vs in (in_filters or {}).items()))

    async def get_edges_by_target_keys(self, target_keys, edge_collection, filters=None,
                                       return_field="_from", transaction=None,
                                       raise_on_error=False):
        targets = set(target_keys)
        return sorted({
            e[return_field] for e in self.edges
            if e.get("_to") in targets and all(e.get(k) == v for k, v in (filters or {}).items())
        })

    async def delete_edges_by_source_keys(self, source_keys, edge_collection, filters=None,
                                          transaction=None, exclude=None):
        sources = set(source_keys)
        before = len(self.edges)
        self.edges = [
            e for e in self.edges
            if not (e.get("_from") in sources
                    and all(e.get(k) == v for k, v in (filters or {}).items())
                    and not any(e.get(k) == v for k, v in (exclude or {}).items()))
        ]
        return before - len(self.edges)

    async def delete_edges_touching_nodes(self, node_keys, node_collection, edge_collection,
                                          transaction=None):
        ids = {f"{node_collection}/{k}" for k in node_keys}
        before = len(self.edges)
        self.edges = [e for e in self.edges if e.get("_from") not in ids and e.get("_to") not in ids]
        return before - len(self.edges)

    async def get_nodes_by_filters(self, collection, filters, return_fields=None, transaction=None,
                                   **_):
        docs = self._docs(collection)
        rows = [d for d in docs if all(d.get(k) == v for k, v in filters.items())]
        if return_fields:
            return [{f: d.get(f) for f in return_fields} for d in rows]
        return rows

    async def batch_upsert_nodes(self, nodes, collection, transaction=None):
        for node in nodes:
            doc = dict(node)
            doc["_key"] = doc.pop("id")
            if collection == BLOCKS:
                self.blocks[doc["_key"]] = doc
        return True

    async def delete_nodes(self, keys, collection, transaction=None):
        for key in keys:
            self.blocks.pop(key, None)
        return True

    async def batch_upsert_record_relations(self, edges, transaction=None):
        # Both providers update a matching edge in place.
        index = {(e["_from"], e["_to"], e["relationshipType"]): e for e in self.edges}
        for edge in edges:
            key = (edge["_from"], edge["_to"], edge["relationshipType"])
            if key in index:
                index[key].update(edge)
            else:
                index[key] = dict(edge)
                self.edges.append(index[key])
        return True

    # main's record-links model renamed the method; edges here are one list either way.
    batch_upsert_node_relations = batch_upsert_record_relations

    async def delete_blocks_for_records(self, record_ids, transaction=None):
        wanted = set(record_ids)
        keys = [k for k, d in self.blocks.items() if d.get("recordId") in wanted]
        await self.delete_edges_touching_nodes(keys, BLOCKS, "recordRelations")
        for k in keys:
            self.blocks.pop(k)
        return len(keys)

    # -- agent tools ------------------------------------------------------

    def _docs(self, collection: str) -> list[dict]:
        return list({BLOCKS: self.blocks, CODE_FILES: self.code_files, RECORDS: self.records,
                     RECORD_GROUPS: self.groups, "syncPoints": self.sync_points}.get(collection, {}).values())

    # Sync points, so a build's state can be recorded as the production runner records it.
    @property
    def sync_points(self) -> dict:
        return self.__dict__.setdefault("_sync_points", {})

    async def upsert_sync_point(self, sync_point_key, sync_point_data, collection, transaction=None):
        self.sync_points.setdefault(sync_point_key, {"_key": sync_point_key, "syncPointKey": sync_point_key}).update(sync_point_data)
        return True

    async def update_sync_point_if(self, sync_point_key, expected, sync_point_data, collection):
        doc = self.sync_points.get(sync_point_key)
        if doc is None or any(doc.get(k) != v for k, v in expected.items()):
            return False
        doc.update(sync_point_data)
        return True

    async def get_nodes_by_field_in(self, collection, field_name, field_values, return_fields=None,
                                    transaction=None, *, raise_on_error=False):
        wanted = set(field_values)
        rows = [d for d in self._docs(collection) if d.get(field_name) in wanted]
        if return_fields:
            return [{f: d.get(f) for f in return_fields} for d in rows]
        return rows

    async def get_nodes_by_field_prefix(self, collection, field_name, prefix, filters=None,
                                        limit=400, transaction=None, raise_on_error=False):
        rows = [d for d in self._docs(collection)
                if str(d.get(field_name) or "").startswith(prefix)
                and all(d.get(k) == v for k, v in (filters or {}).items())]
        return sorted(rows, key=lambda d: d.get("_key"))[:limit]

    async def search_nodes_by_field_terms(self, collection, field_name, terms, filters=None,
                                          limit=400, transaction=None, raise_on_error=False):
        lowered = [t.lower() for t in terms]
        rows = [d for d in self._docs(collection)
                if d.get(field_name)
                and any(t in str(d[field_name]).lower() for t in lowered)
                and all(d.get(k) == v for k, v in (filters or {}).items())]
        if self.search_backend == "neo4j":
            def rank(d):
                value = str(d[field_name]).lower()
                return (0 if value in lowered else 1, len(value), d["_key"])
            rows.sort(key=rank)
        else:
            # Arango: CONTAINS with LIMIT in document order, exact filtering after.
            rows.sort(key=lambda d: d["_key"])
        return rows[:limit]

    async def get_user_connector_instances(self, collection, user_id, org_id,
                                           team_scope, personal_scope):
        ids = sorted({g["connectorId"] for g in self.groups.values()})
        return [{"_key": c, "name": "GitHub", "type": "GITHUB TEAMS"} for c in ids]

    async def get_records_by_record_group(self, record_group_id, connector_id, org_id,
                                          depth, user_key=None, limit=None, **_):
        from types import SimpleNamespace

        from app.models.entities import RecordType

        rows = [SimpleNamespace(id=rid, record_type=RecordType(r["recordType"]))
                for rid, r in self.records.items()
                if r["recordGroupId"] == record_group_id and r["connectorId"] == connector_id]
        return rows[:limit] if limit else rows

    async def filter_accessible_record_ids(self, record_ids, user_id, org_id, *,
                                           transaction=None):
        return {r for r in record_ids if r in self.records and r not in self.denied}

    async def check_record_access_with_details(self, user_id, org_id, record_id):
        if record_id in self.denied or record_id not in self.records:
            return None
        return {"record": self.records[record_id]}

    async def get_accessible_virtual_record_ids(self, user_id, org_id, filters=None,
                                                time_range=None, *, raise_on_error=False,
                                                exclude_app_ids=frozenset()):
        out: dict[str, str] = {}
        for rid, rec in self.records.items():
            if rid in self.denied or rec["connectorId"] in exclude_app_ids:
                continue
            apps = (filters or {}).get("apps")
            if apps and rec["connectorId"] not in apps:
                continue
            out.setdefault(rec["virtualRecordId"], rid)
        return out

    async def get_neighbors_for_nodes_by_relationship_types(self, node_keys, node_collection,
                                                            relationship_types, direction,
                                                            limit=5000, transaction=None, raise_on_error=False):
        anchors = {f"{node_collection}/{k}" for k in node_keys}
        wanted = set(relationship_types)
        out = []
        for e in self.edges:
            if e.get("relationshipType") not in wanted:
                continue
            for anchor_f, other_f, label in (("_from", "_to", "outbound"), ("_to", "_from", "inbound")):
                if direction not in (label, "any") or e.get(anchor_f) not in anchors:
                    continue
                collection, _, key = e[other_f].partition("/")
                out.append({"anchorKey": e[anchor_f].partition("/")[2], "collection": collection,
                            "key": key, "direction": label,
                            "relationshipType": e["relationshipType"]})
        return out[:limit]

    async def get_neighbors_by_relationship_types(self, node_key, node_collection,
                                                  relationship_types, direction, limit=25,
                                                  transaction=None, raise_on_error=False):
        rows = await self.get_neighbors_for_nodes_by_relationship_types(
            [node_key], node_collection, relationship_types, direction, limit)
        return [{k: r[k] for k in ("collection", "key", "relationshipType")} for r in rows]


class MemBlobStore:
    def __init__(self, graph: MemGraph) -> None:
        self.graph = graph

    async def get_record_from_storage(self, virtual_record_id, org_id):
        return self.graph.blobs.get(virtual_record_id)
