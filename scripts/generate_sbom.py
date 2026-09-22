#!/usr/bin/env python3
"""Generate CycloneDX Software Bill of Materials."""
import subprocess, sys, json
from pathlib import Path

def generate_sbom():
    print("Generating SBOM...")
    # Use pip to list packages
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=json"],
        capture_output=True, text=True
    )
    packages = json.loads(result.stdout)
    
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "version": 1,
        "components": [
            {
                "type": "library",
                "name": pkg["name"],
                "version": pkg["version"],
                "purl": f"pkg:pypi/{pkg['name']}@{pkg['version']}"
            }
            for pkg in packages
        ]
    }
    
    output_path = Path(__file__).parent.parent / "sbom.json"
    output_path.write_text(json.dumps(sbom, indent=2))
    print(f"SBOM written to {output_path} ({len(packages)} components)")

if __name__ == "__main__":
    generate_sbom()
