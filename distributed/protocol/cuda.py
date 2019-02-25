"""
Efficient serialization GPU arrays.
"""
import cupy
from .serialize import dask_serialize, dask_deserialize

# Some questions
# 1.Do we need *protocol-dependent* serialization?
#   I assume we want this kind of serialization only when
#   in UCP.
# 2. What does ucp-py need to know about?


@dask_serialize.register(cupy.ndarray)
def serialize_cupy_ndarray(x):
    # TODO: handle non-contiguous
    # TODO: handle 2d
    # TODO: 0d

    if x.flags.c_contiguous or x.flags.f_contiguous:
        strides = x.strides
        data = x.ravel()  # order='K'
    else:
        x = cupy.ascontiguousarray(x)
        strides = x.strides
        data = x.ravel()

    dtype = (0, x.dtype.str)

    header = x.__cuda_array_interface__.copy()
    header['lengths'] = (x.nbytes,)  # one per stride
    header['compression'] = (None,)  # TODO
    header['is_cuda'] = True
    header['dtype'] = dtype
    return header, [data]


@dask_deserialize.register(cupy.ndarray)
def deserialize_cupy_array(header, frames):
    frame, = frames
    # TODO: put this in ucx... as a kind of "fixup"
    frame.typestr = header['typestr']
    frame.shape = header['shape']
    arr = cupy.asarray(frame)
    return arr
