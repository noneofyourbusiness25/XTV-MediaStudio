import asyncio
import os
import math
import logging
from typing import Optional, Callable
from pyrogram import Client, raw, utils
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.errors import FloodWait
from pyrogram.methods.messages.inline_session import get_session

logger = logging.getLogger("FastDownload")

async def fast_download(
    client: Client,
    message,
    file_name: str,
    progress: Optional[Callable] = None,
    progress_args: tuple = (),
    workers: int = 15
) -> str:
    """Download media from a message using multiple concurrent connections."""
    try:
        media = getattr(message, "document", None) or getattr(message, "video", None) or getattr(message, "audio", None) or getattr(message, "photo", None) or getattr(message, "voice", None) or getattr(message, "video_note", None) or getattr(message, "animation", None) or getattr(message, "sticker", None)

        if not media:
            raise ValueError("Message does not contain media")

        file_size = getattr(media, "file_size", 0)

        if not file_size or file_size < 20 * 1024 * 1024:
            raise ValueError("File is too small for fast download.")

        file_id_str = media.file_id
        file_id = FileId.decode(file_id_str)

        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id,
                    access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(
                        chat_id=-file_id.chat_id
                    )
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                photo_id=file_id.media_id,
                big=file_id.thumbnail_source in (
                    ThumbnailSource.CHAT_PHOTO_BIG,
                    ThumbnailSource.CHAT_PHOTO_BIG_LEGACY
                )
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size
            )

        dc_id = file_id.dc_id
        chunk_size = 1024 * 1024  # 1MB per chunk
        total_parts = math.ceil(file_size / chunk_size)

        os.makedirs(os.path.dirname(os.path.abspath(file_name)), exist_ok=True)
        temp_file_name = file_name + ".temp"

        session = await get_session(client, dc_id)
        downloaded = 0
        queue = asyncio.Queue()
        for i in range(total_parts):
            queue.put_nowait(i)

        async def worker():
            nonlocal downloaded
            while not queue.empty():
                part_idx = await queue.get()
                offset_bytes = part_idx * chunk_size
                limit_bytes = chunk_size

                retries = 0
                while retries < 5:
                    try:
                        r = await session.invoke(
                            raw.functions.upload.GetFile(
                                location=location,
                                offset=offset_bytes,
                                limit=limit_bytes
                            )
                        )

                        if isinstance(r, raw.types.upload.File):
                            chunk_data = r.bytes
                        elif isinstance(r, raw.types.upload.FileCdnRedirect):
                            raise ValueError("CDN redirect encountered in parallel mode")
                        else:
                            raise ValueError(f"Unknown response type: {type(r)}")

                        async with asyncio.Lock():
                            with open(temp_file_name, "r+b") as f:
                                f.seek(offset_bytes)
                                f.write(chunk_data)
                                downloaded += len(chunk_data)

                            if progress:
                                if getattr(client, "_progress_lock", None) is None:
                                    client._progress_lock = asyncio.Lock()

                                async with client._progress_lock:
                                    try:
                                        if asyncio.iscoroutinefunction(progress):
                                            await progress(downloaded, file_size, *progress_args)
                                        else:
                                            progress(downloaded, file_size, *progress_args)
                                    except Exception as e:
                                        pass

                        queue.task_done()
                        break
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except Exception as e:
                        retries += 1
                        if retries >= 5:
                            logger.error(f"Failed to download part {part_idx} after 5 retries: {e}")
                            queue.task_done()
                            raise e

        with open(temp_file_name, "wb") as f:
            f.truncate(file_size)

        tasks = [asyncio.create_task(worker()) for _ in range(min(workers, total_parts))]
        await asyncio.gather(*tasks)

        if os.path.getsize(temp_file_name) == file_size:
            os.rename(temp_file_name, file_name)
            return file_name
        else:
            raise Exception("File size mismatch after download")

    except Exception as e:
        logger.warning(f"Fast download failed: {e}")
        if 'temp_file_name' in locals() and os.path.exists(temp_file_name):
            os.remove(temp_file_name)
        raise
