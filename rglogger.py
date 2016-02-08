from __future__ import absolute_import, print_function
import os
import sys
import copy
import socket
import inspect
import logging
import platform
import datetime
import multiprocessing

import requests
import jsonpickle
from six import text_type

try:
    import django
    from django.http import HttpRequest as DjangoRequest
    USE_DJANGO = True
except ImportError:
    USE_DJANGO = False

VERSION_INFO = (1, 1, 0)
VERSION = ".".join(map(text_type, VERSION_INFO))


class Handler(logging.Handler):
    def __init__(
        self,
        api_key,
        raygun_endpoint='https://api.raygun.io/entries',
        version='',
        transmit_local_variables=True,
        transmit_global_variables=True,
        timeout=30,
        machine_name='',
        tags=None,
        *args,
        **kwargs
    ):
        super(Handler, self).__init__(*args, **kwargs)
        self.api_key = api_key
        self.raygun_endpoint = raygun_endpoint
        self.version = version
        self.transmit_local_variables = transmit_local_variables
        self.transmit_global_variables = transmit_global_variables
        self.timeout = timeout
        self.tags = tags or []
        self.environment_data = {
            "processorCount": multiprocessing.cpu_count(),
            "architecture": platform.architecture()[0],
            "cpu": platform.processor(),
            "oSVersion": "%s %s" % (platform.system(), platform.release()),
            "environmentVariables": os.environ.data,
            "runtimeLocation": sys.executable,
            "runtimeVersion": 'Python ' + sys.version
        }
        if USE_DJANGO:
            self.environment_data['frameworkVersion'] = django.get_version()
        self.machine_name = machine_name or socket.gethostname()

    def emit(self, log_record=None, class_name='', message='', exc_info=None, frames=None, extra_environment_data=None, user_custom_data=None, tags=None, extra_tags=None, user=None, request=None):
        environment_data = copy.deepcopy(self.environment_data)
        environment_data.update(extra_environment_data or {})

        client_data = {
            "name": "raygun4py",  # Can't modify this or we lose python syntax highlighting in the UI!
            "version": VERSION,
            "clientUrl": "https://github.com/mirusresearch/raygun-logger"
        }

        user_custom_data = user_custom_data or {}

        if not exc_info and not frames:
            exc_info = sys.exc_info()
            if not any(exc_info):
                exc_info = None

        if exc_info:
            exc_type, exc_value, exc_traceback = exc_info
            class_name = exc_type.__name__
            message = "%s: %s" % (class_name, exc_value)
            frames = inspect.getinnerframes(exc_traceback)

        if not frames:
            currframe = inspect.currentframe()
            src_frame = currframe.f_back
            # while '/logging/' in src_frame.f_code.co_filename:
            while inspect.getmodule(src_frame) is logging:
                src_frame = src_frame.f_back
            frames = reversed(inspect.getouterframes(src_frame))

        if log_record:
            class_name = log_record.levelname.upper()
            message = "%s: %s" % (class_name, log_record.message)

        stack_trace = []
        global_vars = {}
        request = None
        for idx, frame in enumerate(frames):
            if idx == 0 and self.transmit_global_variables:
                global_vars = frame[0].f_globals
            if not self.transmit_local_variables:
                local_vars = {}
            else:
                local_vars = get_locals(frame[0])
                if not request:
                    if 'request' in local_vars:
                        req = local_vars['request']
                        if USE_DJANGO and isinstance(req, DjangoRequest):
                            request = {
                                "hostName": req.get_host(),
                                "url": req.path,
                                "httpMethod": req.method,
                                "ipAddress": req.META.get('REMOTE_ADDR', '?'),
                                "queryString": dict(req.GET.iteritems()),
                                "form": dict(req.POST.iteritems()),
                                "headers": dict(req.META.iteritems()),
                                "rawData": req.body,
                            }
            stack_trace.append({
                'lineNumber': frame[2],
                'className': frame[3],
                'fileName': frame[1],
                'methodName': frame[4][0] if frame[4] is not None else None,
                'localVariables': get_locals(frame[0]) if self.transmit_local_variables is True else None
            })

        tags = copy.deepcopy(self.tags if tags is None else tags)
        if extra_tags is not None:
            tags.extend(extra_tags)

        msg = {
            'occurredOn': datetime.datetime.utcnow().isoformat(),
            'details': {
                'version': self.version or "Not defined",
                'tags': tags or None,
                'machineName': self.machine_name or None,
                'environment': environment_data,
                'client': client_data,
                'error': {
                    'className': class_name,
                    'message': message,
                    'stackTrace': stack_trace,
                    'globalVariables': global_vars,
                    'data': ""
                },
                'request': request,
                'user': user,
                'userCustomData': user_custom_data,
            }
        }

        headers = {
            "X-ApiKey": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "raygun4py"
        }

        response = requests.post(self.raygun_endpoint, headers=headers, data=jsonpickle.encode(msg), timeout=self.timeout)
        return response


def catch_all(rg_handler):
    def log_exception(exc_type, exc_value, exc_traceback):
        # First call the original excepthook so it gets printed
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        # Then send it on to RG
        rg_handler.emit(exc_info=(exc_type, exc_value, exc_traceback))
    sys.excepthook = log_exception


def get_locals(frame):
    result = {}
    local_vars = getattr(frame, 'f_locals', {})
    for key, val in local_vars.iteritems():
        # Note that str() *can* fail; thus protect against it as much as we can.
        try:
            result[key] = val if isinstance(val, text_type) else text_type(val)
        except Exception as e:
            try:
                r = repr(val)
            except Exception as re:
                r = "Couldn't convert to repr due to {0}".format(re)
            result[key] = "!!! Couldn't convert {0!r} (repr: {1}) due to {2!r} !!!".format(key, r, e)
    return result
