import os

from ._version import get_versions as _get_version

def get_version():
    """
    Uses the versioneer code to generate a version string based on the last git tag and subsequent commits afterwards.
    For packaging reasons, this version string will also be saved in a file called __version__. This ensure, that the
    frozen version can also be used in the executables produced by pyinstaller
    :return:
    """
    version = _get_version()['version']
    dir_path = os.path.dirname(os.path.realpath(__file__))
    ver_path = os.path.abspath(os.path.join(dir_path, '__version__'))
    
    if version == "0+unknown":
        if os.path.isfile(ver_path):
            try:
                
                with open(ver_path, 'r') as fp:
                    version = fp.readline()
            except ImportError:
                pass
    else:
        # trying to freeze the current version into a python file which gets loaded in case it is not accessible
        with open(ver_path, 'w+') as fp:
            fp.write(version)

    return version