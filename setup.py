"""Custom build_py that purges the build tree before copying.

setuptools copies package data into build/lib without ever removing files that have since
been deleted from the source tree, so build/lib accumulates the union of every previous
build. Because package-data patterns are resolved against that directory, anything left
there is treated as current package data and ends up in the wheel. The result is that
wheel contents depend on build history rather than on tracked source.

Removing the destination for our own package before the copy makes the build a pure
function of the source tree. Stale files cannot survive it, and the purge is cheap because
build_py recopies every file it owns in the same run.
"""

import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_PACKAGE = "face_id_verification"


class build_py(_build_py):
    def run(self):
        self._purge_stale_output()
        super().run()

    def _purge_stale_output(self):
        destination = os.path.join(self.build_lib, _PACKAGE)
        if os.path.isdir(destination):
            shutil.rmtree(destination)


setup(cmdclass={"build_py": build_py})
