"""Functionality related to the usdb.animux.de web page."""

import logging
import re
import urllib.parse
from datetime import datetime
from enum import Enum
from functools import wraps
from json import JSONEncoder
from typing import Any, Callable, NoReturn

import requests
from bs4 import BeautifulSoup

from usdb_dl import SongId

_logger: logging.Logger = logging.getLogger(__file__)

USDB_BASE_URL = "http://usdb.animux.de/"
DATASET_NOT_FOUND_STRING = "Datensatz nicht gefunden"
USDB_DATETIME_STRF = "%d.%m.%y - %H:%M"


class RequestMethod(Enum):
    """Supported HTTP requests."""

    GET = "GET"
    POST = "POST"


class ParseException(Exception):
    """Raised when HTML from USDB has unexpected format."""


def raises_parse_exception(func: Callable) -> Callable:
    """Converts certain errors of annotated functions that indicate wrong assumptions
    about the parsed HTML into ParseErrors.
    This can be used with '# type: ignore' and an outer try-except clause to parse HTML
    concisely, but safely.
    """

    @wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (AttributeError, IndexError, ValueError) as exception:
            # AttributeError: not existing attribute (e.g. because the object is None)
            # IndexError: list index out of bounds
            # ValueError: too many values to unpack
            raise ParseException from exception

    return wrapped


def assert_never(value: NoReturn) -> NoReturn:
    """Used to get a static type error if an enum isn't matched exhaustively.

    After we upgrade to Python 3.11, we can use typing.assert_never."""
    assert False, f"Unhandled type: {type(value).__name__}"


class SongMeta:
    """Meta data about a song that USDB shows in the result list."""

    song_id: SongId
    artist: str
    title: str
    language: str
    edition: str
    golden_notes: bool
    rating: int
    views: int

    def __init__(
        self,
        *,
        song_id: int | str,
        artist: str,
        title: str,
        language: str,
        edition: str,
        golden_notes: bool | str,
        rating: int | str,
        views: int | str,
    ) -> None:
        """This constructor accepts both, strings as scraped from USDB, and already
        parsed values as stored in a JSON file.
        """
        self.song_id = SongId(song_id)
        self.artist = artist
        self.title = title
        self.language = language or "language_not_set"
        self.edition = edition
        self.golden_notes = (
            golden_notes if isinstance(golden_notes, bool) else golden_notes == "Yes"
        )
        self.rating = rating if isinstance(rating, int) else rating.count("star.png")
        self.views = int(views)

    def rating_str(self) -> str:
        return self.rating * "★"  # + (5-rating) * "☆"


class SongMetaEncoder(JSONEncoder):
    """Custom JSON encoder"""

    def default(self, o: Any) -> Any:
        if isinstance(o, SongMeta):
            return o.__dict__
        if isinstance(o, SongId):
            return int(o)
        return super().default(o)


class CommentContents:
    """The parsed contents of a SongComment."""

    text: str
    youtube_ids: list[str]
    urls: list[str]

    def __init__(
        self,
        *,
        text: str,
        youtube_ids: list[str],
        urls: list[str],
    ) -> None:
        self.text = text
        self.youtube_ids = youtube_ids
        self.urls = urls


class SongComment:
    """A comment to a song on USDB."""

    date_time: datetime
    author: str
    contents: CommentContents

    def __init__(
        self,
        *,
        date_time: str,
        author: str,
        contents: CommentContents,
    ) -> None:
        self.date_time = datetime.strptime(date_time, USDB_DATETIME_STRF)
        self.author = author
        self.contents = contents


class SongDetails:
    """Details about a song that USDB shows on a song's page, or are specified in the
    comment section."""

    song_id: SongId
    artist: str
    title: str
    cover_url: str | None
    bpm: float
    gap: float
    golden_notes: bool
    song_check: bool
    date_time: datetime
    uploader: str
    editors: list[str]
    views: int
    rating: int
    votes: int
    audio_sample: str | None
    team_comment: str | None
    comments: list[SongComment]

    def __init__(  # pylint: disable=too-many-locals
        self,
        *,
        song_id: SongId,
        artist: str,
        title: str,
        cover_url: str,
        bpm: str,
        gap: str,
        golden_notes: str,
        song_check: str,
        date_time: str,
        uploader: str,
        editors: list[str],
        views: str,
        rating: int,
        votes: str,
        audio_sample: str,
        team_comment: str,
    ) -> None:
        self.song_id = song_id
        self.artist = artist
        self.title = title
        self.cover_url = None if "nocover" in cover_url else USDB_BASE_URL + cover_url
        self.bpm = float(bpm.replace(",", "."))
        self.gap = float(gap.replace(",", "."))
        self.golden_notes = "Yes" in golden_notes
        self.song_check = "Yes" in song_check
        self.date_time = datetime.strptime(date_time, USDB_DATETIME_STRF)
        self.uploader = uploader
        self.editors = editors
        self.views = int(views)
        self.rating = rating
        self.votes = int(votes)
        self.audio_sample = audio_sample or None
        self.team_comment = None if "No comment yet" in team_comment else team_comment
        self.comments = []


