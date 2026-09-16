# Copyright 2026 NXP
#
# SPDX-License-Identifier: BSD-3-Clause

#!/usr/bin/env python3
"""
Generic SPDX 2.3 SBOM Validator
================================
Validates an SBOM JSON document against the public SPDX 2.3
specification only (document/creationInfo/package/relationship
structure, required fields, referential integrity). This module is
intentionally company-policy-free so it is safe to ship in a
publicly-mirrored repository.

An organization-specific policy overlay (if any) is applied
separately by the caller — see sbom_collect.py, which looks for an
optional internal overlay validator and merges its findings with
this one's.

Also used in-process (see sbom_collect.py) by calling
SPDX23Validator().validate(sbom_dict) directly on a parsed document,
without going through a file.

Usage:
    python spdx23_validator.py <sbom_file.json> [--strict] [--json]

Options:
    --strict   Treat SHOULD/RECOMMENDED violations as errors
    --json     Output results as JSON instead of human-readable text
"""

import json
import re
import sys
import argparse
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum
from datetime import datetime


# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    ERROR = "ERROR"        # MUST / SHALL - spec violation
    WARNING = "WARNING"    # SHOULD / RECOMMENDED - best practice
    INFO = "INFO"          # informational / minor note


@dataclass
class Issue:
    severity: Severity
    section: str           # short rule tag, e.g. "doc", "package", "relationship"
    message: str
    location: Optional[str] = None  # e.g. package SPDXID or field path

    def __str__(self):
        loc = f" [{self.location}]" if self.location else ""
        return f"[{self.severity.value}] ({self.section}){loc} {self.message}"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
