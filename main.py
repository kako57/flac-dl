#!/usr/bin/env python3

import requests
import re
import json
import html
import os
import sys
from pprint import pprint
from multiprocessing import Pool
from stem import Signal
from stem.control import Controller
from mutagen.flac import FLAC, Picture

search_result_regex = re.compile("gtm-data='.*' href=\"/fi-en/album.*\" title=\".*\"")
album_track_regex = re.compile("data-track-v2=.*}\"")
album_art_regex = re.compile("class=\"album-cover__image\" src=\".*\" alt")

album_request_headers = {
    'authority': 'www.qobuz.com',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'accept-language': 'en-CA,en;q=0.9',
    'cache-control': 'max-age=0',
    'dnt': '1',
    'sec-ch-ua': '"Not?A_Brand";v="8", "Chromium";v="108", "Google Chrome";v="108"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'none',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
}

# add tor proxy
proxies = {
    'http': 'socks5h://localhost:9050',
    'https': 'socks5h://localhost:9050'
}

def new_tor_ip():
    with Controller.from_port(port=9051) as controller: # type: ignore
        controller.authenticate(password=os.environ["TOR_PASSWORD"])
        controller.signal(Signal.NEWNYM) # type: ignore

def download_file(track_id, filename):
    url = f"http://de2.bot-hosting.net:5515/qobuz/track/{track_id}"

    # every now and then, print a progress message, so we know the script is still running
    print(f"Downloading {filename}")
    r = requests.get(url, proxies=proxies, stream=True)
    with open(filename, 'wb+') as f:
        for chunk in r.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
                # print("\rDownloaded {f.tell()} bytes of {filename}", end="")
                # clear line with ANSI escape, then print progress
                print(f"\033[2K\rDownloaded {f.tell()} bytes of {filename}", end="")

    print(f"\033[2K\rFinished downloading {filename}")
    return filename

def get_album_info(album_id):
    url = f"https://www.qobuz.com/fi-en/album/{album_id}"
    response = requests.get(url, headers=album_request_headers, proxies=proxies)

    # parse html; search for regex "data-track-v2=.*}\""
    # get all occurrences
    search_entries = album_track_regex.findall(response.text)

    # each entry in search_entries is a string like this:
    # data-track-v2="{&quot;key&quot;:&quot;value&quot;}"
    # we want to get the json string and parse it as json
    result = []

    for entry in search_entries:
        # get the json string
        json_string = entry.split("=")[1][1:-1]
        # the json string is escaped to be valid HTML, so we need to unescape it
        json_string = html.unescape(json_string)
        track_object = json.loads(json_string)

        result.append(track_object)
    
    if not result:
        return [], ""

    # get album art
    search_entries = album_art_regex.findall(response.text)
    # the link to the album art is after album-cover__image="" inside src="" before alt=""
    album_art = search_entries[0].split('src="')[1][:-1]
    album_art = album_art[:album_art.find('"')]

    assert album_art.endswith(".jpg"), "Album art is not a jpg"

    return result, album_art

def update_track_info(tracks, track_files, album_art):
    p = Picture()
    p.type = 3
    p.mime = "image/jpeg"
    p.data = requests.get(album_art, proxies=proxies).content
    p.desc = "Cover"


    for i in range(len(tracks)):
        track = tracks[i]
        track_file = track_files[i]
        audio = FLAC(track_file)
        audio["TITLE"] = track["item_name"]
        audio["ARTIST"] = track["item_brand"]
        audio["ALBUM"] = track["item_category"]
        audio["PUBLISHER"] = track["item_category2"]
        audio.pprint()
        audio.add_picture(p)

        audio["TRACKNUMBER"] = str(i + 1)
        audio["TRACKTOTAL"] = str(len(tracks))
        print(audio.pprint())
        audio.save()


def download_album_tracks(album_id):
    tracks, album_art = get_album_info(album_id)

    if not tracks:
        print("No tracks found")
        return

    bitrate_and_depth = tracks[0]["item_variant_max"]
    album_artist = tracks[0]["item_brand"]
    album_title = tracks[0]["item_category"]

    print(f"Downloading {album_title} by {album_artist} in {bitrate_and_depth} format")

    # create folder to store the tracks
    folder_name = f"{album_artist} - {album_title}"
    folder_name = folder_name.replace("/", "-")
    os.makedirs(folder_name, exist_ok=True)

    # save current working directory
    cwd = os.getcwd()

    os.chdir(folder_name)

    track_files = []

    # download in sequence
    for i, track in enumerate(tracks, 1):
        track_files.append(download_file(track["item_id"], f"{i:02d}. {track['item_name']}.flac"))
    
    update_track_info(tracks, track_files, album_art)

    # go back to original working directory
    os.chdir(cwd)


def search_album(query_string):
    url = f"https://www.qobuz.com/fi-en/search?q={query_string}"
    response = requests.get(url, proxies=proxies)

    # parse html; search for regex "gtm-data='.*' href=\"/fi-en/album.*\" title=\".*\""
    # get all occurrences
    search_entries = search_result_regex.findall(response.text)

    # each entry in entries is a string like this:
    # "gtm-data='{"product":{"id":"abcde12345","album":"Album Name","artistName":"Artist Name"}}' href=\"/fi-en/album/album-name/123456789\" title=\"Album Name\""
    # we want to extract the album id, album name and artist name
    result = []

    for entry in search_entries:
        # get gtm-data. this is a json string
        gtm_data = entry.split("'")[1]

        # parse json
        gtm_data = json.loads(gtm_data)

        album_id = gtm_data["product"]["id"]
        album_name = gtm_data["product"]["album"]
        artist_name = gtm_data["product"]["artistName"]
        result.append((album_id, album_name, artist_name))

    return result

if __name__ == "__main__":
    new_tor_ip()

    # get search query from command line
    query = sys.argv[1]
    result = search_album(query)

    if not result:
        print("No results found")
        exit(0)

    # print the search results and add a number in front of each result
    for i, r in enumerate(result, 1):
        print(f"{i}: {r[1]}\n")

    # ask user to select an album
    album_index = int(input(f"Select an album: "))

    if album_index > len(result):
        print("Invalid album index")
        exit(1)

    album_id = result[album_index - 1][0]
    download_album_tracks(album_id)
