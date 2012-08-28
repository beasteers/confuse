"""Worry-free YAML configuration files.
"""
from __future__ import unicode_literals
import platform
import os
import pkgutil
import sys
import yaml

UNIX_DIR_VAR = 'XDG_DATA_HOME'
UNIX_DIR_FALLBACK = '~/.config'
WINDOWS_DIR_VAR = 'APPDATA'
WINDOWS_DIR_FALLBACK = '~\\AppData\\Roaming'
MAC_DIR = '~/Library/Application Support'

CONFIG_FILENAME = 'config.yaml'
DEFAULT_FILENAME = 'config_default.yaml'


# Utilities.

PY3 = sys.version_info[0] == 3
STRING = str if PY3 else unicode

def iter_first(sequence):
    """Get the first element from an iterable or raise a ValueError if
    the iterator generates no values.
    """
    it = iter(sequence)
    try:
        if PY3:
            return next(it)
        else:
            return it.next()
    except StopIteration:
        raise ValueError()


# Exceptions.

class ConfigError(Exception):
    """Base class for exceptions raised when querying a configuration.
    """

class NotFoundError(ConfigError):
    """A requested value could not be found in the configuration trees.
    """

class ConfigTypeError(ConfigError, TypeError):
    """The value in the configuration did not match the expected type.
    """

class ConfigValueError(ConfigError, ValueError):
    """The value in the configuration is illegal."""

class ConfigReadError(ConfigError):
    """A configuration file could not be read."""
    def __init__(self, filename, reason=None):
        self.filename = filename
        self.reason = reason
        message = 'file {0} could not be read'.format(filename)
        if reason:
            message += ': {0}'.format(reason)
        super(ConfigReadError, self).__init__(message)


# Views and data access logic.

class ConfigView(object):
    """A configuration "view" is a query into a program's configuration
    data. A view represents a hypothetical location in the configuration
    tree; to extract the data from the location, a client typically
    calls the ``view.get()`` method. The client can access children in
    the tree (subviews) by subscripting the parent view (i.e.,
    ``view[key]``).
    """

    name = None
    """The name of the view, depicting the path taken through the
    configuration in Python-like syntax (e.g., ``foo['bar'][42]``).
    """

    overlay = None
    """The portion of the transient overlay corresponding to this
    view.
    """

    def get_all(self):
        """Generates all available values for the view in the order of
        the configuration's sources. (Each source may have at most one
        value for each view.) If no values are available, no values are
        generated. If a type error is encountered when traversing a
        source to resolve the view, a ConfigTypeError may be raised.
        """
        raise NotImplementedError

    def get(self, typ=None):
        """Returns the canonical value for the view. This amounts to the
        first item in ``view.get_all()``. If the view cannot be
        resolved, this method raises a NotFoundError.
        """
        values = self.get_all()

        # Get the first value.
        try:
            value = iter_first(values)
        except ValueError:
            raise NotFoundError("{0} not found".format(self.name))

        # Validate/convert.
        if isinstance(typ, type):
            # Check type of value.
            if not isinstance(value, typ):
                raise ConfigTypeError(
                    "{0} must by of type {1}, not {2}".format(
                        self.name, typ, type(value)
                    )
                )

        elif typ is not None:
            # typ must a callable that takes this view and the value.
            value = typ(self, value)

        return value

    def __repr__(self):
        return '<ConfigView: %s>' % self.name

    def __getitem__(self, key):
        """Get a subview of this view."""
        return Subview(self, key)

    def __setitem__(self, key, value):
        """Set a value in the transient overlay for a certain key under
        this view.
        """
        self.overlay[key] = value

    # Magical conversions. These special methods make it possible to use
    # View objects somewhat transparently in certain circumstances. For
    # example, rather than using ``view.get(bool)``, it's possible to
    # just say ``bool(view)`` or use ``view`` in a conditional.

    def __str__(self):
        """Gets the value for this view as a byte string."""
        return str(self.get())
    
    def __unicode__(self):
        """Gets the value for this view as a unicode string. (Python 2
        only.)
        """
        return unicode(self.get())
    
    def __nonzero__(self):
        """Gets the value for this view as a boolean. (Python 2 only.)
        """
        return self.__bool__()

    def __bool__(self):
        """Gets the value for this view as a boolean. (Python 3 only.)
        """
        return bool(self.get())

    # Dictionary emulation methods.

    def keys(self):
        """Returns an iterable containing all the keys available as
        subviews of the current views. This enumerates all the keys in
        *all* dictionaries matching the current view, in contrast to
        ``dict(view).keys()``, which gets all the keys for the *first*
        dict matching the view. If the object for this view in any
        source is not a dict, then a ConfigTypeError is raised.
        """
        keys = set()
        for dic in self.get_all():
            try:
                cur_keys = dic.keys()
            except AttributeError:
                raise ConfigTypeError('%s must be a dict, not %s' %
                                      (self.name, STRING(type(dic))))
            keys.update(cur_keys)
        return keys

    def items(self):
        """Iterates over (key, subview) pairs contained in dictionaries
        from *all* sources at this view. If the object for this view in
        any source is not a dict, then a ConfigTypeError is raised.
        """
        for key in self.keys():
            yield key, self[key]

    def values(self):
        """Iterates over all the subviews contained in dictionaries from
        *all* sources at this view. If the object for this view in any
        source is not a dict, then a ConfigTypeError is raised.
        """
        for key in self.keys():
            yield self[key]

    # List/sequence emulation.

    def all_contents(self):
        """Iterates over all subviews from collections at this view from
        *all* sources. If the object for this view in any source is not
        iterable, then a ConfigTypeError is raised. This method is
        intended to be used when the view indicates a list; this method
        will concatenate the contents of the list from all sources.
        """
        for collection in self.get_all():
            try:
                it = iter(collection)
            except TypeError:
                raise ConfigTypeError('%s must be an iterable, not %s' %
                                      (self.name, STRING(type(collection))))
            for value in it:
                yield value

