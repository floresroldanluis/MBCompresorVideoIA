"""Reuse measured NVENC rates only for the exact same input and encoding plan.

This is a local record of completed encodes, not an AI model or a prediction for
unseen videos. A changed source, encoder, driver, profile or byte limit misses.
Failure to read/write the optional record must never fail a video conversion.
"""
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from size_units import maximum_bytes


SCHEMA=1
PROFILE_REVISION='nvenc-vbr-windows-gib-p6-qres-high-2026-11'
MIN_DURATION=60.0
MAX_RECORDS=128
MAX_HISTORY_BYTES=262144
_lock=threading.RLock()
_binary_hashes={}


def history_path():
    base=Path(os.environ.get('USERPROFILE',str(Path.home())))/'AppData'/'Local' if os.name=='nt' else Path(os.environ.get('LOCALAPPDATA',str(Path.home())))
    return base/'MBCompresorPro'/'nvenc-rate-history.json'


def _source_signature(path):
    path=Path(path).resolve()
    stat=path.stat()
    if not path.is_file() or stat.st_size<=0:raise ValueError('Not a nonempty file')
    return [os.path.normcase(str(path)),stat.st_size,stat.st_mtime_ns]


def _binary_signature(path):
    path=Path(path).resolve()
    before=_source_signature(path)
    cache_key=tuple(before)
    with _lock:
        digest=_binary_hashes.get(cache_key)
    if digest is None:
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if _source_signature(path)!=before:raise ValueError('Encoder changed during inspection')
        with _lock:
            if len(_binary_hashes)>=16:_binary_hashes.clear()
            _binary_hashes[cache_key]=digest
    # Identical bundled binaries remain usable after installation to a new path.
    return [before[1],digest]


def build_context(source,ffmpeg,ffprobe,options,driver,source_info):
    """Return an exact plan key, or None when calibration cannot be trusted.

    source_info comes from ffprobe on the original. This helper is also used to
    import an explicitly verified rate from a previous application's event log.
    It never encodes a video or writes a record.
    """
    try:
        values=dataclasses.asdict(options) if dataclasses.is_dataclass(options) else dict(options)
        if values.get('unlimited') or values.get('local_ai') or values.get('codec') not in ('h264','h265'):return None
        maximum=float(values['max_gb'])
        if not math.isfinite(maximum) or maximum<=0:return None
        duration=float(source_info['duration'])-float(values.get('trim_start',0))
        if values.get('trim_end') is not None:duration=float(values['trim_end'])-float(values.get('trim_start',0))
        if not math.isfinite(duration) or duration<MIN_DURATION:return None
        driver=str(driver).strip()
        if not driver:return None
        source_signature=_source_signature(source)
        for key in ('output_name','output_dir','output_folder'):values.pop(key,None)
        cap=maximum_bytes(maximum)
        width=int(source_info['width']);height=int(source_info['height'])
        chosen_height=int(values.get('height') or height)
        output_height=min(height,chosen_height)
        if values.get('height'):
            max_width=round(chosen_height*16/9/2)*2
            output_height=min(output_height,int(height*max_width/width))
        output_height=output_height//2*2
        output_width=round(width*output_height/height/2)*2
        fps=values.get('fps')
        if fps is None:
            ratio=source_info.get('video',{}).get('avg_frame_rate','0/1')
            numerator,denominator=map(float,ratio.split('/'))
            fps=numerator/denominator if denominator else 0.
        if not math.isfinite(float(fps)) or float(fps)<=0:return None
        payload=dict(revision=PROFILE_REVISION,source=source_signature,
                     encoder=_binary_signature(ffmpeg),probe=_binary_signature(ffprobe),
                     driver=driver,options=values,cap=cap)
        encoded=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        return dict(key=hashlib.sha256(encoded).hexdigest(),cap=cap,duration=duration,
                    codec='hevc' if values['codec']=='h265' else 'h264',
                    source_signature=source_signature,
                    output_width=output_width,output_height=output_height,fps=float(fps))
    except Exception:
        return None


