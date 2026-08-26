# Copyright 2026 NXP
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Reference-based merge of per-project SPDX SBOMs into one workspace document.

Each manifest project's own SBOM.spdx.json already declares (via its own
DESCRIBES relationship) which package(s) it considers its top-level
component(s). Those are treated as that project's roots -- the workspace-level
equivalent of a resolved `sbom_ref` in the per-package SBOM generator
(mcuxsdk/ecosystem/sdk_generator/lib/SBOM/). From each root, the transitive
closure is expanded by following containment/link relationships *within that
project's own document*, and cross-project deduplication is done by a stable
(name, version, download location) key rather than by concatenating every
package from every file.

Output shape mirrors SDKGenerator::SbomDocumentBuilder (same module, per-package
SBOM generator) so both SBOM producers honor the same NXP Policy: dataLicense =
"CC0-1.0", documentNamespace under "https://nxp.com/spdx", creators = exactly
["Organization: NXP"], one DESCRIBES from SPDXRef-DOCUMENT, per-package
required fields, and package SPDXIDs kept as originally assigned (only
suffixed on an actual collision) rather than rewritten.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

DOC_SPDXID = 'SPDXRef-DOCUMENT'
SPDX_VERSION = 'SPDX-2.3'
DATA_LICENSE = 'CC0-1.0'
NAMESPACE_ROOT = 'https://nxp.com/spdx'
LICENSE_LIST_VERSION = '3.26'
NXP_ORG = 'Organization: NXP'

# Relationship types that express "the source contains/depends on the
# target" and are therefore followed when expanding a root's transitive
# closure within its own project.
EXPAND_TYPES = {'CONTAINS', 'DYNAMIC_LINK', 'STATIC_LINK', 'HAS_PREREQUISITE'}

# Fields the NXP Policy forbids on per-package entries (mirrors
# SbomDocumentBuilder::POLICY_FORBIDDEN_PACKAGE_KEYS).
POLICY_FORBIDDEN_PACKAGE_KEYS = {
    'checksums', 'copyrightText', 'hasFiles', 'attributionTexts',
    'sourceInfo', 'packageVerificationCode',
}

# Fields REQUIRED_PACKAGE_KEYS in SbomValidator; missing ones get a
# NOASSERTION fallback so every merged package still validates.
_PACKAGE_FALLBACKS = {
    'name': 'NOASSERTION',
    'versionInfo': 'NOASSERTION',
    'downloadLocation': 'NOASSERTION',
    'licenseConcluded': 'NOASSERTION',
    'licenseDeclared': 'NOASSERTION',
}

_NoOp = lambda *_args, **_kwargs: None


def dedup_key(package: Dict) -> str:
    """Stable cross-project identity for a package: name/version/download location."""
    name = package.get('name', '')
    version = package.get('versionInfo', '')
    download = package.get('downloadLocation', '')
    return f"{name}|{version}|{download}"


def _sanitize(text: str) -> str:
    return re.sub(r'[^A-Za-z0-9.\-]', '-', text)


def _packages_by_spdxid(sbom_doc: Dict) -> Dict[str, Dict]:
    return {p['SPDXID']: p for p in sbom_doc.get('packages', []) if p.get('SPDXID')}


def derive_roots(sbom_doc: Dict, project_name: str,
                  warn: Optional[Callable[[str], None]] = None) -> List[Dict]:
    """Find the package(s) a project's own document DESCRIBES.

    Falls back to packages with no incoming containment edge, then to every
    package in the document, if the source SBOM predates/lacks a DESCRIBES
    relationship -- so malformed or legacy per-project SBOMs still merge
    instead of contributing nothing, at the cost of a logged warning.
    """
    warn = warn or _NoOp
    by_id = _packages_by_spdxid(sbom_doc)
    relationships = sbom_doc.get('relationships', [])

    describes_ids = {
        r.get('relatedSpdxElement') for r in relationships
        if r.get('relationshipType') == 'DESCRIBES' and r.get('spdxElementId') == DOC_SPDXID
    }
    roots = [by_id[i] for i in describes_ids if i in by_id]
    if roots:
        return roots

    contained_ids = {
        r.get('relatedSpdxElement') for r in relationships
        if r.get('relationshipType') in EXPAND_TYPES
    }
    roots = [pkg for spdxid, pkg in by_id.items() if spdxid not in contained_ids]
    if roots:
        warn(f"Project '{project_name}' SBOM has no DESCRIBES relationship; inferring "
             f"{len(roots)} root(s) from packages with no incoming containment edge")
        return roots

    warn(f"Project '{project_name}' SBOM has no DESCRIBES relationship and no "
         f"relationship-derivable roots; treating all {len(by_id)} package(s) as roots")
    return list(by_id.values())