def get_usdb_page(
    rel_url: str,
    method: RequestMethod = RequestMethod.GET,
    headers: dict[str, str] | None = None,
    payload: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> str:
    """Retrieve html subpage from usbd.

    Parameters:
        rel_url: relative url of page to retrieve
        method: GET or POST
        headers: dict of headers to send with request
        payload: dict of data to send with request
        params: dict of params to send with request
    """
    # wildcard login
    _headers = {"Cookie": "PHPSESSID"}
    if headers:
        _headers.update(headers)

    url = USDB_BASE_URL + rel_url

    match method:
        case RequestMethod.GET:
            _logger.debug("get request for %s", url)
            response = requests.get(url, headers=_headers, params=params, timeout=60)
        case RequestMethod.POST:
            _logger.debug("post request for %s", url)
            response = requests.post(
                url, headers=_headers, data=payload, params=params, timeout=60
            )
        case _ as unreachable:
            assert_never(unreachable)

    response.raise_for_status()
    response.encoding = response.encoding = "utf-8"
    return response.text


def get_usdb_details(song_id: SongId) -> SongDetails | None:
    """Retrieve song details from usdb webpage, if song exists.

    Parameters:
        song_id: id of song to retrieve details for
    """
    html = get_usdb_page(
        "index.php", params={"id": str(int(song_id)), "link": "detail"}
    )
    soup = BeautifulSoup(html, "lxml")
    if DATASET_NOT_FOUND_STRING in soup.get_text():
        return None
    return _parse_song_page(soup, song_id)


def _parse_song_page(soup: BeautifulSoup, song_id: SongId) -> SongDetails:
    details_table, comments_table, *_ = soup.find_all("table", border="0", width="500")
    details = _parse_details_table(details_table, song_id)
    details.comments = _parse_comments_table(comments_table)
    return details


def get_usdb_available_songs(
    content_filter: dict[str, str] | None = None
) -> list[SongMeta]:
    """Return a list of all available songs.

    Parameters:
        content_filter: filters response (e.g. {'artist': 'The Beatles'})
    """
    payload = {"limit": "50000", "order": "id", "ud": "desc"}
    payload.update(content_filter or {})

    html = get_usdb_page(
        "index.php", RequestMethod.POST, params={"link": "list"}, payload=payload
    )

    regex = (
        r'<td onclick="show_detail\((\d+)\)">(.*)</td>\n'
        r'<td onclick="show_detail\(\d+\)">(.*)</td>\n'
        r'<td onclick="show_detail\(\d+\)">(.*)</td>\n'
        r'<td onclick="show_detail\(\d+\)">(.*)</td>\n'
        r'<td onclick="show_detail\(\d+\)">(.*)</td>\n'
        r'<td onclick="show_detail\(\d+\)">(.*)</td>\n'
        r'<td onclick="show_detail\(\d+\)">(.*)</td>'
    )
    matches = re.findall(regex, html)

    available_songs = [
        SongMeta(
            song_id=match[0],
            artist=match[1],
            title=match[2],
            edition=match[3],
            golden_notes=match[4],
            language=match[5],
            rating=match[6],
            views=match[7],
        )
        for match in matches
    ]
    _logger.info(f"fetched {len(available_songs)} available songs")
    return available_songs


def _parse_details_table(details_table: BeautifulSoup, song_id: SongId) -> SongDetails:
    """Parse song attributes from usdb page.

    Parameters:
        details: dict of song attributes
        details_table: BeautifulSoup object of song details table
    """
    editors = []
    pointer = details_table.find(text="Song edited by:")
    while pointer is not None:
        pointer = pointer.find_next("td")
        if pointer.a is None:  # type: ignore
            break
        editors.append(pointer.text.strip())  # type: ignore
        pointer = pointer.find_next("tr")  # type: ignore

    stars = details_table.find(text="Rating").next.find_all("img")  # type: ignore

    audio_sample = ""
    if param := details_table.find("param", attrs={"name": "FlashVars"}):
        flash_vars = urllib.parse.parse_qs(param.get("value"))  # type: ignore
        audio_sample = flash_vars["soundFile"][0]

    return SongDetails(
        song_id=song_id,
        artist=details_table.find_next("td").text,  # type: ignore
        title=details_table.find_next("td").find_next("td").text,  # type: ignore
        cover_url=details_table.img["src"],  # type: ignore
        bpm=details_table.find(text="BPM").next.text,  # type: ignore
        gap=details_table.find(text="GAP").next.text,  # type: ignore
        golden_notes=details_table.find(text="Golden Notes").next.text,  # type: ignore
        song_check=details_table.find(text="Songcheck").next.text,  # type: ignore
        date_time=details_table.find(text="Date").next.text,  # type: ignore
        uploader=details_table.find(text="Created by").next.text,  # type: ignore
        editors=editors,
        views=details_table.find(text="Views").next.text,  # type: ignore
        rating=sum("star.png" in s.get("src") for s in stars),
        votes=details_table.find(text="Rating").next_element.text.split("(")[1].split(")")[0],  # type: ignore
        audio_sample=audio_sample,
        # only captures first team comment (example of multiple needed!)
        team_comment=details_table.find(text="Team Comment").next.text,  # type: ignore
    )


def _parse_comments_table(comments_table: BeautifulSoup) -> list[SongComment]:
    """Parse the table into individual comments, extracting potential video links,
    GAP and BPM values.

    Parameters:
        details: dict of song attributes
        comments_table: BeautifulSoup object of song details table
    """
    comments = []
    # last entry is the field to enter a new comment, so this one is ignored
    for header in comments_table.find_all("tr", class_="list_tr2")[:-1]:
        meta = header.find("td").text.strip()
        if " | " not in meta:
            # header is just the placeholder element
            _logger.debug("\t- usdb::song has no comments!")
            break
        date_time, author = meta.split(" | ")
        contents = _parse_comment_contents(header.next_sibling)
        comments.append(
            SongComment(
                date_time=date_time,
                author=author,
                contents=contents,
            )
        )

    return comments


def _parse_comment_contents(contents: BeautifulSoup) -> CommentContents:
    text = contents.find("td").text.strip()  # type: ignore
    urls = []
    for embed in contents.find_all("embed"):
        # TODO: this assumes youtube embeds
        yt_url = embed.get("src").split("&")[0]
        urls.append(yt_url)
        # try:
        #    yt_id = extract.video_id(yt_url)
        # except:
        #    _logger.warning(
        #        f"\t- usdb::comment embed contains a url ({yt_url}), "
        #        "but the Youtube video ID could not be extracted."
        #    )
        # else:
        #    # TODO: this only takes the first youtube link in the newest comments
        #    details["video_params"] = {"v": yt_id}
    # regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
    # for url in re.findall(regex, text):
    #     try:
    #        yt_id = extract.video_id(url[0])
    #     except:
    #        _logger.warning(
    #            f"\t- usdb::comment contains a plain url ({url}), "
    #            "but it does not seem to be a Youtube link."
    #        )
    #     else:
    #        comment_urls.append(f"https://www.youtube.com/watch?v={yt_id}")
    #        if not details.get("video_params"):
    #            details["video_params"] = {"v": yt_id}
    #        text = text.replace(url[0], "").strip()
    return CommentContents(
        text=text,
        urls=urls,
        youtube_ids=[],
    )


def get_notes(song_id: SongId) -> str:
    """Retrieve notes for a song."""
    _logger.debug(f"\t- fetch notes for song {song_id}")
    html = get_usdb_page(
        "index.php",
        RequestMethod.POST,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        params={"link": "gettxt", "id": str(int(song_id))},
        payload={"wd": "1"},
    )
    soup = BeautifulSoup(html, "lxml")
    return _parse_song_txt_from_txt_page(soup)


def _parse_song_txt_from_txt_page(soup: BeautifulSoup) -> str:
    return soup.find("textarea").string.replace("<", "(").replace(">", ")")  # type: ignore
