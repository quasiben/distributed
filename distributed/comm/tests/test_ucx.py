import asyncio
import itertools

import pytest
ucp = pytest.importorskip('ucp')

from distributed import Client
from distributed.comm import ucx, listen, connect
from distributed.comm.registry import backends, get_backend
from distributed.comm import ucx, parse_address, parse_host_port
from distributed.protocol import to_serialize
from distributed.deploy.local import LocalCluster
from distributed.utils_test import gen_test, loop, inc

from .test_comms import check_deserialize


ucx_addr = ucp.get_address()
scheduler_port = 13337

ADDRESS = ucx_addr
HOST = f'ucx://{ucx_addr}:{scheduler_port}'

# Currently having some issues with re-using ports.
# Tests just hang. Still debugging.
port_counter = itertools.count(scheduler_port)


def test_parse_address():
    result = ucx._parse_address("ucx://10.33.225.160")
    assert result == ("ucx", "10.33.225.160")


def test_parse_host_port():
    assert ucx._parse_host_port("10.33.225.160") == ("10.33.225.160", 13337)
    assert ucx._parse_host_port("10.33.225.160:13337") == ("10.33.225.160", 13337)
    assert ucx._parse_host_port("10.33.225.160:13338") == ("10.33.225.160", 13338)


def test_registered():
    assert "ucx" in backends
    backend = get_backend("ucx")
    assert isinstance(backend, ucx.UCXBackend)


async def get_comm_pair(listen_addr, listen_args=None, connect_args=None, **kwargs):
    q = asyncio.queues.Queue()

    async def handle_comm(comm):
        await q.put(comm)

    # Workaround for hanging test in
    # pytest distributed/comm/tests/test_ucx.py::test_comm_objs -vs --count=2
    # on the second time through.
    ucp._libs.ucp_py.reader_added = 0

    listener = listen(listen_addr, handle_comm, connection_args=listen_args, **kwargs)
    with listener:
        comm = await connect(
            listener.contact_address, connection_args=connect_args, **kwargs
        )
        serv_com = await q.get()
        return comm, serv_com


@pytest.mark.asyncio
async def test_ping_pong():
    address = "{}:{}".format(HOST, next(port_counter))
    com, serv_com = await get_comm_pair(address)
    msg = {"op": "ping"}
    await com.write(msg)
    result = await serv_com.read()
    assert result == msg
    result["op"] = "pong"

    await serv_com.write(result)

    result = await com.read()
    assert result == {"op": "pong"}

    await com.close()
    await serv_com.close()


@pytest.mark.asyncio
async def test_comm_objs():
    address = "{}:{}".format(HOST, next(port_counter))
    comm, serv_com = await get_comm_pair(address)

    assert comm.peer_address == address
    scheme, loc = parse_address(comm.peer_address)
    assert scheme == 'ucx'

    assert comm.peer_address == address
    scheme, loc = parse_address(serv_com.peer_address)
    assert scheme == 'ucx'


def test_ucx_specific():
    """
    Test concrete UCX API.
    """
    # TODO:
    # 1. ensure exceptions in handle_comm fail the test
    # 2. Use dict in read / write, put seralization there.
    # 3. Test peer_address
    # 4. Test cleanup
    async def f():
        address = "{}:{}".format(HOST, next(port_counter))

        async def handle_comm(comm):
            # XXX: failures here don't fail the build yet
            msg = await comm.read()
            msg["op"] = "pong"
            await comm.write(msg)
            assert comm.closed() is False
            await comm.close()
            assert comm.closed

        listener = ucx.UCXListener(address, handle_comm)
        listener.start()
        host, port = listener.get_host_port()
        assert host.count(".") == 3
        assert port > 0

        connector = ucx.UCXConnector()
        l = []

        async def client_communicate(key, delay=0):
            addr = "%s:%d" % (host, port)
            comm = await connector.connect(addr)
            # TODO: peer_address
            # assert comm.peer_address == 'ucx://' + addr
            assert comm.extra_info == {}
            msg = {"op": "ping", "data": key}
            await comm.write(msg)
            if delay:
                await asyncio.sleep(delay)
            msg = await comm.read()
            assert msg == {"op": "pong", "data": key}
            l.append(key)
            return comm
            assert comm.closed() is False
            await comm.close()
            assert comm.closed

        comm = await client_communicate(key=1234, delay=0.5)

        # Many clients at once
        N = 2
        futures = [client_communicate(key=i, delay=0.05) for i in range(N)]
        await asyncio.gather(*futures)
        assert set(l) == {1234} | set(range(N))

    asyncio.run(f())


