import asyncio
import logging
import os
import time
from tempfile import gettempdir
from typing import Callable, Dict, Iterable, Iterator, List, Optional

import aiofiles
import aiohttp
import requests.exceptions

from .exceptions import NonStreamable
from .utils import gen_threadsafe_session

logger = logging.getLogger("streamrip")

CHUNK_SIZE = 32768  # 32KB chunks for better throughput
STREAM_RETRIES = 5
STREAM_BACKOFF_FACTOR = 0.5


class DownloadStream:
    """An iterator over chunks of a stream with retry and resume support.

    Usage:

        >>> stream = DownloadStream('https://google.com', None)
        >>> with open('google.html', 'wb') as file:
        >>>     for chunk in stream:
        >>>         file.write(chunk)

    """

    def __init__(
        self,
        url: str,
        source: str = None,
        params: dict = None,
        headers: dict = None,
        item_id: str = None,
    ):
        """Create an iterable DownloadStream of a URL.

        :param url: The url to download
        :type url: str
        :param source: The source service
        :type source: str
        :param params: Parameters to pass in the request
        :type params: dict
        :param headers: Headers to pass in the request
        :type headers: dict
        :param item_id: the ID of the track
        :type item_id: str
        """
        self.source = source
        self._url = url
        self._headers = headers
        self._params = params if params is not None else {}
        self.session = gen_threadsafe_session(headers=headers)

        self.id = item_id
        if isinstance(self.id, int):
            self.id = str(self.id)

        self.request = self.session.get(
            url,
            allow_redirects=True,
            stream=True,
            params=self._params,
            timeout=(10, 30),
        )
        self.file_size = int(self.request.headers.get("Content-Length", 0))

        if self.file_size < 20000 and not self.url.endswith(".jpg"):
            import json

            try:
                info = self.request.json()
                try:
                    raise NonStreamable(f"{info['error']} - {info['message']}")
                except KeyError:
                    raise NonStreamable(info)

            except json.JSONDecodeError:
                raise NonStreamable("File not found.")

    def __iter__(self) -> Iterator[bytes]:
        """Iterate through chunks of the stream with resume on failure.

        :rtype: Iterator[bytes]
        """
        downloaded = 0
        retries_left = STREAM_RETRIES
        response = self.request

        while True:
            try:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        downloaded += len(chunk)
                        yield chunk
                # Successfully finished
                return
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout,
            ) as e:
                retries_left -= 1
                if retries_left <= 0:
                    raise NonStreamable(
                        f"Download failed after {STREAM_RETRIES} retries: {e}"
                    )

                wait = STREAM_BACKOFF_FACTOR * (2 ** (STREAM_RETRIES - retries_left - 1))
                logger.warning(
                    "Download interrupted at %d bytes, retrying in %.1fs (%d retries left): %s",
                    downloaded,
                    wait,
                    retries_left,
                    e,
                )
                time.sleep(wait)

                # Try to resume with Range header
                try:
                    resume_headers = dict(self._headers or {})
                    resume_headers["Range"] = f"bytes={downloaded}-"
                    response = self.session.get(
                        self._url,
                        allow_redirects=True,
                        stream=True,
                        params=self._params,
                        headers=resume_headers,
                        timeout=(10, 30),
                    )
                    if response.status_code == 206:
                        logger.info("Resumed download at byte %d", downloaded)
                    elif response.status_code == 200:
                        # Server doesn't support Range; restart from beginning
                        logger.warning(
                            "Server does not support Range requests, restarting download"
                        )
                        downloaded = 0
                    else:
                        raise NonStreamable(
                            f"Unexpected status {response.status_code} on resume"
                        )
                except requests.exceptions.RequestException as re_err:
                    retries_left -= 1
                    if retries_left <= 0:
                        raise NonStreamable(
                            f"Failed to reconnect after {STREAM_RETRIES} retries: {re_err}"
                        )
                    logger.warning("Reconnection failed: %s", re_err)

    @property
    def url(self):
        """Return the requested url."""
        return self.request.url

    def __len__(self) -> int:
        """Return the value of the "Content-Length" header.

        :rtype: int
        """
        return self.file_size


class DownloadPool:
    """Asynchronously download a set of urls."""

    def __init__(
        self,
        urls: Iterable,
        tempdir: str = None,
        chunk_callback: Optional[Callable] = None,
    ):
        self.finished: bool = False
        # Enumerate urls to know the order
        self.urls = dict(enumerate(urls))
        self._downloaded_urls: List[str] = []
        # {url: path}
        self._paths: Dict[str, str] = {}
        self.task: Optional[asyncio.Task] = None

        if tempdir is None:
            tempdir = gettempdir()
        self.tempdir = tempdir

    async def getfn(self, url):
        path = os.path.join(self.tempdir, f"__streamrip_partial_{abs(hash(url))}")
        self._paths[url] = path
        return path

    async def _download_urls(self):
        async with aiohttp.ClientSession() as session:
            tasks = [
                asyncio.ensure_future(self._download_url(session, url))
                for url in self.urls.values()
            ]
            await asyncio.gather(*tasks)

    async def _download_url(self, session, url):
        filename = await self.getfn(url)
        logger.debug("Downloading %s", url)
        timeout = aiohttp.ClientTimeout(total=120, sock_read=60)
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                async with session.get(url, timeout=timeout) as response, aiofiles.open(
                    filename, "wb"
                ) as f:
                    await f.write(await response.content.read())
                break
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    "Cover download attempt %d/%d failed for %s: %s",
                    attempt,
                    max_retries,
                    url,
                    e,
                )
                if attempt == max_retries:
                    logger.error("Giving up on cover download: %s", url)
                    return

        if self.callback:
            self.callback()

        logger.debug("Finished %s", url)

    def download(self, callback=None):
        self.callback = callback
        asyncio.run(self._download_urls())

    @property
    def files(self):
        if len(self._paths) != len(self.urls):
            # Not all of them have downloaded
            raise Exception("Must run DownloadPool.download() before accessing files")

        return [
            os.path.join(self.tempdir, self._paths[self.urls[i]])
            for i in range(len(self.urls))
        ]

    def __len__(self):
        return len(self.urls)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.debug("Removing tempfiles %s", self._paths)
        for file in self._paths.values():
            try:
                os.remove(file)
            except FileNotFoundError:
                pass

        return False
