import asyncio
import os
import math
import logging
from typing import Optional, Callable
from pyrogram import Client, raw
from pyrogram.errors import FloodWait

logger = logging.getLogger("FastUpload")

async def fast_upload(
    client: Client,
    file_path: str,
    progress: Optional[Callable] = None,
    progress_args: tuple = (),
    workers: int = 15
) -> str:
    """
    Fast upload a file using multiple concurrent connections.
    Returns the file_id or the InputFile object that can be passed to send_document.
    """
    file_size = os.path.getsize(file_path)

    if file_size < 20 * 1024 * 1024:
        return file_path

    part_size = 512 * 1024
    file_total_parts = math.ceil(file_size / part_size)
    is_big = file_size > 10 * 1024 * 1024
    file_id = client.rnd_id()

    dc_id = await client.storage.dc_id()
    session = await client.get_session(dc_id, is_media=True)

    uploaded = 0

    queue = asyncio.Queue()
    for i in range(file_total_parts):
        queue.put_nowait(i)

    async def worker():
        nonlocal uploaded
        while not queue.empty():
            part_idx = await queue.get()
            offset = part_idx * part_size

            with open(file_path, "rb") as f:
                f.seek(offset)
                chunk = f.read(part_size)

            if not chunk:
                queue.task_done()
                continue

            retries = 0
            while retries < 5:
                try:
                    if is_big:
                        rpc = raw.functions.upload.SaveBigFilePart(
                            file_id=file_id,
                            file_part=part_idx,
                            file_total_parts=file_total_parts,
                            bytes=chunk
                        )
                    else:
                        rpc = raw.functions.upload.SaveFilePart(
                            file_id=file_id,
                            file_part=part_idx,
                            bytes=chunk
                        )

                    await session.invoke(rpc)

                    async with asyncio.Lock():
                        uploaded += len(chunk)
                        if progress:
                            if getattr(client, "_progress_lock", None) is None:
                                client._progress_lock = asyncio.Lock()

                            async with client._progress_lock:
                                try:
                                    if asyncio.iscoroutinefunction(progress):
                                        await progress(uploaded, file_size, *progress_args)
                                    else:
                                        progress(uploaded, file_size, *progress_args)
                                except Exception:
                                    pass

                    queue.task_done()
                    break
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception as e:
                    retries += 1
                    if retries >= 5:
                        logger.error(f"Failed to upload part {part_idx} after 5 retries: {e}")
                        queue.task_done()
                        raise e

    tasks = [asyncio.create_task(worker()) for _ in range(min(workers, file_total_parts))]
    await asyncio.gather(*tasks)

    name = os.path.basename(file_path)
    if is_big:
        return raw.types.InputFileBig(
            id=file_id,
            parts=file_total_parts,
            name=name
        )
    else:
        return raw.types.InputFile(
            id=file_id,
            parts=file_total_parts,
            name=name,
            md5_checksum=""
        )
