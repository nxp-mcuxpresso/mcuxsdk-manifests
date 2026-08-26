# Copyright 2026 NXP
#
# SPDX-License-Identifier: BSD-3-Clause

"""Unit tests for sbom_merge.py (stdlib unittest only, no third-party deps)."""

import unittest

from sbom_merge import SbomMerger, derive_roots, scope_project


def _pkg(spdxid, name, version='1.0.0', download='NOASSERTION'):
    return {
        'SPDXID': spdxid,
        'name': name,
        'versionInfo': version,
        'downloadLocation': download,
        'filesAnalyzed': False,
        'licenseConcluded': 'NOASSERTION',
        'licenseDeclared': 'NOASSERTION',
        'supplier': 'NOASSERTION',
    }


def _rel(src, rel_type, tgt):
    return {'spdxElementId': src, 'relationshipType': rel_type, 'relatedSpdxElement': tgt}


def _sbom_doc(packages, relationships):
    return {
        'spdxVersion': 'SPDX-2.3',
        'dataLicense': 'CC0-1.0',
        'SPDXID': 'SPDXRef-DOCUMENT',
        'name': 'per-project-sbom',
        'packages': packages,
        'relationships': relationships,
    }


class TestDeriveRoots(unittest.TestCase):
    def test_uses_describes_relationship(self):
        doc = _sbom_doc(
            [_pkg('SPDXRef-Package-a', 'a'), _pkg('SPDXRef-Package-b', 'b')],
            [_rel('SPDXRef-DOCUMENT', 'DESCRIBES', 'SPDXRef-Package-a')])
        roots = derive_roots(doc, 'proj')
        self.assertEqual([p['SPDXID'] for p in roots], ['SPDXRef-Package-a'])

    def test_falls_back_to_no_incoming_edge(self):
        warnings = []
        doc = _sbom_doc(
            [_pkg('SPDXRef-Package-a', 'a'), _pkg('SPDXRef-Package-b', 'b')],
            [_rel('SPDXRef-Package-a', 'CONTAINS', 'SPDXRef-Package-b')])
        roots = derive_roots(doc, 'proj', warn=warnings.append)
        self.assertEqual([p['SPDXID'] for p in roots], ['SPDXRef-Package-a'])
        self.assertTrue(warnings)

    def test_falls_back_to_every_package_when_no_topology(self):
        warnings = []
        doc = _sbom_doc([_pkg('SPDXRef-Package-a', 'a'), _pkg('SPDXRef-Package-b', 'b')], [])
        roots = derive_roots(doc, 'proj', warn=warnings.append)
        self.assertEqual(len(roots), 2)
        self.assertTrue(warnings)


class TestScopeProject(unittest.TestCase):
    def test_bfs_pulls_in_contains_children(self):
        umbrella = _pkg('SPDXRef-Package-umbrella', 'umbrella')
        child = _pkg('SPDXRef-Package-child', 'child')
        doc = _sbom_doc(
            [umbrella, child],
            [_rel('SPDXRef-DOCUMENT', 'DESCRIBES', 'SPDXRef-Package-umbrella'),
             _rel('SPDXRef-Package-umbrella', 'CONTAINS', 'SPDXRef-Package-child')])
        roots = derive_roots(doc, 'proj')
        entries, internal_rels = scope_project(doc, roots)

        names = {(e['package']['name'], e['is_root']) for e in entries}
        self.assertEqual(names, {('umbrella', True), ('child', False)})
        self.assertEqual(len(internal_rels), 1)
        self.assertEqual(internal_rels[0]['type'], 'CONTAINS')

    def test_does_not_cross_into_unreachable_siblings(self):
        umbrella = _pkg('SPDXRef-Package-umbrella', 'umbrella')
        child = _pkg('SPDXRef-Package-child', 'child')
        stray = _pkg('SPDXRef-Package-stray', 'stray')
        doc = _sbom_doc(
            [umbrella, child, stray],
            [_rel('SPDXRef-DOCUMENT', 'DESCRIBES', 'SPDXRef-Package-umbrella'),
             _rel('SPDXRef-Package-umbrella', 'CONTAINS', 'SPDXRef-Package-child')])
        roots = derive_roots(doc, 'proj')
        entries, _ = scope_project(doc, roots)
        self.assertEqual({e['package']['name'] for e in entries}, {'umbrella', 'child'})


