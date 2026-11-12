"""Best-effort Windows file-use diagnostics; never stops apps or services."""
from __future__ import annotations

import ctypes
import os


DWORD = ctypes.c_uint32
UINT = ctypes.c_uint32
ERROR_MORE_DATA = 234
# Windows SDK RestartManager.h: maximum excludes the terminating WCHAR.
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63


class FILETIME(ctypes.Structure):
    _fields_ = [('dwLowDateTime', DWORD), ('dwHighDateTime', DWORD)]


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [('dwProcessId', DWORD), ('ProcessStartTime', FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [('Process', RM_UNIQUE_PROCESS),
                ('strAppName', ctypes.c_wchar * (CCH_RM_MAX_APP_NAME + 1)),
                ('strServiceShortName', ctypes.c_wchar * (CCH_RM_MAX_SVC_NAME + 1)),
                ('ApplicationType', ctypes.c_int32), ('AppStatus', DWORD),
                ('TSSessionId', DWORD), ('bRestartable', ctypes.c_int32)]


def _load_api():
    api = ctypes.WinDLL('RstrtMgr.dll')
    api.RmStartSession.argtypes = [ctypes.POINTER(DWORD), DWORD, ctypes.c_wchar_p]
    api.RmRegisterResources.argtypes = [DWORD, UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                      UINT, ctypes.POINTER(RM_UNIQUE_PROCESS),
                                      UINT, ctypes.POINTER(ctypes.c_wchar_p)]
    api.RmGetList.argtypes = [DWORD, ctypes.POINTER(UINT), ctypes.POINTER(UINT),
                             ctypes.POINTER(RM_PROCESS_INFO), ctypes.POINTER(DWORD)]
    api.RmEndSession.argtypes = [DWORD]
    for name in ('RmStartSession', 'RmRegisterResources', 'RmGetList', 'RmEndSession'):
        getattr(api, name).restype = DWORD
    return api


def _unavailable(code=None):
    detail = f', código {code}' if code is not None else ''
    return f'No se pudo identificar el proceso que ocupa el archivo (Restart Manager{detail}).'


def describe_locking_processes(paths) -> str:
    """Describe processes using any given absolute file paths, without closing them.

    This is a snapshot of Restart Manager's visible resource users, not proof
    that every returned process denies deletion. Unreported/transient locks and
    API/access failures are normal. One list-size retry bounds API call count;
    Windows controls the duration of each synchronous API call.
    """
    if os.name != 'nt':
        return ''
    api = None
    session = DWORD()
    started = False
    try:
        if isinstance(paths, (str, bytes, os.PathLike)):
            paths = (paths,)
        files = {}
        for path in paths:
            value = os.path.abspath(os.fsdecode(os.fspath(path)))
            if '\0' in value:
                return _unavailable()
            files[os.path.normcase(value)] = value
        if not files:
            return ''
        api = _load_api()
        code = api.RmStartSession(ctypes.byref(session), 0, ctypes.create_unicode_buffer(33))
        if code:
            return _unavailable(code)
        started = True
        resources = (ctypes.c_wchar_p * len(files))(*files.values())
        code = api.RmRegisterResources(session, len(files), resources, 0, None, 0, None)
        if code:
            return _unavailable(code)
        needed, count, reasons = UINT(), UINT(), DWORD()
        code = api.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                             None, ctypes.byref(reasons))
        processes = ()
        if code == ERROR_MORE_DATA:
            if not 0 < needed.value <= 4096:
                return _unavailable(code)
            processes = (RM_PROCESS_INFO * needed.value)()
            count.value = len(processes)
            code = api.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                 processes, ctypes.byref(reasons))
        if code:
            return _unavailable(code)
        descriptions = {}
        for process in processes[:min(count.value, len(processes))]:
            pid = process.Process.dwProcessId
            name = process.strAppName or process.strServiceShortName or 'aplicación desconocida'
            name = ' '.join(''.join(c if c.isprintable() else ' ' for c in name).split())
            descriptions[pid] = f'{name or "aplicación desconocida"} (PID {pid})'
        if descriptions:
            entries = list(descriptions.values())
            extra = f'; otros {len(entries)-16} procesos' if len(entries) > 16 else ''
            return 'Procesos que usan los archivos: ' + '; '.join(entries[:16]) + extra + '.'
        return ('Windows no identificó un proceso que use estos archivos; el bloqueo puede '
                'ser transitorio o no estar visible para Restart Manager.')
    except Exception:
        # Diagnostics must never replace the original error or disclose paths.
        return _unavailable()
    finally:
        if started:
            try:
                api.RmEndSession(session)
            except Exception:
                pass
