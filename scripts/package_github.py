"""打包可公开的源码，可选同步到 .release 下的 Git 仓库；不复制密钥和运行数据。"""
import argparse
import fnmatch
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / '.release'
ROOT_FILES = ('.gitignore', '.env.example', 'README.md', 'requirements.txt',
              'requirements.lock.txt', 'pytest.ini', 'start.ps1', 'start-editor.ps1')
FOLDERS = ('app', 'static', 'tests', 'scripts', 'online-demo')
BLOCKED_DIRS = {'.git', '.venv', '__pycache__', 'node_modules', 'dist', 'out',
                '.next', '.vinext', '.wrangler', '.sites-runtime', '.agents', '.codex',
                '.workbuddy', '.superpowers', '.pytest_cache', 'outputs', 'work',
                'coverage', 'data', 'artifacts', '.release'}


def allowed(path):
    if set(path.parts) & BLOCKED_DIRS:
        return False
    name = path.name.lower()
    if name == '.env.example' or name == 'model.env.example':
        return True
    return not any(fnmatch.fnmatch(name, pattern) for pattern in
                   ('.env*', '.dev.vars*', '*.env.local', '*.log', '*.pyc',
                    '*.tsbuildinfo', '*.pem', '*.key', '*.pfx', '*.sqlite*', '*.db',
                    '*.genie', 'next-env.d.ts'))


def secret_values():
    # 仅用于防止把本机已知密钥意外写进源码；从不输出其值。
    from dotenv import dotenv_values
    values = []
    for env in (ROOT / '.env', ROOT / 'online-demo/model.env.local'):
        if env.exists():
            for key, value in dotenv_values(env).items():
                if value and len(value) >= 8 and ('SECRET' in key or 'API_KEY' in key):
                    values.append(value.encode())
    return values


def collect():
    files = {name: (ROOT / name).read_bytes() for name in ROOT_FILES if (ROOT / name).exists()}
    files['docs/TESTING.md'] = (ROOT / 'docs/TESTING.md').read_bytes()
    for folder in FOLDERS:
        pending = [ROOT / folder]
        while pending:
            directory = pending.pop()
            for path in directory.iterdir():
                relative = path.relative_to(ROOT)
                if path.is_symlink() or not allowed(relative):
                    continue
                if path.is_dir():
                    pending.append(path)
                elif '.openai' not in relative.parts:
                    files[relative.as_posix()] = path.read_bytes()
    # 保留构建需要的空白配置，不绑定原作者的 Sites 项目。
    files['online-demo/.openai/hosting.json'] = b'{"d1":null,"r2":null}\n'
    secrets = secret_values()
    for name, content in files.items():
        if any(value in content for value in secrets):
            raise ValueError(f'检测到本机密钥，已停止打包：{name}')
        if name.endswith('.npmrc') and (b'_authToken' in content or b'_password' in content):
            raise ValueError('npm配置包含认证信息，已停止打包')
        if len(content) > 50_000_000:
            raise ValueError(f'文件过大，请检查：{name}')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copy-to', type=Path, help='可选：同步到项目 .release 内的仓库副本，不删除已有文件')
    args = parser.parse_args()
    destination = args.copy_to.resolve() if args.copy_to else None
    if destination and (not destination.is_relative_to(RELEASE.resolve()) or destination == RELEASE.resolve()):
        parser.error('--copy-to 必须位于本项目 .release 的子目录中')
    files = collect()
    RELEASE.mkdir(exist_ok=True)
    archive = RELEASE / 'AI-class-representative-source.zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED) as output:
        for name, content in sorted(files.items()):
            output.writestr(name, content)
            if destination:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
    print(json.dumps({'files': len(files), 'zip_bytes': archive.stat().st_size,
                      'archive': str(archive), 'known_secret_scan': 'passed'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