class RootView(ConfigView):
    """The base of a view hierarchy. This view keeps track of the
    sources that may be accessed by subviews.
    """
    def __init__(self, sources):
        """Create a configuration hierarchy for a list of sources. At
        least one source must be provided. The first source in the list
        has the highest priority.
        """
        self.sources = list(sources)
        self.overlay = {}
        self.name = 'root'

    def add(self, obj):
        """Add the object (probably a dict) as a source for
        configuration data. The object as added as the lowest-priority
        source. This can be used to dynamically extend the defaults
        (i.e., when loading a plugin that shares the main application's
        config file).
        """
        self.sources.append(obj)

    def get_all(self):
        return [self.overlay] + self.sources

class Subview(ConfigView):
    """A subview accessed via a subscript of a parent view."""
    def __init__(self, parent, key):
        """Make a subview of a parent view for a given subscript key.
        """
        self.parent = parent
        self.key = key
        self.name = '{0}[{1}]'.format(self.parent.name, repr(self.key))

    def get_all(self):
        for collection in self.parent.get_all():
            try:
                value = collection[self.key]
            except IndexError:
                # List index out of bounds.
                continue
            except KeyError:
                # Dict key does not exist.
                continue
            except TypeError:
                # Not subscriptable.
                raise ConfigTypeError("%s must be a collection, not %s" %
                                      (self.parent.name,
                                       STRING(type(collection))))
            yield value

    @property
    def overlay(self):
        parent_overlay = self.parent.overlay
        if self.key not in parent_overlay:
            parent_overlay[self.key] = {}
        return parent_overlay[self.key]


# Config file paths, including platform-specific paths and in-package
# defaults.

# Based on get_root_path from Flask by Armin Ronacher.
def _package_path(name):
    """Returns the path to the package containing the named module or
    None if the path could not be identified (e.g., if
    ``name == "__main__"``).
    """
    loader = pkgutil.get_loader(name)
    if loader is None or name == '__main__':
        return None

    if hasattr(loader, 'get_filename'):
        filepath = loader.get_filename(name)
    else:
        # Fall back to importing the specified module.
        __import__(name)
        filepath = sys.modules[name].__file__

    return os.path.dirname(os.path.abspath(filepath))

