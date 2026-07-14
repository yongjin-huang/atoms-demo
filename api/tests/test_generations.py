"""Event-log mechanics: replay, live follow, multi-subscriber, cancel.
Pure asyncio — the driver is injected, so no SDKs, no database."""

import asyncio
import time

from generations import Generation, Registry


def collect(reg, gen, since=0):
    async def _c():
        return [f async for f in reg.subscribe(gen, since) if not f.startswith(":")]
    return _c()


def test_replay_then_terminate():
    async def main():
        reg = Registry()
        def runner(gen: Generation):
            gen.emit("chat", {"text": "hi"})
            gen.emit("done", {"ok": True})
            gen.finish("done")
        gen = reg.start(user_id="u", project_id=None, prompt="p", model_id="m", runner=runner)
        frames = await collect(reg, gen)
        assert "event: chat" in frames[0]
        assert "event: done" in frames[1]
        assert frames[0].startswith("id: 0\n") and frames[1].startswith("id: 1\n")
    asyncio.run(main())


def test_late_subscriber_gets_full_replay():
    async def main():
        reg = Registry()
        def runner(gen):
            for i in range(5):
                gen.emit("code", {"text": str(i)})
            gen.finish("done")
        gen = reg.start(user_id="u", project_id=None, prompt="p", model_id="m", runner=runner)
        await asyncio.sleep(0.05)          # let it finish before anyone watches
        frames = await collect(reg, gen)
        assert len(frames) == 5            # nothing lost
        resumed = await collect(reg, gen, since=3)
        assert len(resumed) == 2           # since=seq resumes mid-log
    asyncio.run(main())


def test_two_subscribers_see_everything():
    async def main():
        reg = Registry()
        def runner(gen):
            for i in range(20):
                gen.emit("chat", {"text": str(i)})
                time.sleep(0.002)          # interleave with subscribers
            gen.finish("done")
        gen = reg.start(user_id="u", project_id=None, prompt="p", model_id="m", runner=runner)
        a, b = await asyncio.gather(collect(reg, gen), collect(reg, gen))
        assert len(a) == 20 and a == b
    asyncio.run(main())


def test_cancel_is_cooperative():
    async def main():
        reg = Registry()
        def runner(gen: Generation):
            while not gen.cancel_flag.is_set():
                time.sleep(0.005)
            gen.emit("stopped", {})
            gen.finish("stopped")
        gen = reg.start(user_id="u", project_id=None, prompt="p", model_id="m", runner=runner)
        await asyncio.sleep(0.02)
        gen.cancel_flag.set()
        frames = await collect(reg, gen)
        assert any("event: stopped" in f for f in frames)
        assert gen.status == "stopped"
    asyncio.run(main())


def test_ownership_lookups():
    async def main():
        reg = Registry()
        def runner(gen):
            while not gen.cancel_flag.is_set():
                time.sleep(0.005)
            gen.finish("stopped")
        gen = reg.start(user_id="u1", project_id="proj", prompt="p", model_id="m", runner=runner)
        assert reg.running_for_project("proj")
        assert [g.id for g in reg.active_for_user("u1")] == [gen.id]
        assert reg.active_for_user("u2") == []
        gen.cancel_flag.set()
        while gen.status == "running":
            await asyncio.sleep(0.01)
        assert not reg.running_for_project("proj")
    asyncio.run(main())
