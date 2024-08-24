"""Module for external APIs."""

import logging

import musicbrainzngs

import usdb_syncer.constants


musicbrainzngs_logger = logging.getLogger("musicbrainzngs")
musicbrainzngs_logger.setLevel(logging.ERROR)


class API:
    """Base class for external APIs."""

    BASE_URL: str


class MusicBrainzAPI(API):
    """Wrapper for the MusicBrainz API."""

    BASE_URL = "https://musicbrainz.org/ws/2"
    RATE_LIMIT = 1

    USER_AGENT = "usdb_syncer"
    VERSION = usdb_syncer.constants.VERSION
    CONTACT = "github.com/bohning/usdb_syncer"

    def __init__(self) -> None:
        super().__init__()
        musicbrainzngs.set_useragent(
            self.USER_AGENT, self.VERSION, contact=self.CONTACT
        )
        musicbrainzngs.set_rate_limit(1, self.RATE_LIMIT)

    def get_song(self, artist: str, title: str) -> dict | None:
        """Search for a song on MusicBrainz and return the first result."""
        results = musicbrainzngs.search_release_groups(query=artist + " " + title)
        if not results["release-group-list"]:
            return None
        for song in results["release-group-list"]:
            # Verify that the artist is in the artist-credit
            artist_credit: list[dict]
            for artist_credit in song["artist-credit"]:
                if not isinstance(artist_credit, dict):
                    continue
                if artist in artist_credit["name"] or artist_credit["name"] in artist:
                    break
            else:
                continue
            break
        else:
            return None
        return song


_musicbrainzapi = MusicBrainzAPI()


def get_musicbrainz_api() -> MusicBrainzAPI:
    return _musicbrainzapi
