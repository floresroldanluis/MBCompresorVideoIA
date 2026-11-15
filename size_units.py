"""The maximum-size control uses the binary GB shown by Windows Explorer."""
import math


BYTES_PER_GB=1024**3


def maximum_bytes(max_gb):
    """Convert a positive Windows-size limit to whole bytes, rounding down."""
    value=float(max_gb)
    byte_limit=value*BYTES_PER_GB
    if not math.isfinite(byte_limit) or value<=0:
        raise ValueError('El límite de tamaño debe ser positivo y finito.')
    return math.floor(byte_limit)
