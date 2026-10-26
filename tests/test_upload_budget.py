"""El disco temporal de las subidas (dueño, 2026-10-12): Starlette vuelca a /tmp cada archivo subido
pasado 1 MiB, y un zip de Prism llega a 1 GiB. Cada subida reserva su Content-Length antes de leerse;
lo que no cabe en el tope se rechaza sin escribir nada, y la reserva se libera al terminar, falle o no."""
import asyncio
import threading

import pytest

from src.library.upload_budget import UploadBudget, UploadBusy


def test_uploads_reserve_their_size_and_free_it_when_done() -> None:
    async def scenario() -> None:
        budget = UploadBudget(max_bytes=100)
        async with budget.reserve(60):
            with pytest.raises(UploadBusy):
                async with budget.reserve(50):
                    pass
            async with budget.reserve(40):
                assert budget.in_use == 100
        assert budget.in_use == 0

    asyncio.run(scenario())


def test_a_failed_upload_frees_its_reservation() -> None:
    async def scenario() -> None:
        budget = UploadBudget(max_bytes=100)
        with pytest.raises(RuntimeError):
            async with budget.reserve(80):
                raise RuntimeError("broken zip")
        assert budget.in_use == 0

    asyncio.run(scenario())


def test_the_stored_files_are_read_off_the_event_loop() -> None:
    """Walking the upload directory blocks (review, 2026-10-22): a thread does it, not the loop."""
    readers: list[threading.Thread] = []

    def stored() -> int:
        readers.append(threading.current_thread())
        return 0

    async def scenario() -> threading.Thread:
        async with UploadBudget(max_bytes=100, stored=stored).reserve(10):
            return threading.current_thread()

    loop_thread = asyncio.run(scenario())
    assert readers and readers[0] is not loop_thread


def test_an_upload_released_while_the_disk_is_read_still_counts() -> None:
    """Review of 2026-10-26: B's copy lands on the disk after A's walk passed it, and B lets go of
    its hold; A counted B's bytes nowhere and took room the disk does not have."""
    walking, landed = threading.Event(), threading.Event()

    def stored() -> int:
        walking.set()
        landed.wait(5)
        return 0  # the walk had already passed where B's copy landed

    async def scenario() -> None:
        budget = UploadBudget(max_bytes=100)
        hold = budget.reserve(60)
        await hold.__aenter__()

        async def b_lands_and_lets_go() -> None:
            await asyncio.to_thread(walking.wait, 5)
            await hold.__aexit__(None, None, None)
            landed.set()

        budget.stored = stored
        mover = asyncio.create_task(b_lands_and_lets_go())
        with pytest.raises(UploadBusy):
            async with budget.reserve(50):
                pass
        await mover

    asyncio.run(scenario())
