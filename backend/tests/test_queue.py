import asyncio

from spookhub.boards.queue import Command, LatestWinsQueue


async def test_latest_wins_drops_intermediate():
    sent: list[int] = []
    gate = asyncio.Event()

    async def send(cmd: Command, timeout: float) -> float:
        await gate.wait()
        sent.append(cmd.payload["n"])
        return 1.0

    q = LatestWinsQueue(send=send)
    q.start()
    q.submit({"n": 1}, critical=False)
    await asyncio.sleep(0.01)  # 1 is now in flight
    for n in (2, 3, 4):
        q.submit({"n": n}, critical=False)
    gate.set()
    for _ in range(100):
        if sent and sent[-1] == 4 and q.idle:
            break
        await asyncio.sleep(0.01)
    await q.stop()
    assert sent == [1, 4]
    assert q.dropped == 2


async def test_critical_gives_up_after_one_retry():
    calls = 0

    async def send(cmd: Command, timeout: float) -> float:
        nonlocal calls
        calls += 1
        assert timeout == 0.3
        raise OSError("down")

    q = LatestWinsQueue(send=send)
    q.start()
    q.submit({"x": 1}, critical=True)
    await asyncio.sleep(0.1)
    await q.stop()
    assert calls == 2


async def test_noncritical_retry_abandoned_for_newer_command():
    calls: list[int] = []

    async def send(cmd: Command, timeout: float) -> float:
        calls.append(cmd.payload["n"])
        if cmd.payload["n"] == 1:
            raise OSError("down")
        return 1.0

    q = LatestWinsQueue(send=send)
    q.start()
    q.submit({"n": 1}, critical=False)
    await asyncio.sleep(0.05)  # first attempt failed, now backing off
    q.submit({"n": 2}, critical=False)
    await asyncio.sleep(0.1)
    await q.stop()
    assert calls == [1, 2]


async def test_slow_board_does_not_block_others():
    done: dict[str, float] = {}
    loop = asyncio.get_running_loop()

    def make(name: str, delay: float) -> LatestWinsQueue:
        async def send(cmd: Command, timeout: float) -> float:
            await asyncio.sleep(delay)
            done[name] = loop.time()
            return delay

        return LatestWinsQueue(send=send)

    slow, fast = make("slow", 0.5), make("fast", 0.01)
    t0 = loop.time()
    for q in (slow, fast):
        q.start()
        q.submit({}, critical=True)
    await asyncio.sleep(0.1)
    assert done["fast"] - t0 < 0.1
    await slow.stop()
    await fast.stop()
