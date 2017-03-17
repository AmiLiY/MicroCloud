#!python
# -*- coding: utf-8 -*-

""" created by yang on 2017/3/14
"""
import inspect
import logging
import logging.config
import logging.handlers
import os
import stat
import sys
import six
import traceback

from six import moves
from functools import wraps

from BCP.Common.Env.ConfigParameter import ConfigParameter

__author__ = "muyidixin2006@126.com"

_AUDIT = logging.INFO + 1
logging.addLevelName(_AUDIT, 'AUDIT')
_TRACE = 5
logging.addLevelName(_TRACE, 'TRACE')

try:
    NullHandler = logging.NullHandler
except AttributeError:  # NOTE(jkoelker) NullHandler added in Python 2.7
    class NullHandler(logging.Handler):
        def handle(self, record):
            pass

        def emit(self, record):
            pass

        def createLock(self):
            self.lock = None


def _get_binary_name():
    return os.path.basename(inspect.stack()[-1][1])


def _get_log_file_path(binary=None):
    logfile = ConfigParameter.log_file
    logdir = ConfigParameter.log_dir

    if logfile and not logdir:
        return logfile

    if logfile and logdir:
        return os.path.join(logdir, logfile)

    if logdir:
        binary = binary or _get_binary_name()
        return '%s.log' % (os.path.join(logdir, binary),)


class BaseLoggerAdapter(logging.LoggerAdapter):

    warn = logging.LoggerAdapter.warning

    @property
    def handlers(self):
        return self.logger.handlers

    def trace(self, msg, *args, **kwargs):
        self.log(_TRACE, msg, *args, **kwargs)

    def audit(self, msg, *args, **kwargs):
        self.log(_AUDIT, msg, *args, **kwargs)


class KeywordArgumentAdapter(BaseLoggerAdapter):
    """Logger adapter to add keyword arguments to log record's extra data

    Keywords passed to the log call are added to the "extra"
    dictionary passed to the underlying logger so they are emitted
    with the log message and available to the format string.

    Special keywords:

    extra
      An existing dictionary of extra values to be passed to the
      logger. If present, the dictionary is copied and extended.
    resource
      A dictionary-like object containing a ``name`` key or ``type``
       and ``id`` keys.

    """

    def process(self, msg, kwargs):
        extra = {}
        extra.update(self.extra)
        if 'extra' in kwargs:
            extra.update(kwargs.pop('extra'))
        for name in list(kwargs.keys()):
            if name == 'exc_info':
                continue
            extra[name] = kwargs.pop(name)
        extra['extra_keys'] = list(sorted(extra.keys()))
        kwargs['extra'] = extra
        resource = kwargs['extra'].get('resource', None)
        if resource:
            if not resource.get('name', None):
                resource_type = resource.get('type', None)
                resource_id = resource.get('id', None)

                if resource_type and resource_id:
                    kwargs['extra']['resource'] = ('[' + resource_type +
                                                   '-' + resource_id + '] ')
            else:
                kwargs['extra']['resource'] = ('[' + resource.get('name', '')
                                               + '] ')

        return msg, kwargs


def handle_exception(type_, value, tb):
    extra = {}
    if ConfigParameter.verbose:
        extra['exc_info'] = (type_, value, tb)
    getLogger().critical(str(value), **extra)


class LogConfigError(Exception):
    message = 'Error loading logging config %(log_config)s: %(err_msg)s'

    def __init__(self, log_config, err_msg):
        self.log_config = log_config
        self.err_msg = err_msg

    def __str__(self):
        return self.message % dict(log_config=self.log_config,
                                   err_msg=self.err_msg)


def _load_log_config():
    try:
        for logger in _iter_loggers():
            logger.level = logging.NOTSET
            logger.handlers = []
            logger.propagate = 1
        logging.config.fileConfig(ConfigParameter.log_config,
                                  disable_existing_loggers=False)
        _refresh_root_level(ConfigParameter.debug, ConfigParameter.verbose)
    except (moves.configparser.Error, KeyError, os.error) as exc:
        raise LogConfigError(ConfigParameter.log_config, six.text_type(exc))


def _create_logging_excepthook(product_name):
    def logging_excepthook(exc_type, value, tb):
        extra = {'exc_info': (exc_type, value, tb)}
        getLogger(product_name).critical(
            "".join(traceback.format_exception_only(exc_type, value)),
            **extra)
    return logging_excepthook


def setup():
    """Setup BCP logging."""
    sys.excepthook = handle_exception

    if ConfigParameter.log_dir:
        if not os.path.isdir(ConfigParameter.log_dir):
            os.mkdir(ConfigParameter.log_dir)
    else:
        os.mkdir("/var/log/bcp")

    if ConfigParameter.log_config:
        _load_log_config()
    else:
        _setup_logging_from_flags()
    sys.excepthook = _create_logging_excepthook("bcp")


def _iter_loggers():
    """Iterate on existing loggers."""

    # Sadly, Logger.manager and Manager.loggerDict are not documented,
    # but there is no logging public function to iterate on all loggers.

    # The root logger is not part of loggerDict.
    yield logging.getLogger()

    manager = logging.Logger.manager
    for logger in manager.loggerDict.values():
        if isinstance(logger, logging.PlaceHolder):
            continue
        yield logger


def _refresh_root_level(debug, verbose):
    """Set the level of the root logger.

    If 'debug' is True, the level will be DEBUG. Otherwise we look at 'verbose'
    - if that is True, the level will be INFO. If neither are set, the level
    will be WARNING.

    Note the 'verbose' option is deprecated.

    :param debug
    :param verbose
    """
    log_root = getLogger().logger
    if debug:
        log_root.setLevel(logging.DEBUG)
    elif verbose:
        log_root.setLevel(logging.INFO)
    else:
        log_root.setLevel(logging.WARNING)


def _setup_logging_from_flags():
    bcp_root = getLogger().logger
    for handler in bcp_root.handlers:
        bcp_root.removeHandler(handler)

    logpath = _get_log_file_path()
    if logpath:
        filelog = logging.handlers.WatchedFileHandler(logpath)
        bcp_root.addHandler(filelog)

        mode = int(ConfigParameter.logfile_mode, 8)
        st = os.stat(logpath)
        if st.st_mode != (stat.S_IFREG | mode):
            os.chmod(logpath, mode)

    for handler in bcp_root.handlers:
        handler.setFormatter(
            logging.Formatter(fmt=ConfigParameter.log_format,
                              datefmt=ConfigParameter.log_date_format))

    _refresh_root_level(ConfigParameter.debug, ConfigParameter.verbose)

    root = logging.getLogger()
    for handler in root.handlers:
        root.removeHandler(handler)
    handler = NullHandler()
    handler.setFormatter(logging.Formatter())
    root.addHandler(handler)


_loggers = {}


def getLogger(name='bcp', project='unknown', version='unknown'):
    if name not in _loggers:
        _loggers[name] = KeywordArgumentAdapter(logging.getLogger(name),
                                                {'project': project,
                                                 'version': version})
    return _loggers[name]


def logged(level, name=None, message=None):
    """
    Add logging to a function. level is the logging
    level, name is the logger name, and message is the
    log message. If name and message aren't specified,
    they default to the function's module and name.
    """

    def decorate(func):
        log_name = name if name else func.__module__
        log = logging.getLogger(log_name)
        log_msg = message if message else func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            log.log(level, log_msg)
            return func(*args, **kwargs)

        return wrapper

    return decorate


def main():
    pass


if __name__ == "__main__":
    main()
