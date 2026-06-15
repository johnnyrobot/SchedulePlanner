#!/usr/bin/env python3
"""E16 — CycloneDX SBOM + no-copyleft license gate.

Generates a CycloneDX 1.5 SBOM for the SHIPPED dependency set (requirements.lock,
NOT the dev environment) and FAILS if any bundled component carries strong copyleft
(GPL / AGPL), operationalizing the project's doctrine 5 (no copyleft in the bundle).

Honest by construction:
  * The component set is the lock (the actual bundle), resolved package-by-package
    from requirements.lock — build-only tools (pyinstaller is GPL, but its output
    is not, and it is NOT in the lock) never enter the SBOM.
  * The bundled NON-PyPI runtimes are carried as EXPLICITLY-NOTED external
    components (no silent drops, even in the SBOM): the Temurin JRE that runs the
    OpenDataLoader jar, and the CPython runtime embedded by PyInstaller.
  * Strong copyleft (GPL/AGPL) trips the gate; weak copyleft (LGPL — dynamic link)
    does not. The Temurin JRE is GPL-2.0-WITH-Classpath-Exception, distributed
    unmodified and run as a SEPARATE subprocess, so it is noted in the SBOM but
    gate-EXEMPT with a documented reason (the Classpath Exception permits this);
    the gate never silently passes it.

Usage:
    python3 scripts/generate_sbom.py [--lock requirements.lock] [--out sbom.cyclonedx.json]
Exit 0 when clean, 2 when a non-exempt copyleft component is found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

try:
    import importlib.metadata as _md
except ImportError:  # pragma: no cover - py<3.8 not supported
    _md = None

SPEC_VERSION = "1.5"

# Bundled runtimes that are NOT PyPI packages. Carried in the SBOM so nothing is
# silently dropped; ``gate_exempt`` (with a reason) keeps the GPL-with-Classpath-
# Exception JRE — a separately-distributed subprocess runtime — from tripping the
# gate, while still disclosing its real license.
BUNDLED_EXTERNALS = [
    {
        "name": "Eclipse Temurin JRE",
        "version": "17",
        "license": "GPL-2.0-with-classpath-exception",
        "purl": "pkg:generic/eclipse-temurin@17",
        "note": ("bundled JVM that runs the OpenDataLoader PDF jar as a SEPARATE "
                 "subprocess (scripts/fetch_jre.sh); distributed unmodified"),
        "gate_exempt": True,
        "exempt_reason": ("OpenJDK's GPL-2.0 Classpath Exception permits "
                          "distribution of the unmodified runtime alongside "
                          "independent software run on it"),
    },
    {
        "name": "CPython runtime",
        "version": sys.version.split()[0],
        "license": "PSF-2.0",
        "purl": "pkg:generic/cpython",
        "note": "Python.framework embedded by PyInstaller; PSF license (permissive)",
        "gate_exempt": False,
    },
]

# Vetted SPDX licenses for PLATFORM-CONDITIONAL lock packages that do NOT install
# on the gate runner (pywebview's Windows/.NET/Qt backends are absent on macOS CI),
# so importlib.metadata cannot read their license. Without this the gate saw
# "UNKNOWN" and silently passed them — a GPL win32-only dep would slip through.
# Each license was vetted from the package's PyPI metadata; keys are normalized
# (lower, '_'->'-'). Adding a NEW platform-conditional dep FAILS the gate until it
# is vetted in here (see unverified_components).
PLATFORM_CONDITIONAL_LICENSES = {
    "clr-loader": "MIT",          # pythonnet loader (Windows/.NET)
    "colorama": "BSD-3-Clause",   # Windows ANSI shim
    "pythonnet": "MIT",           # .NET interop backend
    "qtpy": "MIT",                # Qt abstraction backend
    "tzdata": "Apache-2.0",       # IANA tz database
}


def parse_lock(path):
    """Ordered, deduped ``[(name, version)]`` parsed from a pip/uv lock file.

    Reads only the ``name==version`` requirement lines (ignores ``--hash`` / ``#``
    continuation lines), so it works on a ``--generate-hashes`` lock."""
    seen, out = set(), []
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)", line)
        if not m:
            continue
        name = m.group(1)
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        out.append((name, m.group(2)))
    return out


def license_of(name):
    """Best-effort license string for an installed distribution: its
    License-classifier(s) preferred, else the free-text License field, else "".
    Returns "" when the package is not installed (CI installs the whole lock)."""
    if _md is None:
        return ""
    try:
        md = _md.metadata(name)
    except _md.PackageNotFoundError:
        return ""
    # Prefer the PEP 639 SPDX License-Expression (most authoritative), then the
    # License-classifier(s), then the free-text License field.
    expr = (md.get("License-Expression") or "").strip()
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip()
                   for c in (md.get_all("Classifier") or [])
                   if c.startswith("License ::")]
    if classifiers:
        return "; ".join(dict.fromkeys(classifiers))
    return (md.get("License") or "").strip().splitlines()[0] if md.get("License") else ""


def is_copyleft(license_str):
    """True iff ``license_str`` is STRONG copyleft (GPL / AGPL). LGPL (weak copyleft,
    dynamic-link) is allowed. Order matters: AGPL and LGPL are matched before the
    bare 'GPL' / 'General Public License' so they are not mis-classified."""
    u = (license_str or "").upper()
    if "AGPL" in u or "AFFERO" in u:
        return True
    if "LGPL" in u or "LESSER GENERAL PUBLIC" in u:
        return False
    if "WITH-CLASSPATH-EXCEPTION" in u or "CLASSPATH-EXCEPTION" in u:
        return False  # GPL + Classpath Exception is handled as exempt-by-license
    return "GPL" in u or "GENERAL PUBLIC LICENSE" in u


def build_components(lock_path):
    """The SBOM component list: the lock's PyPI packages (with licenses) followed by
    the explicitly-noted bundled externals. Deterministically ordered."""
    comps = []
    for name, version in parse_lock(lock_path):
        key = name.lower().replace("_", "-")
        lic = license_of(name)
        source = "installed-metadata" if lic else ""
        if not lic:
            # Not installed on this runner (platform-conditional): resolve from the
            # human-vetted map rather than letting it ride as UNKNOWN.
            vetted = PLATFORM_CONDITIONAL_LICENSES.get(key)
            if vetted:
                lic, source = vetted, "vetted-platform-conditional"
        comps.append({
            "type": "library", "name": name, "version": version,
            "license": lic or "UNKNOWN",
            "license_source": source or "unresolved",
            "purl": f"pkg:pypi/{key}@{version}",
            "gate_exempt": False,
        })
    comps.extend(dict(e) for e in BUNDLED_EXTERNALS)
    comps.sort(key=lambda c: (0 if c.get("type") == "library" and "pypi" in c.get("purl", "")
                              else 1, c["name"].lower()))
    return comps


def build_sbom(components):
    """A minimal but valid CycloneDX 1.5 JSON document from the component list."""
    out_components = []
    for c in components:
        comp = {
            "type": c.get("type", "library"),
            "name": c["name"],
            "version": c.get("version", ""),
            "purl": c.get("purl", ""),
            "licenses": [{"license": {"name": c.get("license", "UNKNOWN")}}],
        }
        props = []
        if c.get("note"):
            props.append({"name": "edgesched:bundled-external", "value": c["note"]})
        if c.get("gate_exempt"):
            props.append({"name": "edgesched:license-gate",
                          "value": f"exempt: {c.get('exempt_reason', '')}"})
        if c.get("license_source") == "vetted-platform-conditional":
            props.append({"name": "edgesched:license-source",
                          "value": "manually vetted (platform-conditional dep; not installed "
                                   "on the gate runner)"})
        if props:
            comp["properties"] = props
        out_components.append(comp)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "version": 1,
        "metadata": {
            "tools": [{"name": "edgesched-generate-sbom", "version": "1.0"}],
            "component": {"type": "application", "name": "SchedulePlanner"},
        },
        "components": out_components,
    }


def copyleft_offenders(components):
    """Non-exempt components whose license is strong copyleft — the gate failures."""
    return [c for c in components
            if not c.get("gate_exempt") and is_copyleft(c.get("license", ""))]


def unverified_components(components):
    """Non-exempt components whose license could NOT be verified (UNKNOWN).

    An unverifiable license is a gate FAILURE, not a pass: it could be copyleft.
    This closes the bypass where a platform-conditional package absent from the
    gate runner showed UNKNOWN and slipped the copyleft check. Fix by vetting the
    package's real license into PLATFORM_CONDITIONAL_LICENSES."""
    return [c for c in components
            if not c.get("gate_exempt")
            and (c.get("license", "") or "UNKNOWN").upper() == "UNKNOWN"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", default="requirements.lock")
    ap.add_argument("--out", default="sbom.cyclonedx.json")
    args = ap.parse_args(argv)

    components = build_components(args.lock)
    sbom = build_sbom(components)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(sbom, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"Wrote {args.out}: {len(components)} components "
          f"({sum(1 for c in components if c.get('gate_exempt'))} gate-exempt external).")

    offenders = copyleft_offenders(components)
    unverified = unverified_components(components)
    if offenders or unverified:
        if offenders:
            print("LICENSE GATE FAILED — strong-copyleft (GPL/AGPL) component(s) in the bundle:",
                  file=sys.stderr)
            for c in offenders:
                print(f"  - {c['name']} {c.get('version', '')}: {c.get('license')}",
                      file=sys.stderr)
        if unverified:
            print("LICENSE GATE FAILED — component(s) with an UNVERIFIABLE license (not "
                  "installed on this runner and not vetted in PLATFORM_CONDITIONAL_LICENSES); "
                  "an unknown license could be copyleft. Vet each from PyPI and add its SPDX "
                  "license to that map:", file=sys.stderr)
            for c in unverified:
                print(f"  - {c['name']} {c.get('version', '')}: license could not be resolved",
                      file=sys.stderr)
        return 2
    print("License gate PASS (no non-exempt GPL/AGPL component; all licenses verified).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
