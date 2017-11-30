"""
Magic module that maps its submodules to Quilt tables.

Submodules have the following format: quilt.data.$user.$package.$table

E.g.:
  import quilt.data.$user.$package as $package
  print $package.$table
or
  from quilt.data.$user.$package import $table
  print $table

The corresponding data is looked up in `quilt_modules/$user/$package.json`
in ancestors of the current directory.
"""

import imp
import os.path
import sys

from six import iteritems

from .nodes import DataNode, GroupNode, PackageNode
from .tools import core
from .tools.store import PackageStore


__path__ = []  # Required for submodules to work


import importlib
def quilt_patch_open(module_name, func_name):
    """monkeypatch an open-like function (that takes a filename as the first argument)
    to rewrite that filename to the equivalent in Quilt's objs/ directory.  This is
    beneficial for taking advantage of Quilt (file dedup, indexing, reproducibility,
    versioning, etc) without needing to rewrite code that wants to read its data from
    files."""
    try:
        from unittest.mock import patch   # Python3
    except:
        from mock import patch  # Python2
        if module_name == 'builtins':
            module_name = '__builtin__'

    module = importlib.import_module(module_name)
    original_func = getattr(module, func_name)
    patcher = None
    def patch_func(filename, *args, **kwargs):
        patcher.stop()
        # TODO: add logic to redirect to quilt_packages/objs/...
        # TODO: detect quilt's own open() calls and don't redirect
        try:
            print("quilt_patch_open() called with: {}".format(filename))
            res = getattr(module, func_name)(filename, *args, **kwargs)
        finally:
            patcher.start()
        return res
    patcher = patch(module_name+'.'+func_name, patch_func)
    patcher.start()

class FakeLoader(object):
    """
    Fake module loader used to create intermediate user and package modules.
    """
    def __init__(self, path):
        self._path = path

    def load_module(self, fullname):
        """
        Returns an empty module.
        """
        mod = sys.modules.setdefault(fullname, imp.new_module(fullname))
        mod.__file__ = self._path
        mod.__loader__ = self
        mod.__path__ = []
        mod.__package__ = fullname
        return mod


def _from_core_node(package, core_node):
    if isinstance(core_node, core.TableNode) or isinstance(core_node, core.FileNode):
        node = DataNode(package, core_node)
    else:
        if isinstance(core_node, core.RootNode):
            node = PackageNode(package, core_node)
        elif isinstance(core_node, core.GroupNode):
            node = GroupNode(package, core_node)
        else:
            assert "Unexpected node: %r" % core_node

        for name, core_child in iteritems(core_node.children):
            child = _from_core_node(package, core_child)
            setattr(node, name, child)

    return node


class PackageLoader(object):
    """
    Module loader for Quilt tables.
    """
    def __init__(self, path, package):
        self._path = path
        self._package = package

    def load_module(self, fullname):
        """
        Returns an object that lazily looks up tables and groups.
        """
        mod = sys.modules.get(fullname)
        if mod is not None:
            return mod

        # We're creating an object rather than a module. It's a hack, but it's approved by Guido:
        # https://mail.python.org/pipermail/python-ideas/2012-May/014969.html

        mod = _from_core_node(self._package, self._package.get_contents())
        sys.modules[fullname] = mod
        return mod


class ModuleFinder(object):
    """
    Looks up submodules.
    """
    @staticmethod
    def find_module(fullname, path=None):
        """
        Looks up the table based on the module path.
        """
        if not fullname.startswith(__name__ + '.'):
            # Not a quilt submodule.
            return None

        submodule = fullname[len(__name__) + 1:]
        parts = submodule.split('.')

        # TODO/HACK: replace "from quilt.data.vfs__foo" with "from quilt.vfs.foo"
        if parts[0].startswith("vfs__"):
            parts[0] = parts[0][len("vfs__"):]
            quilt_patch_open('builtins', 'open')
            quilt_patch_open('h5py', 'File') # for keras/tensorflow
            quilt_patch_open('bz2', 'BZ2File')
            quilt_patch_open('gzip', 'GzipFile')
        
        if len(parts) == 1:
            for store_dir in PackageStore.find_store_dirs():
                store = PackageStore(store_dir)
                # find contents
                file_path = store.user_path(parts[0])
                if os.path.isdir(file_path):
                    return FakeLoader(file_path)
        elif len(parts) == 2:
            user, package = parts
            pkgobj = PackageStore.find_package(user, package)
            if pkgobj:
                file_path = pkgobj.get_path()
                return PackageLoader(file_path, pkgobj)

        return None

sys.meta_path.append(ModuleFinder)
