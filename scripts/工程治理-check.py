"""工程一致性检查：本地文件、测试登记、依赖锁定、文档链接和模块分层。"""
import ast
import importlib.metadata
import json
import pathlib
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from packaging.requirements import Requirement

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import run_quality as quality


def inspect_repository():
    problems = []

    def require(condition, message):
        if not condition:
            problems.append(message)

    config = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    declared = config['project']['dependencies'] + config['project']['optional-dependencies']['dev']
    locked = [Requirement(line) for line in (ROOT / '依赖锁定-requirements.txt').read_text(encoding='utf-8').splitlines()
              if line.strip() and not line.lstrip().startswith('#')]
    for requirement in [Requirement(value) for value in declared] + locked:
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = importlib.metadata.version(requirement.name)
            require(installed in requirement.specifier, f'依赖版本不一致：{requirement.name}={installed}，要求 {requirement.specifier}')
        except importlib.metadata.PackageNotFoundError:
            problems.append(f'缺少依赖：{requirement.name}')
    for value in declared:
        requirement = Requirement(value)
        matches = [item for item in locked if item.name.lower().replace('_', '-') == requirement.name.lower().replace('_', '-')]
        require(bool(matches), f'声明的依赖未锁定：{requirement.name}')
        for item in matches:
            pins = list(item.specifier)
            require(len(pins) == 1 and pins[0].operator == '==' and pins[0].version in requirement.specifier,
                    f'锁定版本不满足声明：{requirement.name}')

    entries = []
    for group in quality.ORDER:
        scripts = quality.GROUPS[group]['scripts']
        require(len(scripts) == len(set(scripts)), f'测试分组重复：{group}')
        for script in scripts:
            filename = script.split()[0]
            require((ROOT / 'mcp' / filename).is_file(), f'测试文件缺失：{script}')
            require(bool(quality.SCOPES.get(filename)), f'测试未登记守护面：{script}')
            entries.append(filename)
    manual = {'experiments/test_time_aware_engine.py', 'experiments/test_server_evidence.py'}
    discovered = {path.relative_to(ROOT / 'mcp').as_posix() for path in (ROOT / 'mcp').rglob('test_*.py')}
    unclassified = discovered - set(entries) - manual
    require(not unclassified, f'存在未分类测试：{sorted(unclassified)}')
    require(all((ROOT / 'mcp' / path).is_file() for path in manual), '人工实验测试清单已过期')
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    for group in ('offline', 'real'):
        row = next((line for line in readme.splitlines() if f'**{group}**' in line), '')
        require(f"{len(quality.GROUPS[group]['scripts'])} 项" in row, f'根说明的 {group} 测试数已过期')

    schema = ast.parse((ROOT / 'mcp/aw_tool_schemas.py').read_text(encoding='utf-8'))
    names = [keyword.value.value for node in ast.walk(schema) if isinstance(node, ast.Call)
             for keyword in node.keywords if keyword.arg == 'name' and isinstance(keyword.value, ast.Constant)
             and str(keyword.value.value).startswith('world_')]
    require(len(names) == len(set(names)), '工具定义名称重复')
    guide = (ROOT / 'docs/项目指南与架构.md').read_text(encoding='utf-8')
    require(f'{len(names)} 个 world_* 工具' in guide, '架构指南的工具总数已过期')

    active_docs = [ROOT / 'README.md', ROOT / 'CONTRIBUTING.md', ROOT / 'mcp/README.md',
                   ROOT / 'extension/README.md', *sorted((ROOT / 'docs').glob('*.md'))]
    for path in active_docs:
        content = path.read_text(encoding='utf-8')
        for raw in re.findall(r'\]\(([^)]+)\)', content):
            target = unquote(urlsplit(raw.strip('<>')).path)
            if not target or urlsplit(raw).scheme:
                continue
            require((path.parent / target).exists(), f'文档链接失效：{path.relative_to(ROOT)} → {raw}')
        require(not re.search(r'pip install\s+mcp\b', content), f'安装说明绕过依赖锁定：{path.relative_to(ROOT)}')

    ranks = {'aw_core': 0, 'aw_runtime': 1, 'aw_status': 2, 'aw_query': 2, 'aw_timeline': 2,
             'aw_outcome': 3, 'aw_guide': 3, 'aw_taskgraph': 4, 'aw_actions': 5, 'aw_tool_schemas': 0}
    for module, rank in ranks.items():
        path = ROOT / 'mcp' / f'{module}.py'
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            dependencies = []
            if isinstance(node, ast.ImportFrom):
                dependencies = [node.module or '']
            elif isinstance(node, ast.Import):
                dependencies = [alias.name for alias in node.names]
            for dependency in dependencies:
                dependency = dependency.removeprefix('mcp.')
                require(dependency != 'server' and (dependency not in ranks or ranks[dependency] < rank),
                        f'模块反向或同层依赖：{module}:{node.lineno} → {dependency}')

    ok, detail = quality.check_all_in_one()
    require(ok, detail)
    manifest = json.loads((ROOT / 'extension/manifest.json').read_text(encoding='utf-8'))
    popup = manifest.get('action', {}).get('default_popup')
    require(not popup or (ROOT / 'extension' / popup).is_file(), '扩展弹窗文件缺失或不是本地路径')
    for group in manifest['content_scripts']:
        for filename in group.get('js', []) + group.get('css', []):
            require((ROOT / 'extension' / filename).is_file(), f'扩展声明的文件不存在：{filename}')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    runtime_paths = ('mcp/profiles/', 'mcp/screenshots/', 'mcp/memory/', 'mcp/runtime_traces/', '.venv/', '.治理证据-evidence/')
    for filename in tracked:
        require(not filename.startswith(runtime_paths) and '__pycache__/' not in filename,
                f'运行时文件被纳入版本管理：{filename}')
    return problems


if __name__ == '__main__':
    findings = inspect_repository()
    for finding in findings:
        print(f'失败：{finding}')
    print(f'工程一致性检查：{"失败" if findings else "通过"}（{len(findings)} 个问题）')
    raise SystemExit(bool(findings))
