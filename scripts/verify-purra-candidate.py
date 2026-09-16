"""Verify archived wheels and, optionally, install their exact hashed URLs.

Run with the intended candidate Python from any directory. The remaining
requirements are installed in the same invocation; old constraints cannot win.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'backend/purra-candidate.json').read_text())
parser = argparse.ArgumentParser()
parser.add_argument('--install', action='store_true')
args = parser.parse_args()
requirements = []
for item in manifest['artifacts']:
    wheel = root / 'backend/vendor/purra-1.0.1' / item['file']
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual != item['sha256']:
        raise SystemExit(f'Candidate hash mismatch: {wheel.name}')
    requirements.append(wheel.as_uri() + '#sha256=' + actual)
    print(f'{wheel.name} sha256={actual}')
if args.install:
    wheel_urls = tuple(requirements)
    requirements.append('-r ' + str(root / 'backend/requirements-runtime.txt'))
    with tempfile.TemporaryDirectory(prefix='purrtypos-candidate-') as temp:
        file = Path(temp) / 'requirements.txt'
        file.write_text('\n'.join(requirements) + '\n')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(file)], cwd=root, check=True)
    # Same-version local rebuilds must replace installed bytes, not just satisfy
    # the version constraint. Dependencies were resolved together above.
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps',
                    '--force-reinstall', *wheel_urls], cwd=root, check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
