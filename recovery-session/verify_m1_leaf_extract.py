"""Verify the actual main-plus-leaf-patch tree without changing a checkout."""

import importlib.abc
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path('/private/tmp/kodezart-v03-recovery-integration')
OUT = Path('/private/tmp/kodezart-recovery-session')
BASE = '4661a24b599d75503a997f3ce122f3ad2da77048'
DONOR = 'd2c6fceab762191d4e40b23c8cd349ef476e4b12'
SOURCE = ['src/kodezart/types/domain/scope_address.py', 'src/kodezart/types/domain/scope.py', 'src/kodezart/types/domain/surface.py', 'src/kodezart/domain/surface_lease.py']
TESTS = ['tests/domain/test_scope_ref.py', 'tests/types/test_writable_surface.py', 'tests/domain/test_surface_lease.py']


def git(*args, env=None, data=None):
    return subprocess.check_output(['git', *args], cwd=ROOT, env=env, input=data)


patch = git('diff', '--binary', '--full-index', BASE, DONOR, '--', *SOURCE, *TESTS)
(OUT/'extraction-m1-first-slice.patch').write_bytes(patch)
env = {**os.environ, 'GIT_INDEX_FILE': str(OUT/'extraction-m1-scratch.index')}
git('read-tree', BASE, env=env)
git('apply', '--cached', '--check', '-', env=env, data=patch)
git('apply', '--cached', '-', env=env, data=patch)
TREE = git('write-tree', env=env).decode().strip()
print('BASE', BASE, 'DONOR', DONOR, 'EXTRACTED_TREE', TREE, flush=True)


class PinnedLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Load every kodezart module only from the actual extracted Git tree."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith('kodezart'):
            return None
        stem = 'src/'+fullname.replace('.', '/')
        for file, package in ((stem+'/__init__.py', True), (stem+'.py', False)):
            try:
                content = git('show', f'{TREE}:{file}')
            except subprocess.CalledProcessError:
                continue
            spec = importlib.util.spec_from_loader(fullname, self, is_package=package)
            spec.loader_state = (file, content)
            return spec
        raise ModuleNotFoundError(f'{fullname} is absent from extracted tree {TREE}')

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, content = module.__spec__.loader_state
        module.__file__ = str(ROOT/path)
        exec(compile(content, module.__file__, 'exec'), module.__dict__)


sys.meta_path.insert(0, PinnedLoader())
result = pytest.main(['-q', '--noconftest', '-o', 'addopts=', *[str(ROOT/p) for p in TESTS]])
print('EXTRACTED_TREE', TREE, 'PYTEST_EXIT', result, flush=True)
raise SystemExit(result)
