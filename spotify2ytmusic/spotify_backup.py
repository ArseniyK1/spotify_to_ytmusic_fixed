#!/usr/bin/env python3
#
#  This file is licensed under the MIT license
#  This file originates from https://github.com/caseychu/spotify-backup

import base64
import codecs
import hashlib
import html
import http.client
import http.server
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


class SpotifyAPI:
    """Class to interact with the Spotify API using an OAuth token."""

    BASE_URL = "https://api.spotify.com/v1/"

    def __init__(self, auth):
        self._auth = auth

    def get(self, url, params={}, tries=3):
        """Fetch a resource from Spotify API."""
        url = self._construct_url(url, params)
        for _ in range(tries):
            try:
                req = self._create_request(url)
                return self._read_response(req)
            except Exception as err:
                print(f"Error fetching URL {url}: {err}")
                time.sleep(2)
        sys.exit("Failed to fetch data from Spotify API after retries.")

    def list(self, url, params={}):
        """Fetch paginated resources and return as a combined list."""
        response = self.get(url, params)
        items = response["items"]

        while response["next"]:
            response = self.get(response["next"])
            items += response["items"]
        return items

    @staticmethod
    def _pkce_verifier():
        """RFC 7636: 43–128 URL-safe characters."""
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def authorize(client_id, scope):
        """Open a browser for user authorization and return SpotifyAPI instance."""
        redirect_uri = f"http://127.0.0.1:{SpotifyAPI._SERVER_PORT}/redirect"
        code_verifier = SpotifyAPI._pkce_verifier()
        state = secrets.token_urlsafe(16)
        url = SpotifyAPI._construct_auth_url(
            client_id, scope, redirect_uri, code_verifier, state
        )
        print(f"Open this link if the browser doesn't open automatically: {url}")
        webbrowser.open(url)

        server = SpotifyAPI._AuthorizationServer(
            "127.0.0.1", SpotifyAPI._SERVER_PORT, code_verifier, state, client_id, redirect_uri
        )
        try:
            while True:
                server.handle_request()
        except SpotifyAPI._Authorization as auth:
            return SpotifyAPI(auth.access_token)

    @staticmethod
    def _construct_auth_url(client_id, scope, redirect_uri, code_verifier, state):
        return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "scope": scope,
                "redirect_uri": redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": SpotifyAPI._pkce_challenge(code_verifier),
                "state": state,
            }
        )

    def _construct_url(self, url, params):
        """Construct a full API URL."""
        if not url.startswith(self.BASE_URL):
            url = self.BASE_URL + url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return url

    def _create_request(self, url):
        """Create an authenticated request."""
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self._auth}")
        return req

    def _read_response(self, req):
        """Read and parse the response."""
        with urllib.request.urlopen(req) as res:
            reader = codecs.getreader("utf-8")
            return json.load(reader(res))

    _SERVER_PORT = 43019

    class _AuthorizationServer(http.server.HTTPServer):
        def __init__(self, host, port, code_verifier, state, client_id, redirect_uri):
            self.code_verifier = code_verifier
            self.state = state
            self.client_id = client_id
            self.redirect_uri = redirect_uri
            super().__init__((host, port), SpotifyAPI._AuthorizationHandler)

        def handle_error(self, request, client_address):
            raise

    class _AuthorizationHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/redirect":
                self.send_error(404)
                return
            self._handle_redirect_callback()

        def _handle_redirect_callback(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if qs.get("error"):
                desc = (qs.get("error_description") or qs.get("error") or ["unknown"])[0]
                self._send_html(
                    400,
                    f"Spotify: {html.escape(desc)}",
                )
                return
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            if not code or state != self.server.state:
                self._send_html(400, "Invalid authorization response (missing code or state).")
                return
            try:
                access_token = self._exchange_code_for_token(code)
            except Exception as e:
                self._send_html(500, f"Token exchange failed: {html.escape(str(e))}")
                return
            self._send_html(200, "Thanks! You may now close this window.")
            raise SpotifyAPI._Authorization(access_token)

        def _exchange_code_for_token(self, code):
            body = urllib.parse.urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.server.redirect_uri,
                    "client_id": self.server.client_id,
                    "code_verifier": self.server.code_verifier,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                "https://accounts.spotify.com/api/token",
                data=body,
                method="POST",
            )
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req) as res:
                payload = json.load(codecs.getreader("utf-8")(res))
            if "access_token" not in payload:
                raise RuntimeError(payload.get("error_description", payload))
            return payload["access_token"]

        def _send_html(self, status, body_text):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            page = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>Spotify</title></head><body><p>"
                + body_text
                + "</p></body></html>"
            )
            self.wfile.write(page.encode("utf-8"))

        def log_message(self, format, *args):
            pass

    class _Authorization(Exception):
        def __init__(self, access_token):
            self.access_token = access_token


def fetch_user_data(spotify, dump):
    """Fetch playlists and liked songs based on the dump parameter."""
    playlists = []
    liked_albums = []

    if "liked" in dump:
        print("Loading liked albums and songs...")
        liked_tracks = spotify.list("me/tracks", {"limit": 50})
        liked_albums = spotify.list("me/albums", {"limit": 50})
        playlists.append({"name": "Liked Songs", "tracks": liked_tracks})

    if "playlists" in dump:
        print("Loading playlists...")
        playlist_data = spotify.list("me/playlists", {"limit": 50})
        for playlist in playlist_data:
            print(f"Loading playlist: {playlist['name']}")
            playlist["tracks"] = spotify.list(
                playlist["tracks"]["href"], {"limit": 100}
            )
        playlists.extend(playlist_data)

    return playlists, liked_albums


def write_to_file(file, format, playlists, liked_albums):
    """Write fetched data to a file in the specified format."""
    print(f"Writing to {file}...")
    with open(file, "w", encoding="utf-8") as f:
        if format == "json":
            json.dump({"playlists": playlists, "albums": liked_albums}, f)
        else:
            for playlist in playlists:
                f.write(playlist["name"] + "\r\n")
                for track in playlist["tracks"]:
                    if track["track"]:
                        f.write(
                            "{name}\t{artists}\t{album}\t{uri}\t{release_date}\r\n".format(
                                uri=track["track"]["uri"],
                                name=track["track"]["name"],
                                artists=", ".join(
                                    [
                                        artist["name"]
                                        for artist in track["track"]["artists"]
                                    ]
                                ),
                                album=track["track"]["album"]["name"],
                                release_date=track["track"]["album"]["release_date"],
                            )
                        )
                f.write("\r\n")


def main(dump="playlists,liked", format="json", file="playlists.json", token=""):
    print("Starting backup...")
    spotify = (
        SpotifyAPI(token)
        if token
        else SpotifyAPI.authorize(
            client_id="5c098bcc800e45d49e476265bc9b6934",
            scope="playlist-read-private playlist-read-collaborative user-library-read",
        )
    )

    playlists, liked_albums = fetch_user_data(spotify, dump)
    write_to_file(file, format, playlists, liked_albums)
    print(f"Backup completed! Data written to {file}")


if __name__ == "__main__":
    main()