class TestSbomMerger(unittest.TestCase):
    def _single_root_sbom(self, name, version='1.0.0'):
        pkg = _pkg(f'SPDXRef-Package-{name}', name, version)
        return _sbom_doc([pkg], [_rel('SPDXRef-DOCUMENT', 'DESCRIBES', pkg['SPDXID'])])

    def test_two_independent_projects_no_overlap(self):
        merger = SbomMerger()
        merged = merger.merge([
            {'project_name': 'proj-a', 'sbom_data': self._single_root_sbom('foo')},
            {'project_name': 'proj-b', 'sbom_data': self._single_root_sbom('bar')},
        ])

        names = sorted(p['name'] for p in merged['packages'])
        self.assertEqual(names, ['MCUXpresso-SDK', 'bar', 'foo'])

        describes = [r for r in merged['relationships'] if r['relationshipType'] == 'DESCRIBES']
        self.assertEqual(len(describes), 1)
        contains = [r for r in merged['relationships'] if r['relationshipType'] == 'CONTAINS']
        self.assertEqual(len(contains), 2)

    def test_shared_package_across_projects_is_deduped(self):
        shared_a = self._single_root_sbom('shared', version='2.0.0')
        shared_b = self._single_root_sbom('shared', version='2.0.0')

        merger = SbomMerger()
        merged = merger.merge([
            {'project_name': 'proj-a', 'sbom_data': shared_a},
            {'project_name': 'proj-b', 'sbom_data': shared_b},
        ])

        shared_pkgs = [p for p in merged['packages'] if p['name'] == 'shared']
        self.assertEqual(len(shared_pkgs), 1)
        self.assertNotIn('annotations', shared_pkgs[0])

        contains = [r for r in merged['relationships']
                    if r['relationshipType'] == 'CONTAINS' and r['relatedSpdxElement'] == shared_pkgs[0]['SPDXID']]
        self.assertEqual(len(contains), 1)

    def test_no_describes_relationship_falls_back_without_crashing(self):
        doc = _sbom_doc([_pkg('SPDXRef-Package-x', 'x')], [])
        merger = SbomMerger()
        merged = merger.merge([{'project_name': 'legacy-proj', 'sbom_data': doc}])
        self.assertIn('x', [p['name'] for p in merged['packages']])

    def test_document_matches_nxp_policy(self):
        merger = SbomMerger()
        merged = merger.merge([{'project_name': 'proj-a', 'sbom_data': self._single_root_sbom('foo')}])

        self.assertTrue(merged['documentNamespace'].startswith('https://nxp.com/spdx'))
        self.assertEqual(merged['dataLicense'], 'CC0-1.0')
        self.assertEqual(merged['creationInfo']['creators'], ['Organization: NXP'])
        self.assertEqual(merged['creationInfo']['licenseListVersion'], '3.26')

        root = next(p for p in merged['packages'] if p['SPDXID'].startswith('SPDXRef-Package-MCUXpresso'))
        self.assertEqual(root['originator'], 'Organization: NXP')

    def test_forbidden_keys_stripped_and_supplier_forced(self):
        pkg = self._single_root_sbom('foo')
        pkg['packages'][0]['checksums'] = [{'algorithm': 'SHA1', 'checksumValue': 'deadbeef'}]
        pkg['packages'][0]['copyrightText'] = 'NOASSERTION'
        pkg['packages'][0]['supplier'] = 'Organization: SomeVendor'

        merger = SbomMerger()
        merged = merger.merge([{'project_name': 'proj-a', 'sbom_data': pkg}])

        foo = next(p for p in merged['packages'] if p['name'] == 'foo')
        self.assertNotIn('checksums', foo)
        self.assertNotIn('copyrightText', foo)
        self.assertEqual(foo['supplier'], 'NOASSERTION')
        self.assertFalse(foo['filesAnalyzed'])

    def test_spdxid_collision_is_suffixed(self):
        doc_a = _sbom_doc(
            [_pkg('SPDXRef-Package-1', 'alpha')],
            [_rel('SPDXRef-DOCUMENT', 'DESCRIBES', 'SPDXRef-Package-1')])
        doc_b = _sbom_doc(
            [_pkg('SPDXRef-Package-1', 'beta')],
            [_rel('SPDXRef-DOCUMENT', 'DESCRIBES', 'SPDXRef-Package-1')])

        merger = SbomMerger()
        merged = merger.merge([
            {'project_name': 'shared-prefix', 'sbom_data': doc_a},
            {'project_name': 'shared-prefix', 'sbom_data': doc_b},
        ])

        spdxids = [p['SPDXID'] for p in merged['packages']]
        self.assertEqual(len(spdxids), len(set(spdxids)))


if __name__ == '__main__':
    unittest.main()
