"""The clients that interact with the streaming service APIs."""

import base64
import binascii
import concurrent.futures
import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Optional, Sequence, Tuple, Union

from click import launch, secho
from Cryptodome.Cipher import AES

from .constants import (
    AGENT,
    AVAILABLE_QUALITY_IDS,
    QOBUZ_BASE,
    QOBUZ_FEATURED_KEYS,
)
from .exceptions import (
    AuthenticationError,
    IneligibleError,
    InvalidAppIdError,
    InvalidAppSecretError,
    InvalidQuality,
    MissingCredentials,
    NonStreamable,
)
from .spoofbuz import Spoofer
from .utils import gen_threadsafe_session, get_quality, safe_get

logger = logging.getLogger("streamrip")


class Client(ABC):
    """Common API for clients of all platforms.

    This is an Abstract Base Class. It cannot be instantiated;
    it is merely a template.
    """

    source: str
    max_quality: int
    logged_in: bool

    @abstractmethod
    def login(self, **kwargs):
        """Authenticate the client.

        :param kwargs:
        """
        pass

    @abstractmethod
    def search(self, query: str, media_type="album"):
        """Search API for query.

        :param query:
        :type query: str
        :param type_:
        """
        pass

    @abstractmethod
    def get(self, item_id, media_type="album"):
        """Get metadata.

        :param meta_id:
        :param type_:
        """
        pass

    @abstractmethod
    def get_file_url(self, track_id, quality=3) -> dict:
        """Get the direct download url dict for a file.

        :param track_id: id of the track
        """
        pass


