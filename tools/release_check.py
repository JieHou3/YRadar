"""Audit and optionally ZIP the explicit public file set, never the parent project."""
from pathlib import Path
import argparse
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    'README.md', 'README.zh-CN.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
    'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md', '.env.example', '.gitignore',
    'requirements.txt', 'config.py', 'database.py', 'monitor.py', 'notifier.py',
    'geo.py', 'city_coordinates.py', 'vin_hw.py', 'vehicle_tiers.py',
    'warmup_tesla.py', 'warmup.bat', 'start.bat', 'start.sh', 'demo.py',
}
DIRECTORIES = {'docs': {'.md'}, 'tests': {'.py'}, 'fetchers': {'.py'},
               'tools': {'.py'}, '.github': {'.yml', '.yaml', '.md'},
               'assets': {'.svg'}}
PATTERNS = {
    'Telegram token': re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'Discord webhook credential': re.compile(r'https://(?:discord(?:app)?\.com)/api/webhooks/\d+/[A-Za-z0-9_-]+'),
    'Private Windows home path': re.compile(r'[A-Za-z]:[\\/]Users[\\/](?!Public\b|Example\b)[^\s"<>]+'),
    'Private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}


def public_files():
    files = []
    for path in ROOT.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if any(part in {'.git', '__pycache__', '.venv', 'venv', 'dist', '.tools-tmp'} for part in relative.parts):
            continue
        if len(relative.parts) == 1 and path.name in ROOT_FILES:
            files.append(path)
        elif relative.parts[0] in DIRECTORIES and path.suffix in DIRECTORIES[relative.parts[0]]:
            files.append(path)
    return sorted(files)


def audit(files):
    errors = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_text(encoding='utf-8-sig')
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                errors.append(f'{relative}: {label}')
        if path.suffix in {'.py', '.bat', '.sh'} and re.search(r'[\u4e00-\u9fff]', content):
            errors.append(f'{relative}: non-English program text')
        if path.suffix == '.py' and re.search(r'(?m)^\s*(?:from|import)\s+(?:fetchers\.)?(?:truecar_fetcher|matcher)\b', content):
            errors.append(f'{relative}: private provider dependency')
        if path.name == '.env.example':
            for line in content.splitlines():
                key, sep, value = line.partition('=')
                if sep and any(word in key for word in ('TOKEN', 'CHAT_ID', 'WEBHOOK')) and value.strip():
                    errors.append(f'{relative}: credential placeholder must be empty')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', action='store_true', help='Write dist/YRadar-source.zip from the audited file list')
    parser.add_argument('--list', action='store_true', help='Print the audited relative paths')
    args = parser.parse_args()
    files = public_files()
    errors = audit(files)
    if errors:
        for error in errors:
            print('FAIL:', error)
        return 1
    print(f'Public file audit passed: {len(files)} files. Manual review of examples and images is still required.')
    if args.list:
        for path in files:
            print(path.relative_to(ROOT).as_posix())
    if args.zip:
        output = ROOT / 'dist/YRadar-source.zip'
        output.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, 'YRadar/' + path.relative_to(ROOT).as_posix())
        print(f'Created {output.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