@pytest.mark.asyncio
async def test_ping_pong_data():
    np = pytest.importorskip('numpy')

    data = np.ones((10, 10))
    # TODO: broken for large arrays
    address = "{}:{}".format(HOST, next(port_counter))
    com, serv_com = await get_comm_pair(address)
    msg = {"op": "ping", "data": to_serialize(data)}
    await com.write(msg)
    result = await serv_com.read()
    result["op"] = "pong"
    data2 = result.pop('data')
    np.testing.assert_array_equal(data2, data)

    await serv_com.write(result)

    result = await com.read()
    assert result == {"op": "pong"}

    await com.close()
    await serv_com.close()


@gen_test()
def test_ucx_deserialize():
    yield check_deserialize("tcp://")


@pytest.mark.asyncio
async def test_ping_pong_cudf():
    # if this test appears after cupy an import error arises
    # *** ImportError: /usr/lib/x86_64-linux-gnu/libstdc++.so.6: version `CXXABI_1.3.11'
    # not found (required by python3.7/site-packages/pyarrow/../../../libarrow.so.12)
    cudf = pytest.importorskip('cudf')

    df = cudf.DataFrame({"A": [1, 2, None], "B": [1., 2., None]})
    address = "{}:{}".format(HOST, next(port_counter))

    com, serv_com = await get_comm_pair(address)
    msg = {"op": "ping", 'data': to_serialize(df)}

    await com.write(msg)
    result = await serv_com.read()
    data2 = result.pop('data')
    assert result['op'] == 'ping'


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', [
    (100,),
    (10, 10)
])
async def test_ping_pong_cupy(shape):
    cupy = pytest.importorskip('cupy')
    address = "{}:{}".format(HOST, next(port_counter))
    com, serv_com = await get_comm_pair(address)

    arr = cupy.random.random(shape)
    msg = {"op": "ping", 'data': to_serialize(arr)}

    await com.write(msg)
    result = await serv_com.read()
    data2 = result.pop('data')

    assert result['op'] == 'ping'
    cupy.testing.assert_array_equal(arr, data2)
    await com.close()
    await serv_com.close()


@pytest.mark.asyncio
async def test_ping_pong_numba():
    np = pytest.importorskip('numpy')
    numba = pytest.importorskip("numba")
    import numba.cuda

    address = "{}:{}".format(HOST, next(port_counter))

    arr = np.arange(10)
    arr = numba.cuda.to_device(arr)

    com, serv_com = await get_comm_pair(address)
    msg = {"op": "ping", 'data': to_serialize(arr)}

    await com.write(msg)
    result = await serv_com.read()
    data2 = result.pop('data')
    assert result['op'] == 'ping'

@pytest.mark.parametrize('processes', [
    True,
    False,
])
def test_ucx_localcluster(loop, processes):
    if processes:
        kwargs = {'env': {'UCX_MEMTYPE_CACHE': 'n'}}
    else:
        kwargs = {}

    ucx_addr = ucp.get_address()
    port = 13337
    with LocalCluster(protocol="ucx://", scheduler_port=port,
                        ip=ucx_addr,
                        dashboard_address=None,
                        n_workers=2,
                        threads_per_worker=1,
                        processes=processes,
                        **kwargs,
                        ) as cluster:
        with Client(cluster) as e:
            x = e.submit(inc, 1)
            x.result()
            assert x.key in cluster.scheduler.tasks
            assert any(w.data == {x.key: 2} for w in cluster.workers)
            assert e.loop is cluster.loop
            assert len(cluster.scheduler.workers) == 2
            print(cluster.scheduler.workers)


def test_tcp_localcluster(loop):
    ucx_addr = '127.0.0.1'
    port = 13337
    env={'UCX_MEMTYPE_CACHE': 'n'}
    with LocalCluster(
        2,
        scheduler_port=port,
        ip = ucx_addr,
        processes=True,
        threads_per_worker=1,
        dashboard_address=None,
        silence_logs=False,
        env=env,
    ) as cluster:
        print(cluster.scheduler.workers)
        # with Client(cluster) as e:
        #     x = e.submit(inc, 1)
        #     x.result()
        #     assert x.key in c.scheduler.tasks
        #     assert any(w.data == {x.key: 2} for w in c.workers)
        #     assert e.loop is c.loop
        #     print(c.scheduler.workers)