def config_dirs():
    """Returns a list of user configuration directories to be searched.
    """
    if platform.system() == 'Darwin':
        paths = [MAC_DIR, UNIX_DIR_FALLBACK]
    elif platform.system() == 'Windows':
        if WINDOWS_DIR_VAR in os.environ:
            paths = [os.environ[WINDOWS_DIR_VAR]]
        else:
            paths = [WINDOWS_DIR_FALLBACK]
    else:
        # Assume Unix.
        paths = [UNIX_DIR_FALLBACK]
        if UNIX_DIR_VAR in os.environ:
            paths.insert(0, os.environ[UNIX_DIR_VAR])

    # Expand and deduplicate paths.
    out = []
    for path in paths:
        path = os.path.abspath(os.path.expanduser(path))
        if path not in out:
            out.append(path)
    return  out


# Validation and conversion helpers.

def as_filename(view, value):
    """Gets a string as a normalized filename, made absolute and with
    tilde expanded.
    """
    value = STRING(value)
    return os.path.abspath(os.path.expanduser(value))

def as_choice(choices):
    """Returns a function that ensures that the value is one of a
    collection of choices.
    """
    def f(view, value):
        if value not in choices:
            raise ConfigValueError(
                '{0} must be one of {1}, not {2}'.format(
                    view.name, repr(value), repr(list(choices))
                )
            )
        return value
    return f


# Main interface.

class Configuration(RootView):
    def __init__(self, appname, modname=None, read=True):
        """Create a configuration object by reading the
        automatically-discovered config files for the application for a
        given name. If `modname` is specified, it should be the import
        name of a module whose package will be searched for a default
        config file. (Otherwise, no defaults are used.)
        """
        super(Configuration, self).__init__([])
        self.appname = appname
        self.modname = modname
        if read:
            self._read()

    def _search_dirs(self):
        """Yield directories that will be searched for configuration
        files for this application.
        """
        for confdir in config_dirs():
            yield os.path.join(confdir, self.appname)

    def _filenames(self):
        """Get a list of filenames for configuration files. The files
        actually exist and are in the order that they should be
        prioritized.
        """
        out = []

        # Search standard directories.
        for appdir in self._search_dirs():
            out.append(os.path.join(appdir, CONFIG_FILENAME))

        # Search the package for a defaults file.
        if self.modname:
            pkg_path = _package_path(self.modname)
            if pkg_path:
                out.append(os.path.join(pkg_path, DEFAULT_FILENAME))

        return [p for p in out if os.path.isfile(p)]

    def _read(self):
        """Read the default files for this configuration and set them as
        the sources for this configuration.
        """
        self.sources = []
        for filename in self._filenames():
            try:
                with open(filename, 'r') as f:
                    data = yaml.load(f)
            except (IOError, yaml.error.YAMLError) as exc:
                raise ConfigReadError(filename, exc)
            self.sources.append(data)

    def add_args(self, namespace):
        """Add parsed command-line arguments, generated by a library
        like argparse or optparse, as an overlay to the configuration
        sources.
        """
        arg_source = {}
        for key, value in namespace.__dict__.items():
            if value is not None:  # Avoid unset options.
                arg_source[key] = value
        self.sources.insert(0, arg_source)

    def config_dir(self):
        """Get the path to the directory containing the highest-priority
        user configuration. If no user configuration is present, create a
        suitable directory before returning it.
        """
        dirs = list(self._search_dirs())

        # First, look for an existant configuration file.
        for appdir in dirs:
            if os.path.isfile(os.path.join(appdir, CONFIG_FILENAME)):
                return appdir

        # As a fallback, create the first-listed directory name.
        appdir = dirs[0]
        if not os.path.isdir(appdir):
            os.makedirs(appdir)
        return appdir