def scope_project(sbom_doc: Dict, roots: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """BFS from each root over EXPAND_TYPES edges within this project's own SBOM.

    Returns (entries, internal_relationships):
      - entries: [{'package': <dict>, 'is_root': bool}], one per reachable package
      - internal_relationships: [{'src_key': dedup_key, 'tgt_key': dedup_key, 'type': str}],
        using dedup_key endpoints so callers can remap them onto final SPDXIDs later
    """
    by_id = _packages_by_spdxid(sbom_doc)
    relationships = sbom_doc.get('relationships', [])

    children_by_id: Dict[str, List[str]] = {}
    for r in relationships:
        if r.get('relationshipType') not in EXPAND_TYPES:
            continue
        src, tgt = r.get('spdxElementId'), r.get('relatedSpdxElement')
        if src and tgt:
            children_by_id.setdefault(src, []).append(tgt)

    visited: Set[str] = set()
    entries: List[Dict] = []
    queue: List[Tuple[Dict, bool]] = [(root, True) for root in roots]

    while queue:
        pkg, is_root = queue.pop(0)
        spdxid = pkg.get('SPDXID')
        if not spdxid or spdxid in visited:
            continue
        visited.add(spdxid)
        entries.append({'package': pkg, 'is_root': is_root})
        for child_id in children_by_id.get(spdxid, []):
            child_pkg = by_id.get(child_id)
            if child_pkg is not None:
                queue.append((child_pkg, False))

    internal_relationships: List[Dict] = []
    for r in relationships:
        rel_type = r.get('relationshipType')
        if rel_type not in EXPAND_TYPES:
            continue
        src_id, tgt_id = r.get('spdxElementId'), r.get('relatedSpdxElement')
        if src_id not in visited or tgt_id not in visited:
            continue
        src_pkg, tgt_pkg = by_id.get(src_id), by_id.get(tgt_id)
        if src_pkg is None or tgt_pkg is None:
            continue
        internal_relationships.append({
            'src_key': dedup_key(src_pkg),
            'tgt_key': dedup_key(tgt_pkg),
            'type': rel_type,
        })

    return entries, internal_relationships


def _sanitize_package(pkg: Dict, final_spdxid: str) -> Dict:
    """Strip Policy-forbidden fields and force Policy-required fields/fallbacks.

    Mirrors SbomDocumentBuilder#sanitize_package: supplier and filesAnalyzed
    are always overwritten (NXP Policy forbids any supplier other than
    NOASSERTION and requires filesAnalyzed=false); the rest only fall back
    to NOASSERTION when absent so real upstream metadata (license, purl
    externalRefs, originator, homepage, ...) is preserved.
    """
    out = dict(pkg)
    for key in POLICY_FORBIDDEN_PACKAGE_KEYS:
        out.pop(key, None)

    out['SPDXID'] = final_spdxid
    for key, fallback in _PACKAGE_FALLBACKS.items():
        if not out.get(key):
            out[key] = fallback
    out['supplier'] = 'NOASSERTION'
    out['filesAnalyzed'] = False
    return out


class SbomMerger:
    """Merges scoped per-project package sets into one workspace SPDX document."""

    def __init__(self, output_name: str = 'MCUXpresso-SDK-SBOM',
                 root_package_name: str = 'MCUXpresso-SDK',
                 root_package_version: str = 'NOASSERTION',
                 warn: Optional[Callable[[str], None]] = None,
                 debug: Optional[Callable[[str], None]] = None):
        self._output_name = output_name
        self._root_package_name = root_package_name
        self._root_package_version = root_package_version
        self._warn = warn or _NoOp
        self._debug = debug or _NoOp

    def merge(self, sbom_files: List[Dict]) -> Dict:
        """sbom_files: [{'project_name': str, 'sbom_data': <parsed SPDX dict>}, ...]."""
        created = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        root_spdxid = f"SPDXRef-Package-{_sanitize(self._root_package_name)}"

        packages_by_key: Dict[str, Dict] = {}
        ordered_keys: List[str] = []
        all_internal_relationships: List[Dict] = []

        for info in sbom_files:
            project_name = info['project_name']
            sbom_doc = info['sbom_data']

            roots = derive_roots(sbom_doc, project_name, warn=self._warn)
            entries, internal_rels = scope_project(sbom_doc, roots)
            all_internal_relationships.extend(internal_rels)
            self._debug(f"Project '{project_name}': {len(roots)} root(s), "
                        f"{len(entries)} package(s) in scope, {len(internal_rels)} internal relationship(s)")

            for entry in entries:
                pkg = entry['package']
                key = dedup_key(pkg)
                if key in packages_by_key:
                    existing = packages_by_key[key]
                    existing['is_root'] = existing['is_root'] or entry['is_root']
                    existing['contributing_projects'].add(project_name)
                    continue
                packages_by_key[key] = {
                    'package': dict(pkg),
                    'is_root': entry['is_root'],
                    'contributing_projects': {project_name},
                    'original_spdxid': pkg.get('SPDXID') or f'SPDXRef-Package-{len(ordered_keys) + 1}',
                }
                ordered_keys.append(key)

        final_spdxid_by_key = self._assign_spdxids(packages_by_key, ordered_keys, root_spdxid)
        merged_packages = self._build_packages(packages_by_key, ordered_keys, final_spdxid_by_key)
        relationships = self._build_relationships(
            packages_by_key, ordered_keys, final_spdxid_by_key, all_internal_relationships, root_spdxid)

        root_package = {
            'SPDXID': root_spdxid,
            'name': self._root_package_name,
            'versionInfo': self._root_package_version,
            'supplier': 'NOASSERTION',
            'originator': NXP_ORG,
            'downloadLocation': 'NOASSERTION',
            'filesAnalyzed': False,
            'licenseConcluded': 'NOASSERTION',
            'licenseDeclared': 'NOASSERTION',
        }

        all_packages = [root_package] + merged_packages
        all_packages.sort(key=lambda p: p['SPDXID'])
        relationships.sort(key=lambda r: (r['spdxElementId'], r['relationshipType'], r['relatedSpdxElement']))

        return {
            'SPDXID': DOC_SPDXID,
            'spdxVersion': SPDX_VERSION,
            'dataLicense': DATA_LICENSE,
            'name': self._output_name,
            'documentNamespace': self._document_namespace(),
            'creationInfo': {
                'created': created,
                'creators': [NXP_ORG],
                'licenseListVersion': LICENSE_LIST_VERSION,
            },
            'packages': all_packages,
            'relationships': relationships,
        }

    def _document_namespace(self) -> str:
        return f"{NAMESPACE_ROOT}/workspace/{_sanitize(self._output_name)}-{uuid.uuid4()}"

    def _assign_spdxids(self, packages_by_key: Dict[str, Dict], ordered_keys: List[str],
                         root_spdxid: str) -> Dict[str, str]:
        """Collision-safe SPDXID per merged package.

        Keeps each package's own SPDXID (already unique-by-construction in
        well-formed per-project SBOMs, e.g. SPDXRef-package-<uuid>) and only
        appends a numeric suffix if two projects' packages actually collide
        on the same id -- mirrors SbomDocumentBuilder#dedupe_spdxids.
        """
        seen = {DOC_SPDXID, root_spdxid}
        final_by_key: Dict[str, str] = {}
        for key in ordered_keys:
            original_id = packages_by_key[key]['original_spdxid']
            candidate = original_id
            suffix = 0
            while candidate in seen:
                suffix += 1
                candidate = f"{original_id}-{suffix}"
            seen.add(candidate)
            final_by_key[key] = candidate
        return final_by_key

    def _build_packages(self, packages_by_key: Dict[str, Dict], ordered_keys: List[str],
                         final_spdxid_by_key: Dict[str, str]) -> List[Dict]:
        result = []
        for key in ordered_keys:
            entry = packages_by_key[key]
            self._debug(f"Package '{entry['package'].get('name')}' contributed by: "
                        + ', '.join(sorted(entry['contributing_projects'])))
            result.append(_sanitize_package(entry['package'], final_spdxid_by_key[key]))
        return result

    def _build_relationships(self, packages_by_key: Dict[str, Dict], ordered_keys: List[str],
                              final_spdxid_by_key: Dict[str, str], internal_relationships: List[Dict],
                              root_spdxid: str) -> List[Dict]:
        seen_triples: Set[Tuple[str, str, str]] = set()
        relationships: List[Dict] = []

        def add(src: str, rel_type: str, tgt: str) -> None:
            triple = (src, rel_type, tgt)
            if src == tgt or triple in seen_triples:
                return
            seen_triples.add(triple)
            relationships.append({
                'spdxElementId': src,
                'relationshipType': rel_type,
                'relatedSpdxElement': tgt,
            })

        add(DOC_SPDXID, 'DESCRIBES', root_spdxid)

        for key in ordered_keys:
            if packages_by_key[key]['is_root']:
                add(root_spdxid, 'CONTAINS', final_spdxid_by_key[key])

        for rel in internal_relationships:
            src_id = final_spdxid_by_key.get(rel['src_key'])
            tgt_id = final_spdxid_by_key.get(rel['tgt_key'])
            if src_id and tgt_id:
                add(src_id, rel['type'], tgt_id)

        return relationships