class SPDX23Validator:
    # Fields every package must carry per the SPDX 2.3 spec (value may
    # legitimately be NOASSERTION/NONE) -- presence only, no company
    # value constraints; an overlay validator may add its own.
    REQUIRED_PACKAGE_KEYS = (
        "SPDXID", "name", "versionInfo", "supplier", "downloadLocation",
        "filesAnalyzed", "licenseConcluded", "licenseDeclared",
    )

    # SPDX license list identifiers (common subset; full list at spdx.org/licenses)
    # We use a pattern to identify obviously invalid SPDX expressions;
    # unknown identifiers that are not LicenseRef- are flagged as warnings.
    KNOWN_SPDX_LICENSES = {
        "MIT", "Apache-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
        "GPL-2.0", "GPL-3.0-only", "GPL-3.0-or-later", "GPL-3.0",
        "LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only",
        "LGPL-2.1-or-later", "LGPL-2.1", "LGPL-3.0-only",
        "BSD-2-Clause", "BSD-3-Clause", "BSD-4-Clause",
        "ISC", "MPL-2.0", "CDDL-1.0", "EPL-1.0", "EPL-2.0",
        "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0",
        "Unlicense", "WTFPL", "Zlib",
        "OpenSSL", "SSLeay", "Artistic-2.0",
        "NOASSERTION", "NONE",
        # Common exception
        "Linux-syscall-note",
    }

    # SPDX 2.3 Annex/relationship-type list (informational spec vocabulary,
    # not a company-restricted allow-list): unrecognized values only warn.
    KNOWN_RELATIONSHIP_TYPES = {
        "DESCRIBES", "DESCRIBED_BY", "CONTAINS", "CONTAINED_BY",
        "DEPENDS_ON", "DEPENDENCY_OF", "DEPENDENCY_MANIFEST_OF",
        "BUILD_DEPENDENCY_OF", "DEV_DEPENDENCY_OF", "OPTIONAL_DEPENDENCY_OF",
        "PROVIDED_DEPENDENCY_OF", "TEST_DEPENDENCY_OF", "RUNTIME_DEPENDENCY_OF",
        "GENERATES", "GENERATED_FROM", "ANCESTOR_OF", "DESCENDANT_OF",
        "VARIANT_OF", "DISTRIBUTION_ARTIFACT", "PATCH_FOR", "PATCH_APPLIED",
        "COPY_OF", "FILE_ADDED", "FILE_DELETED", "FILE_MODIFIED",
        "EXPANDED_FROM_ARCHIVE", "DYNAMIC_LINK", "STATIC_LINK",
        "DATA_FILE_OF", "TEST_CASE_OF", "BUILD_TOOL_OF", "DEV_TOOL_OF",
        "TEST_OF", "TEST_TOOL_OF", "DOCUMENTATION_OF", "OPTIONAL_COMPONENT_OF",
        "METAFILE_OF", "PACKAGE_OF", "AMENDS", "PREREQUISITE_FOR",
        "HAS_PREREQUISITE", "OTHER",
    }

    # SPDXID character rule: SPDXRef- followed by [a-zA-Z0-9\-\.]+
    SPDXID_RE = re.compile(r'^SPDXRef-[a-zA-Z0-9\-\.]+$')

    # Timestamp format
    TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

    # CPE 2.3 prefix
    CPE23_RE = re.compile(r'^cpe:2\.3:[hoa\*\-]:', re.IGNORECASE)

    def __init__(self, strict: bool = False):
        self.strict = strict
        self.issues: List[Issue] = []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _error(self, section: str, msg: str, location: str = None):
        self.issues.append(Issue(Severity.ERROR, section, msg, location))

    def _warn(self, section: str, msg: str, location: str = None):
        sev = Severity.ERROR if self.strict else Severity.WARNING
        self.issues.append(Issue(sev, section, msg, location))

    def _info(self, section: str, msg: str, location: str = None):
        self.issues.append(Issue(Severity.INFO, section, msg, location))

    def _pkg_loc(self, pkg: dict) -> str:
        return pkg.get("SPDXID", "<unknown package>")

    # ------------------------------------------------------------------
    # Top-level validation
    # ------------------------------------------------------------------
    def validate(self, data: dict) -> List[Issue]:
        self.issues = []
        self._check_document_level(data)
        self._check_creation_info(data)
        self._check_namespace_name(data)
        self._check_document_spdxid(data)
        packages = data.get("packages", [])
        if not isinstance(packages, list):
            self._error("doc", "Field 'packages' must be a JSON array")
            packages = []
        extracted_licenses = data.get("hasExtractedLicensingInfos", [])
        license_refs = self._check_extracted_licenses(extracted_licenses)
        for pkg in packages:
            self._check_package(pkg, license_refs)
        self._check_relationships(data, packages)
        return self.issues

    # ------------------------------------------------------------------
    # Document level
    # ------------------------------------------------------------------
    def _check_document_level(self, data: dict):
        spdx_ver = data.get("spdxVersion")
        if spdx_ver != "SPDX-2.3":
            self._error("doc", f"spdxVersion must be 'SPDX-2.3', got '{spdx_ver}'")

        # SPDX 2.3 spec requires the SBOM document's own dataLicense to be CC0-1.0.
        dl = data.get("dataLicense")
        if dl != "CC0-1.0":
            self._error("doc", f"dataLicense must be 'CC0-1.0' per SPDX spec, got '{dl}'")

    # ------------------------------------------------------------------
    # Creation info
    # ------------------------------------------------------------------
    def _check_creation_info(self, data: dict):
        ci = data.get("creationInfo")
        if not ci:
            self._error("creation-info", "Field 'creationInfo' is missing")
            return

        created = ci.get("created", "")
        if not created:
            self._error("creation-info", "creationInfo.created is missing")
        elif not self.TIMESTAMP_RE.match(created):
            self._error("creation-info",
                        f"creationInfo.created must match YYYY-MM-DDThh:mm:ssZ, got '{created}'")
        else:
            try:
                datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                self._error("creation-info", f"creationInfo.created is not a valid date-time: '{created}'")

        creators = ci.get("creators", [])
        if not creators:
            self._error("creation-info", "creationInfo.creators is missing or empty")
        else:
            for c in creators:
                if not (c.startswith("Organization:") or c.startswith("Person:") or c.startswith("Tool:")):
                    self._error("creation-info",
                                f"creator must be 'Organization: <name>', 'Person: <name>', or 'Tool: <name>', got '{c}'")

    # ------------------------------------------------------------------
    # Namespace and name
    # ------------------------------------------------------------------
    def _check_namespace_name(self, data: dict):
        name = data.get("name", "")
        if not name:
            self._error("doc", "Field 'name' (DocumentName) is missing")

        ns = data.get("documentNamespace", "")
        if not ns:
            self._error("doc", "Field 'documentNamespace' is missing")
        elif not (ns.startswith("http://") or ns.startswith("https://")):
            self._warn("doc", f"documentNamespace should be an absolute URI, got '{ns}'")

    # ------------------------------------------------------------------
    # Document SPDXID
    # ------------------------------------------------------------------
    def _check_document_spdxid(self, data: dict):
        sid = data.get("SPDXID")
        if sid != "SPDXRef-DOCUMENT":
            self._error("doc", f"Document SPDXID must be 'SPDXRef-DOCUMENT', got '{sid}'")

    # ------------------------------------------------------------------
    # Extracted licenses
    # ------------------------------------------------------------------
    def _check_extracted_licenses(self, extracted: list) -> set:
        """Returns set of valid LicenseRef- IDs found in hasExtractedLicensingInfos."""
        license_refs = set()
        if not isinstance(extracted, list):
            self._error("license", "'hasExtractedLicensingInfos' must be a JSON array")
            return license_refs

        for i, entry in enumerate(extracted):
            loc = f"hasExtractedLicensingInfos[{i}]"
            lid = entry.get("licenseId", "")
            if not lid:
                self._error("license", "Missing 'licenseId' in license definition", loc)
            elif not lid.startswith("LicenseRef-"):
                self._error("license", f"licenseId must start with 'LicenseRef-', got '{lid}'", loc)
            else:
                license_refs.add(lid)

            if not entry.get("name"):
                self._error("license", "Missing 'name' in license definition", loc)

            if not entry.get("extractedText"):
                self._error("license", "Missing 'extractedText' (full license text) in license definition", loc)

        return license_refs

    # ------------------------------------------------------------------
    # Package validation
    # ------------------------------------------------------------------
    def _check_package(self, pkg: dict, license_refs: set):
        loc = self._pkg_loc(pkg)

        sid = pkg.get("SPDXID", "")
        if not sid:
            self._error("package", "Package is missing 'SPDXID'", loc)
        elif not self.SPDXID_RE.match(sid):
            self._error("package",
                        f"SPDXID '{sid}' is invalid: must match SPDXRef-[a-zA-Z0-9\\-.]+", loc)

        if not pkg.get("name"):
            self._error("package", "Package is missing 'name'", loc)

        if not pkg.get("versionInfo"):
            self._error("package", "Package is missing 'versionInfo'", loc)

        # SPDX 2.3 requires every package to declare filesAnalyzed and
        # supplier; the *value* used for either is a policy decision left
        # to an overlay validator, not enforced here.
        if pkg.get("filesAnalyzed") is None:
            self._warn("package", "Package missing 'filesAnalyzed'", loc)
        if pkg.get("supplier") is None:
            self._warn("package", "Package missing 'supplier'", loc)

        self._check_package_license(pkg, license_refs)
        self._check_originator(pkg)
        self._check_download_location(pkg)
        self._check_homepage(pkg)
        self._check_external_refs(pkg)

    def _check_package_license(self, pkg: dict, license_refs: set):
        loc = self._pkg_loc(pkg)

        ld = pkg.get("licenseDeclared")
        lc = pkg.get("licenseConcluded")

        if not ld:
            self._error("license", "Package missing required 'licenseDeclared'", loc)
        else:
            self._validate_license_expression(ld, "licenseDeclared", license_refs, loc)

        if not lc:
            self._error("license", "Package missing required 'licenseConcluded'", loc)
        else:
            self._validate_license_expression(lc, "licenseConcluded", license_refs, loc)

    def _validate_license_expression(self, expr: str, field: str, license_refs: set, loc: str):
        """Basic validation of an SPDX license expression."""
        if expr in ("NOASSERTION", "NONE"):
            return

        clean = expr.replace("(", " ").replace(")", " ")
        tokens = re.split(r'\s+(?:AND|OR|WITH)\s+', clean, flags=re.IGNORECASE)
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            if token.startswith("LicenseRef-"):
                if token not in license_refs:
                    self._error("license",
                                f"{field}: LicenseRef '{token}' not defined in hasExtractedLicensingInfos",
                                loc)
            elif token not in self.KNOWN_SPDX_LICENSES:
                self._warn("license",
                           f"{field}: License identifier '{token}' is not in the known SPDX license list subset; verify at https://spdx.org/licenses/",
                           loc)

    def _check_originator(self, pkg: dict):
        loc = self._pkg_loc(pkg)
        originator = pkg.get("originator")
        if originator is None:
            self._info("package", "Package has no 'originator' field (optional)", loc)
            return

        if originator not in ("NOASSERTION",) and not (
            originator.startswith("Organization:") or originator.startswith("Person:")
        ):
            self._error("package",
                        f"originator must be 'NOASSERTION', 'Organization: <name>', or 'Person: <name>', got '{originator}'",
                        loc)

    def _check_download_location(self, pkg: dict):
        loc = self._pkg_loc(pkg)
        dl = pkg.get("downloadLocation")
        if dl is None:
            self._warn("package", "Package missing 'downloadLocation' (required by SPDX spec)", loc)
            return

        if dl in ("NONE", "NOASSERTION"):
            return

        if not (dl.startswith("http://") or dl.startswith("https://") or
                dl.startswith("git+") or dl.startswith("svn+") or
                dl.startswith("hg+") or dl.startswith("bzr+")):
            self._warn("package",
                       f"downloadLocation '{dl}' does not appear to be a valid URL or VCS locator",
                       loc)

    def _check_homepage(self, pkg: dict):
        loc = self._pkg_loc(pkg)
        hp = pkg.get("homepage")
        if hp is None or hp == "NOASSERTION":
            return
        if not (hp.startswith("http://") or hp.startswith("https://")):
            self._warn("package", f"homepage '{hp}' does not appear to be a valid URL", loc)

    def _check_external_refs(self, pkg: dict):
        loc = self._pkg_loc(pkg)
        ext_refs = pkg.get("externalRefs")
        if ext_refs is None:
            return
        if not isinstance(ext_refs, list):
            self._error("package", "'externalRefs' must be an array", loc)
            return

        for i, ref in enumerate(ext_refs):
            ref_loc = f"{loc}/externalRefs[{i}]"
            cat = ref.get("referenceCategory", "")
            ref_type = ref.get("referenceType", "")
            ref_locator = ref.get("referenceLocator", "")

            if not cat:
                self._error("package", "externalRef missing 'referenceCategory'", ref_loc)
            if not ref_type:
                self._error("package", "externalRef missing 'referenceType'", ref_loc)
            if not ref_locator:
                self._error("package", "externalRef missing 'referenceLocator'", ref_loc)

            if ref_type == "cpe23Type":
                if cat != "SECURITY":
                    self._warn("package", f"CPE externalRef should have referenceCategory 'SECURITY', got '{cat}'", ref_loc)
                if not self.CPE23_RE.match(ref_locator):
                    self._warn("package",
                               f"CPE locator '{ref_locator}' does not look like a valid CPE 2.3 identifier (expected cpe:2.3:...)",
                               ref_loc)
            elif ref_type == "cpe22Type":
                self._warn("package", "CPE 2.2 format found; CPE 2.3 is preferred for forward-compatibility", ref_loc)

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    def _check_relationships(self, data: dict, packages: list):
        rels = data.get("relationships", [])
        if not isinstance(rels, list):
            self._error("relationship", "'relationships' must be a JSON array")
            return

        known_ids = {"SPDXRef-DOCUMENT"}
        for pkg in packages:
            sid = pkg.get("SPDXID")
            if sid:
                known_ids.add(sid)

        describes_found = any(
            rel.get("spdxElementId") == "SPDXRef-DOCUMENT" and rel.get("relationshipType") == "DESCRIBES"
            for rel in rels
        )
        if not describes_found:
            self._warn("relationship",
                       "No DESCRIBES relationship found from SPDXRef-DOCUMENT to a top-level package")

        for i, rel in enumerate(rels):
            rel_loc = f"relationships[{i}]"
            elem = rel.get("spdxElementId", "")
            rel_type = rel.get("relationshipType", "")
            related = rel.get("relatedSpdxElement", "")

            if not elem:
                self._error("relationship", "Relationship missing 'spdxElementId'", rel_loc)
            if not rel_type:
                self._error("relationship", "Relationship missing 'relationshipType'", rel_loc)
            if not related:
                self._error("relationship", "Relationship missing 'relatedSpdxElement'", rel_loc)

            if rel_type and rel_type not in self.KNOWN_RELATIONSHIP_TYPES:
                self._warn("relationship", f"Relationship type '{rel_type}' is not a recognized SPDX 2.3 type", rel_loc)

            for sid in (elem, related):
                if sid and ":" not in sid and sid not in known_ids:
                    self._warn("relationship",
                               f"Relationship references unknown SPDXID '{sid}'; "
                               "verify it is defined elsewhere in the document",
                               rel_loc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generic SPDX 2.3 SBOM Validator"
    )
    parser.add_argument("sbom_file", help="Path to the SBOM JSON file to validate")
    parser.add_argument("--strict", action="store_true",
                        help="Treat SHOULD/RECOMMENDED violations as errors")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    try:
        with open(args.sbom_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: File not found: {args.sbom_file}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in '{args.sbom_file}': {e}", file=sys.stderr)
        sys.exit(2)

    validator = SPDX23Validator(strict=args.strict)
    issues = validator.validate(data)

    errors   = [i for i in issues if i.severity == Severity.ERROR]
    warnings = [i for i in issues if i.severity == Severity.WARNING]
    infos    = [i for i in issues if i.severity == Severity.INFO]

    if args.json:
        result = {
            "file": args.sbom_file,
            "summary": {
                "errors": len(errors),
                "warnings": len(warnings),
                "infos": len(infos),
                "pass": len(errors) == 0,
            },
            "issues": [
                {
                    "severity": i.severity.value,
                    "section": i.section,
                    "message": i.message,
                    "location": i.location,
                }
                for i in issues
            ],
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"SPDX 2.3 Validator - {args.sbom_file}")
        print("=" * 70)

        if not issues:
            print("PASS - No issues found.")
        else:
            for sev, group, label in [
                (Severity.ERROR,   errors,   "ERRORS"),
                (Severity.WARNING, warnings, "WARNINGS"),
                (Severity.INFO,    infos,    "INFO"),
            ]:
                if group:
                    print(f"\n--- {label} ({len(group)}) ---")
                    for issue in group:
                        print(str(issue))

        print("\n" + "=" * 70)
        print(f"SUMMARY: {len(errors)} error(s), {len(warnings)} warning(s), {len(infos)} info(s)")
        print("RESULT: FAIL" if errors else "RESULT: PASS")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
