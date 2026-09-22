from pathlib import Path
import ast
import os
import subprocess
import sys
import tempfile
root = Path(__file__).resolve().parent
sources = list(root.glob('*.py'))
for path in sources:
    ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
with tempfile.TemporaryDirectory(prefix='syllabus-cli-') as temp:
    env = os.environ.copy()
    env['SYLLABUS_DB_PATH'] = str(Path(temp) / 'cli.db')
    env['SYLLABUS_EXPORT_DIR'] = str(Path(temp) / 'exports')
    for job in ('init', 'extract', 'report', 'site', 'changes'):
        res = subprocess.run([sys.executable, '-X', 'utf8', 'run.py', job, '--year', '2027'], cwd=root, env=env, capture_output=True, encoding='utf-8')
        if res.returncode:
            raise RuntimeError(res.stdout + res.stderr)
        print(f'CLI {job}: OK')
    res = subprocess.run([sys.executable, '-X', 'utf8', 'run.py', 'summarize', '--year', '2027', '--dry-run'], cwd=root, env=env, capture_output=True, encoding='utf-8')
    assert res.returncode == 0, res.stderr
    print('CLI summarize --dry-run (0 targets): OK, no API needed')
    for args in (['daily','--workers','0'], ['daily','--set','typo'], ['changes','--days','-1']):
        res = subprocess.run([sys.executable, '-X', 'utf8', 'run.py', *args], cwd=root, env=env, capture_output=True)
        assert res.returncode == 2
    print('CLI invalid arguments: rejected before work')
print(f'Syntax: {len(sources)} source files OK')
