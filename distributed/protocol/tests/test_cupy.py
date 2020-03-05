import pickle

import pytest
from distributed.protocol import deserialize, serialize

cupy = pytest.importorskip("cupy")
cupy_sparse = pytest.importorskip("cupyx.scipy.sparse")
scipy_sparse = pytest.importorskip("scipy.sparse")
numpy = pytest.importorskip("numpy")


@pytest.mark.parametrize("shape", [(0,), (5,), (4, 6), (10, 11), (2, 3, 5)])
@pytest.mark.parametrize("dtype", ["u1", "u4", "u8", "f4"])
@pytest.mark.parametrize("order", ["C", "F"])
@pytest.mark.parametrize("serializers", [("cuda",), ("dask",), ("pickle",)])
def test_serialize_cupy(shape, dtype, order, serializers):
    x = cupy.arange(numpy.product(shape), dtype=dtype)
    x = cupy.ndarray(shape, dtype=x.dtype, memptr=x.data, order=order)
    header, frames = serialize(x, serializers=serializers)
    y = deserialize(header, frames, deserializers=serializers)

    if serializers[0] == "cuda":
        assert all(hasattr(f, "__cuda_array_interface__") for f in frames)
    elif serializers[0] == "dask":
        assert all(isinstance(f, memoryview) for f in frames)

    assert (x == y).all()


@pytest.mark.parametrize("dtype", ["u1", "u4", "u8", "f4"])
def test_serialize_cupy_from_numba(dtype):
    cuda = pytest.importorskip("numba.cuda")
    np = pytest.importorskip("numpy")

    if not cuda.is_available():
        pytest.skip("CUDA is not available")

    size = 10
    x_np = np.arange(size, dtype=dtype)
    x = cuda.to_device(x_np)
    header, frames = serialize(x, serializers=("cuda", "dask", "pickle"))
    header["type-serialized"] = pickle.dumps(cupy.ndarray)

    y = deserialize(header, frames, deserializers=("cuda", "dask", "pickle", "error"))

    assert (x_np == cupy.asnumpy(y)).all()


@pytest.mark.parametrize("size", [0, 3, 10])
def test_serialize_cupy_from_rmm(size):
    np = pytest.importorskip("numpy")
    rmm = pytest.importorskip("rmm")

    x_np = np.arange(size, dtype="u1")

    x_np_desc = x_np.__array_interface__
    (x_np_ptr, _) = x_np_desc["data"]
    (x_np_size,) = x_np_desc["shape"]
    x = rmm.DeviceBuffer(ptr=x_np_ptr, size=x_np_size)

    header, frames = serialize(x, serializers=("cuda", "dask", "pickle"))
    header["type-serialized"] = pickle.dumps(cupy.ndarray)

    y = deserialize(header, frames, deserializers=("cuda", "dask", "pickle", "error"))

    assert (x_np == cupy.asnumpy(y)).all()


@pytest.mark.parametrize(
    "sparse_type", [cupy_sparse.dia_matrix,],
)
@pytest.mark.parametrize(
    "dtype", [numpy.dtype("<f4"), numpy.dtype("<f8"),],
)
@pytest.mark.parametrize("serializer", ["cuda", "dask",])
def test_serialize_cupy_sparse_dia(sparse_type, dtype, serializer):
    data = numpy.array([[0, 1, 2], [3, 4, 5]], dtype)
    offsets = numpy.array([0, -1], "i")
    shape = (3, 4)
    a_host = scipy_sparse.dia_matrix((data, offsets), shape=shape)

    asp = sparse_type((data, offsets), shape=shape)

    header, frames = serialize(asp, serializers=[serializer])
    asp2 = deserialize(header, frames)

    a2 = asp2.todense()
    a2_host = cupy.asnumpy(a2)

    assert (a_host == a2_host).all()


@pytest.mark.parametrize(
    "sparse_type",
    [cupy_sparse.csr_matrix, cupy_sparse.csc_matrix, cupy_sparse.coo_matrix,],
)
@pytest.mark.parametrize(
    "dtype", [numpy.dtype("<f4"), numpy.dtype("<f8")],
)
@pytest.mark.parametrize("serializer", ["cuda", "dask",])
def test_serialize_cupy_sparse(sparse_type, dtype, serializer):
    data = cupy.array([0, 1, 2, 3], dtype)
    indices = cupy.array([0, 1, 3, 2], "i")
    indptr = cupy.array([0, 2, 3, 4], "i")

    if sparse_type is cupy_sparse.coo_matrix:
        asp = sparse_type((data, (indices, indptr)))
    else:
        asp = sparse_type((data, indices, indptr))

    header, frames = serialize(asp, serializers=[serializer])
    asp2 = deserialize(header, frames)

    for k in asp.__dict__.keys():
        if isinstance(asp.__dict__[k], cupy.ndarray):
            cupy.testing.assert_array_equal(asp.__dict__[k], asp2.__dict__[k])