def _read(path):
    try:
        with Path(path).open('rb') as stream:raw=stream.read(MAX_HISTORY_BYTES+1)
        if len(raw)>MAX_HISTORY_BYTES:return {}
        value=json.loads(raw)
        if value.get('schema')!=SCHEMA or not isinstance(value.get('records'),dict):return {}
        if len(value['records'])>MAX_RECORDS:return {}
        return value['records']
    except (OSError,ValueError,TypeError,AttributeError):return {}


def _usable(record,context):
    try:
        requested=record['requested_bitrate']
        measured=float(record['measured_video_bitrate'])
        return (type(requested) is int and 10000<=requested<=1_000_000_000 and
                math.isfinite(measured) and measured>0 and .5<=requested/measured<=2 and
                record['cap']==context['cap'] and 0<record['total_bytes']<=context['cap'] and
                measured<=record['total_bytes']*8/max(1.,context['duration']-1.)*1.01 and
                record['codec']==context['codec'] and
                math.isfinite(float(record['updated'])))
    except (ValueError,TypeError,KeyError,ZeroDivisionError):return False


def lookup(context,cache_path=None):
    if context is None:return None
    try:
        if _source_signature(context['source_signature'][0])!=context['source_signature']:return None
        with _lock:record=_read(cache_path or history_path()).get(context['key'])
        return dict(record) if isinstance(record,dict) and _usable(record,context) else None
    except Exception:return None


def _updated(item):
    try:
        value=float(item[1]['updated'])
        return value if math.isfinite(value) else 0.
    except (ValueError,TypeError,KeyError):return 0.


def record_validated(context,requested_bitrate,output,probe_callback,provenance='completed-encode',cache_path=None):
    """Record a completed, measured MP4; optional imports require explicit evidence.

    probe_callback(path) returns the engine's ffprobe metadata dictionary. A
    caller importing an earlier result supplies the rate from its event log and
    describes that evidence in provenance. No video is renamed or modified.
    """
    if context is None:return False
    temporary=None
    try:
        source=context['source_signature']
        if _source_signature(source[0])!=source:return False
        output=Path(output).resolve()
        if os.path.normcase(str(output))==source[0]:return False
        before=_source_signature(output)
        actual=before[1]
        if actual>context['cap']:return False
        info=probe_callback(output)
        video=info['video'];duration=float(video['duration']);measured=float(video['bit_rate'])
        expected=float(context['duration'])
        if (video['codec_name']!=context['codec'] or video.get('pix_fmt')!='yuv420p' or
            not math.isfinite(duration) or abs(duration-expected)>max(1.,expected*.0001) or
            abs(int(info['width'])-context['output_width'])>2 or
            int(info['height'])!=context['output_height']):return False
        if context['fps']:
            numerator,denominator=map(float,video['avg_frame_rate'].split('/'))
            if not denominator or abs(numerator/denominator-context['fps'])>.01:return False
        if _source_signature(output)!=before:return False
        record=dict(requested_bitrate=requested_bitrate,measured_video_bitrate=measured,
                    total_bytes=actual,cap=context['cap'],codec=context['codec'],
                    updated=time.time(),provenance=str(provenance)[:200])
        if not _usable(record,context):return False
        path=Path(cache_path or history_path())
        with _lock:
            records=_read(path);records[context['key']]=record
            records=dict(sorted(records.items(),key=_updated,reverse=True)[:MAX_RECORDS])
            data=json.dumps(dict(schema=SCHEMA,records=records),separators=(',',':'),allow_nan=False).encode('utf-8')
            if len(data)>MAX_HISTORY_BYTES:return False
            path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix='rate-history-',suffix='.tmp',dir=path.parent,delete=False) as stream:
                temporary=Path(stream.name);stream.write(data)
            os.replace(temporary,path);temporary=None
        return True
    except Exception:return False
    finally:
        if temporary is not None:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
