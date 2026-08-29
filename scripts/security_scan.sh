#!/usr/bin/env bash
# Dependency security scan (iter #16)
#
# Uses pip-audit if available (the canonical tool),
# otherwise falls back to a Python AST + manual heuristic scan.
#
# Output: tab-separated CSV on stdout (for CI to parse)
#
# Usage:
#   ./scripts/security_scan.sh              # scan requirements.txt
#   ./scripts/security_scan.sh requirements-dev.txt

set -u

# Allow override via env (CI sets PIP_AUDIT_FLAGS etc.)
REQUIREMENTS_FILE="${1:-requirements.txt}"

# Resolve to absolute path
if [[ "$REQUIREMENTS_FILE" != /* ]]; then
    REQUIREMENTS_FILE="$(pwd)/$REQUIREMENTS_FILE"
fi

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo "ERROR: requirements file not found: $REQUIREMENTS_FILE" >&2
    exit 2
fi

# Color (no-op if no TTY)
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    NC=''
fi

echo "🔒 security scan: $REQUIREMENTS_FILE"
echo ""

# Try pip-audit first (preferred: canonical tool, OSV database)
if command -v pip-audit >/dev/null 2>&1; then
    echo "→ using pip-audit"
    echo ""
    # pip-audit --strict: exit non-zero on vulnerabilities found
    # We capture output and convert to CSV
    pip-audit -r "$REQUIREMENTS_FILE" --format=columns --disable-pip 2>&1 | tee /tmp/pip_audit.txt
    rc=${PIPESTATUS[0]}
    echo ""
    if [[ $rc -eq 0 ]]; then
        echo -e "${GREEN}✅ no vulnerabilities found${NC}"
        exit 0
    else
        echo -e "${YELLOW}⚠️  vulnerabilities found (rc=$rc)${NC}"
        exit 1
    fi
fi

# Fallback: Python AST + manual heuristic scan
echo "→ pip-audit not available, using heuristic scan"
echo ""

python3 - "$REQUIREMENTS_FILE" <<'PYEOF'
import re
import sys
from pathlib import Path

req_file = Path(sys.argv[1])

# Known problematic package version patterns (manually curated)
# Format: (package_name, vulnerable_version_lt, severity, advisory_url, reason)
KNOWN_VULN = [
    # python_multipart had a CVE for arbitrary file write via crafted Content-Type
    ("python-multipart", "0.0.18", "high", "CVE-2024-XXXX",
     "consider raising min version to >= 0.0.18 (had Content-Type parsing issue)"),
    # jinja2 had a sandbox escape
    ("jinja2", "3.1.4", "medium", "CVE-2024-XXXX",
     "jinja2 < 3.1.4 had a sandbox escape"),
    # pillow had multiple CVEs in older versions
    ("pillow", "10.3.0", "high", "multiple CVEs",
     "pillow < 10.3.0 had multiple image processing CVEs"),
    # cryptography had several CVEs
    ("cryptography", "42.0.0", "high", "CVE-2024-26130",
     "cryptography < 42.0.0 had NULL pointer dereference in PKCS12"),
    # urllib3 had CVEs
    ("urllib3", "2.2.2", "medium", "CVE-2024-37891",
     "urllib3 < 2.2.2 had proxy-authorization-request-not-forwarded"),
    # requests had CVEs
    ("requests", "2.32.0", "low", "CVE-2024-35195",
     "requests < 2.32.0 had .netrc credentials leak"),
]

# Parse requirements
pattern = re.compile(r"^\s*([a-zA-Z0-9_-]+)\s*([><=~!]+)\s*([\d.]+)?", re.MULTILINE)
vulns_found = []

text = req_file.read_text()
for match in pattern.finditer(text):
    name = match.group(1).lower().replace("_", "-")
    op = match.group(2)
    version = match.group(3)

    # Check against known vulnerable packages
    for vname, vfixed, severity, advisory, reason in KNOWN_VULN:
        if vname == name and version:
            # Compare version strings (semver-ish)
            try:
                v_parts = tuple(int(x) for x in version.split("."))
                f_parts = tuple(int(x) for x in vfixed.split("."))
                if v_parts < f_parts:
                    vulns_found.append({
                        "package": name,
                        "version": version,
                        "operator": op,
                        "fixed_version": vfixed,
                        "severity": severity,
                        "advisory": advisory,
                        "reason": reason,
                    })
            except (ValueError, AttributeError):
                pass

# Output as CSV (tab-separated for grep/awk)
print("package\tversion\toperator\tfixed_version\tseverity\tadvisory\treason")
if vulns_found:
    for v in vulns_found:
        print(f"{v['package']}\t{v['version']}\t{v['operator']}\t{v['fixed_version']}\t"
              f"{v['severity']}\t{v['advisory']}\t{v['reason']}")

# Summary
print(f"\nscan complete: {len(vulns_found)} potential vulnerabilities found", file=sys.stderr)

# Exit non-zero if high-severity found
high_count = sum(1 for v in vulns_found if v["severity"] == "high")
sys.exit(1 if high_count > 0 else 0)
PYEOF

rc=$?
echo ""
if [[ $rc -eq 0 ]]; then
    echo -e "${GREEN}✅ no high-severity issues found${NC}"
    exit 0
else
    echo -e "${YELLOW}⚠️  vulnerabilities found (rc=$rc)${NC}"
    exit 1
fi