class QobuzClient(Client):
    """QobuzClient."""

    source = "qobuz"
    max_quality = 4

    # ------- Public Methods -------------
    def __init__(self):
        """Create a QobuzClient object."""
        self.logged_in = False

    def login(self, **kwargs):
        """Authenticate the QobuzClient. Must have a paid membership.

        If `app_id` and `secrets` are not provided, this will run the
        Spoofer script, which retrieves them. This will take some time,
        so it is recommended to cache them somewhere for reuse.

        :param email_or_userid: user ID for the qobuz account
        :type email_or_userid: str
        :param password_or_token: auth token for the qobuz account
        :type password_or_token: str
        :param use_auth_token: if True, use user_id + auth token login
        :type use_auth_token: bool
        :param kwargs: app_id: str, secrets: list, return_secrets: bool
        """
        secho(f"Logging into {self.source}", fg="green")
        email_or_userid: str = kwargs.get("email_or_userid") or kwargs.get("email", "")
        password_or_token: str = kwargs.get("password_or_token") or kwargs.get("pwd", "")
        use_auth_token: bool = kwargs.get("use_auth_token", False)
        if not email_or_userid or not password_or_token:
            raise MissingCredentials

        if self.logged_in:
            logger.debug("Already logged in")
            return

        if not kwargs.get("app_id") or not kwargs.get("secrets"):
            self._get_app_id_and_secrets()  # can be async
        else:
            self.app_id, self.secrets = (
                str(kwargs["app_id"]),
                kwargs["secrets"],
            )
            self.session = gen_threadsafe_session(
                headers={"User-Agent": AGENT, "X-App-Id": self.app_id}
            )
            self._validate_secrets()

        self._api_login(email_or_userid, password_or_token, use_auth_token)
        logger.debug("Logged into Qobuz")
        logger.debug("Qobuz client is ready to use")

        self.logged_in = True

    def get_tokens(self) -> Tuple[str, Sequence[str]]:
        """Return app id and secrets.

        These can be saved and reused.

        :rtype: Tuple[str, Sequence[str]]
        """
        return self.app_id, self.secrets

    def search(
        self, query: str, media_type: str = "album", limit: int = 500
    ) -> Generator:
        """Search the qobuz API.

        If 'featured' is given as media type, this will retrieve results
        from the featured albums in qobuz. The queries available with this type
        are:

            * most-streamed
            * recent-releases
            * best-sellers
            * press-awards
            * ideal-discography
            * editor-picks
            * most-featured
            * qobuzissims
            * new-releases
            * new-releases-full
            * harmonia-mundi
            * universal-classic
            * universal-jazz
            * universal-jeunesse
            * universal-chanson

        :param query:
        :type query: str
        :param media_type:
        :type media_type: str
        :param limit:
        :type limit: int
        :rtype: Generator
        """
        return self._api_search(query, media_type, limit)

    def get(self, item_id: Union[str, int], media_type: str = "album") -> dict:
        """Get an item from the API.

        :param item_id:
        :type item_id: Union[str, int]
        :param media_type:
        :type media_type: str
        :rtype: dict
        """
        resp = self._api_get(media_type, item_id=item_id)
        logger.debug(resp)
        return resp

    def get_file_url(self, item_id, quality=3) -> dict:
        """Get the downloadble file url for a track.

        :param item_id:
        :param quality:
        :rtype: dict
        """
        return self._api_get_file_url(item_id, quality=quality)

    # ---------- Private Methods ---------------

    def _get_app_id_and_secrets(self):
        if not hasattr(self, "app_id") or not hasattr(self, "secrets"):
            spoofer = Spoofer()
            self.app_id, self.secrets = (
                str(spoofer.get_app_id()),
                spoofer.get_secrets(),
            )

        if not hasattr(self, "sec"):
            if not hasattr(self, "session"):
                self.session = gen_threadsafe_session(
                    headers={"User-Agent": AGENT, "X-App-Id": self.app_id}
                )
            self._validate_secrets()

    def _gen_pages(self, epoint: str, params: dict) -> Generator:
        """When there are multiple pages of results, this yields them.

        :param epoint:
        :type epoint: str
        :param params:
        :type params: dict
        :rtype: dict
        """
        page, status_code = self._api_request(epoint, params)
        logger.debug("Keys returned from _gen_pages: %s", ", ".join(page.keys()))
        key = epoint.split("/")[0] + "s"
        total = page.get(key, {})
        total = total.get("total") or total.get("items")

        if not total:
            logger.debug("Nothing found from %s epoint", epoint)
            return

        limit = page.get(key, {}).get("limit", 500)
        offset = page.get(key, {}).get("offset", 0)
        params.update({"limit": limit})
        yield page
        while (offset + limit) < total:
            offset += limit
            params.update({"offset": offset})
            page, status_code = self._api_request(epoint, params)
            yield page

    def _validate_secrets(self):
        """Check if the secrets are usable."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(self._test_secret, secret) for secret in self.secrets
            ]

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result is not None:
                    self.sec = result
                    break

        if not hasattr(self, "sec"):
            raise InvalidAppSecretError(f"Invalid secrets: {self.secrets}")

    def _api_get(self, media_type: str, **kwargs) -> dict:
        """Request metadata from the Qobuz API.

        :param media_type:
        :type media_type: str
        :param kwargs:
        :rtype: dict
        """
        item_id = kwargs.get("item_id")

        params = {
            "app_id": self.app_id,
            f"{media_type}_id": item_id,
            "limit": kwargs.get("limit", 500),
            "offset": kwargs.get("offset", 0),
        }
        extras = {
            "artist": "albums",
            "playlist": "tracks",
            "label": "albums",
        }

        if media_type in extras:
            params.update({"extra": extras[media_type]})

        logger.debug("request params: %s", params)

        epoint = f"{media_type}/get"

        response, status_code = self._api_request(epoint, params)
        if status_code != 200:
            raise Exception(f'Error fetching metadata. "{response["message"]}"')

        return response

    def _api_search(self, query: str, media_type: str, limit: int = 500) -> Generator:
        """Send a search request to the API.

        :param query:
        :type query: str
        :param media_type:
        :type media_type: str
        :param limit:
        :type limit: int
        :rtype: Generator
        """
        params = {
            "query": query,
            "limit": limit,
        }
        # TODO: move featured, favorites, and playlists into _api_get later
        if media_type == "featured":
            assert query in QOBUZ_FEATURED_KEYS, f'query "{query}" is invalid.'
            params.update({"type": query})
            del params["query"]
            epoint = "album/getFeatured"

        elif query == "user-favorites":
            assert query in ("track", "artist", "album")
            params.update({"type": f"{media_type}s"})
            epoint = "favorite/getUserFavorites"

        elif query == "user-playlists":
            epoint = "playlist/getUserPlaylists"

        else:
            epoint = f"{media_type}/search"

        return self._gen_pages(epoint, params)

    def _api_login(self, email_or_userid: str, password_or_token: str, use_auth_token: bool = False):
        """Log into the api to get the user authentication token.

        :param email_or_userid: user ID
        :type email_or_userid: str
        :param password_or_token: auth token
        :type password_or_token: str
        :param use_auth_token: if True, use user_id + auth token login
        :type use_auth_token: bool
        """
        if use_auth_token:
            params = {
                "user_id": email_or_userid,
                "user_auth_token": password_or_token,
                "app_id": self.app_id,
            }
        else:
            params = {
                "email": email_or_userid,
                "password": password_or_token,
                "app_id": self.app_id,
            }
        epoint = "user/login"
        resp, status_code = self._api_request(epoint, params)

        if status_code == 401:
            raise AuthenticationError(f"Invalid credentials from params {params}")
        elif status_code == 400:
            logger.debug(resp)
            raise InvalidAppIdError(f"Invalid app id from params {params}")
        else:
            logger.info("Logged in to Qobuz")

        if not resp["user"]["credential"]["parameters"]:
            raise IneligibleError("Free accounts are not eligible to download tracks.")

        self.uat = resp["user_auth_token"]
        self.session.headers.update({"X-User-Auth-Token": self.uat})
        self.label = resp["user"]["credential"]["parameters"]["short_label"]

    def _api_get_file_url(
        self, track_id: Union[str, int], quality: int = 3, sec: Optional[str] = None
    ) -> dict:
        """Get the file url given a track id.

        :param track_id:
        :type track_id: Union[str, int]
        :param quality:
        :type quality: int
        :param sec: only used to check whether a specific secret is valid.
        If it is not provided, it is set to `self.sec`.
        :type sec: str
        :rtype: dict
        """
        unix_ts = time.time()

        if int(quality) not in AVAILABLE_QUALITY_IDS:  # Needed?
            raise InvalidQuality(
                f"Invalid quality id {quality}. Choose from {AVAILABLE_QUALITY_IDS}"
            )

        if sec is not None:
            secret = sec
        elif hasattr(self, "sec"):
            secret = self.sec
        else:
            raise InvalidAppSecretError("Cannot find app secret")

        quality = int(get_quality(quality, self.source))  # type: ignore
        r_sig = f"trackgetFileUrlformat_id{quality}intentstreamtrack_id{track_id}{unix_ts}{secret}"
        logger.debug("Raw request signature: %s", r_sig)
        r_sig_hashed = hashlib.md5(r_sig.encode("utf-8")).hexdigest()
        logger.debug("Hashed request signature: %s", r_sig_hashed)

        params = {
            "request_ts": unix_ts,
            "request_sig": r_sig_hashed,
            "track_id": track_id,
            "format_id": quality,
            "intent": "stream",
        }
        response, status_code = self._api_request("track/getFileUrl", params)
        if status_code == 400:
            raise InvalidAppSecretError("Invalid app secret from params %s" % params)

        return response

    def _api_request(self, epoint: str, params: dict) -> Tuple[dict, int]:
        """Send a request to the API.

        :param epoint:
        :type epoint: str
        :param params:
        :type params: dict
        :rtype: Tuple[dict, int]
        """
        logging.debug(f"Calling API with endpoint {epoint} params {params}")
        r = self.session.get(f"{QOBUZ_BASE}/{epoint}", params=params)
        try:
            logger.debug(r.text)
            return r.json(), r.status_code
        except Exception:
            logger.error("Problem getting JSON. Status code: %s", r.status_code)
            raise

    def _test_secret(self, secret: str) -> Optional[str]:
        """Test the authenticity of a secret.

        :param secret:
        :type secret: str
        :rtype: bool
        """
        try:
            self._api_get_file_url("19512574", sec=secret)
            return secret
        except InvalidAppSecretError as error:
            logger.debug("Test for %s secret didn't work: %s", secret, error)
            return None
