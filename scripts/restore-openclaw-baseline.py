#!/usr/bin/env python3
"""Restore the exact audited Jarvis patches, without restarting any service.
Default is read-only. Only accepts pristine or already-restored files of a
release that has a patch set under patches/openclaw-<version>/.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openclaw-root', type=Path, required=True,
                        help='Package directory containing package.json and dist/')
    parser.add_argument('--apply', action='store_true', help='Write validated patches; never restart')
    args = parser.parse_args()
    root = args.openclaw_root.resolve()
    patches_root = Path(__file__).resolve().parents[1] / 'patches'
    version = json.loads((root / 'package.json').read_text())['version']
    patches = patches_root / f'openclaw-{version}'
    if not (patches / 'manifest.json').is_file():
        available = sorted(p.name.replace('openclaw-', '') for p in patches_root.glob('openclaw-*') if (p / 'manifest.json').is_file())
        raise SystemExit(
            f'No patch set for OpenClaw {version}. Available: {", ".join(available) or "none"}. '
            'Build one under patches/openclaw-<version>/ before applying.'
        )
    manifest = json.loads((patches / 'manifest.json').read_text())
    if version != manifest['version']:
        raise SystemExit('Manifest version does not match the selected patch set.')
    pending = []
    # Validate ALL originals and all results before touching the installation.
    with tempfile.TemporaryDirectory(prefix='jarvis-patch-check-') as tmp:
        stage = Path(tmp)
        for item in manifest['files']:
            source = root / item['path']
            actual = digest(source)
            if actual == item['after']:
                print('EXACT:', item['path'])
                continue
            if actual != item['before']:
                raise SystemExit('Unknown/partially patched file; no files written: ' + str(source))
            target = stage / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            subprocess.run(['git', 'apply', '--check', str(patches / item['patch'])], cwd=stage, check=True)
            subprocess.run(['git', 'apply', str(patches / item['patch'])], cwd=stage, check=True)
            if digest(target) != item['after']:
                raise SystemExit('Patched result does not match recorded live hash')
            subprocess.run(['node', '--check', str(target)], check=True)
            pending.append((source, target, item))
            print('REPRODUCIBLE:', item['path'])
        if args.apply:
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            for source, target, item in pending:
                if digest(source) != item['before']:
                    raise SystemExit('Installation changed during validation; stopped')
                shutil.copy2(source, source.with_name(source.name + '.bak-jarvis-' + stamp))
                shutil.copyfile(target, source)
                if digest(source) != item['after']:
                    raise SystemExit('Post-write verification failed')
                print('RESTORED:', item['path'])
    print('No service was started or restarted.')
    if not args.apply:
        print('Read-only check. Use --apply explicitly to write the baseline.')


if __name__ == '__main__':
    main()
