"""Inspect native slices, deployment targets, dylib dependencies and signing."""
import json
import re
import subprocess
import sys
from pathlib import Path


def run(*args):
    return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT)


def audit(bundle):
    bundle = Path(bundle)
    native = []
    names = {p.name for p in bundle.rglob('*') if p.is_file()}
    maximum = (0,0,0)
    for path in bundle.rglob('*'):
        if not path.is_file():
            continue
        if 'Mach-O' not in run('/usr/bin/file','-b',str(path)):
            continue
        architectures = run('/usr/bin/lipo','-archs',str(path)).strip().split()
        if architectures != ['arm64']:
            raise ValueError('Non-arm64 binary: '+str(path.relative_to(bundle)))
        load = run('/usr/bin/otool','-l',str(path))
        # Extract minimum only from LC_BUILD_VERSION / LC_VERSION_MIN_MACOSX blocks.
        versions = []
        for block in load.split('Load command'):
            if 'LC_BUILD_VERSION' in block:
                versions.extend(re.findall(r'\bminos\s+(\d+\.\d+(?:\.\d+)?)',block))
            elif 'LC_VERSION_MIN_MACOSX' in block:
                versions.extend(re.findall(r'\bversion\s+(\d+\.\d+(?:\.\d+)?)',block))
        for version in versions:
            numbers = tuple(map(int,version.split('.')))
            numbers += (0,)*(3-len(numbers))
            maximum = max(maximum,numbers)
            if numbers > (15,6,0):
                raise ValueError('Deployment target exceeds macOS15.6: '+str(path.relative_to(bundle)))
        # otool -L includes LC_ID_DYLIB (the library's own install name).
        # It is not a load dependency; PyInstaller may preserve its original
        # versioned name while storing this same library under an alias.
        identities = set()
        for block in load.split('Load command'):
            if 'cmd LC_ID_DYLIB\n' in block:
                identities.update(re.findall(r'\bname\s+(.+?)\s+\(offset',block))
        dependencies = []
        for line in run('/usr/bin/otool','-L',str(path)).splitlines()[1:]:
            dependency = line.strip().split(' (')[0]
            if dependency in identities:
                continue
            if dependency.startswith('/') and not dependency.startswith(('/usr/lib/','/System/Library/')):
                raise ValueError('Unbundled absolute dylib: '+dependency)
            if dependency.startswith('@') and Path(dependency).name not in names:
                raise ValueError('Missing bundled dylib: '+dependency)
            dependencies.append(dependency)
        native.append({'path':str(path.relative_to(bundle)),'architecture':architectures,'minimum':versions,'dependencies':dependencies})
    if not native:
        raise ValueError('No Mach-O binaries in bundle')
    run('/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',str(bundle))
    return {'architecture':'arm64','declared_minimum':'15.6','highest_binary_minimum':'.'.join(map(str,maximum)),
            'apple_trust':'ad-hoc; no Developer ID or notarization','native_files':native}


if __name__ == '__main__':
    Path(sys.argv[2]).write_text(json.dumps(audit(sys.argv[1]),indent=2),encoding='utf-8')
