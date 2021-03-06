# The MIT License (MIT)
# Copyright (c) 2020 handlerug
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
# OR OTHER DEALINGS IN THE SOFTWARE.


import calendar
import locale
import re
import textwrap
from math import ceil
from typing import Union

import requests

from octobot import Catalog, CatalogKeyArticle, OctoBot, Context, CatalogPhoto, CatalogNotFound, localizable, \
    PluginInfo, CatalogCantGoBackwards, CatalogCantGoDeeper
from octobot.catalogs import CatalogHandler

GRAPHQL_URL = "https://graphql.anilist.co"

GRAPHQL_QUERY = """
query Media($query: String, $page: Int, $perPage: Int, $type: MediaType) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      perPage
      currentPage
      lastPage
      hasNextPage
    }
    media (search: $query, type: $type) {
      id
      type
      title {
        english
        romaji
      }
      format
      genres
      description
      status
      episodes
      volumes
      chapters
      coverImage {
        large
        medium
      }
      startDate {
        year
        month
        day
      }
      averageScore
      siteUrl
    }
  }
}

query Character($query: String, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      perPage
      currentPage
      lastPage
      hasNextPage
    }
    characters (search: $query) {
      name {
        full
        alternative
      }
      image {
        large
        medium
      }
      description
      media {
        nodes {
          title {
            romaji
            english
          }
          siteUrl
        }
      }
      siteUrl
    }
  }
}
"""
HEADERS = {"User-Agent": "OctoBot/1.0"}

MEDIA_TEMPLATE_STR = localizable("""<b>{title}</b>
<i>{metadata}</i>
<a href="{siteUrl}">on anilist</a>

{description}

<i>{genres}</i>
""")

CHARACTER_TEMPLATE_STR = localizable("""<b>{full_name}</b>
<i>{alternative_names}</i><a href="{siteUrl}">on anilist</a>

{description}

<i>Present in:</i>
{present_in}
""")

ANIME_MEDIA_STATUSES_STR = {
    "FINISHED": localizable("finished"),
    "RELEASING": localizable("airing"),
    "NOT_YET_RELEASED": localizable("not yet airing"),
    "CANCELLED": localizable("cancelled")
}

MANGA_MEDIA_STATUSES_STR = {
    "FINISHED": localizable("finished"),
    "RELEASING": localizable("releasing"),
    "NOT_YET_RELEASED": localizable("not yet released"),
    "CANCELLED": localizable("cancelled")
}

MEDIA_FORMAT_STR = {
    "TV": localizable("TV animation series"),
    "TV_SHORT": localizable("TV short"),
    "MOVIE": localizable("anime movie"),
    "SPECIAL": localizable("anime special"),
    "OVA": localizable("OVA"),
    "ONA": localizable("ONA"),
    "MUSIC": localizable("AMV"),
    "MANGA": localizable("manga series"),
    "NOVEL": localizable("light novel series"),
    "ONE_SHOT": localizable("one-shot manga")
}

MEDIA_ANIME = "ANIME"
MEDIA_MANGA = "MANGA"

MAX_RESULTS = 25

plugin_info = PluginInfo("AniList")


def cleanse_html(raw_html):
    r = re.compile("<.*?>")
    cleansed_text = re.sub(r, "", raw_html)
    cleansed_text = cleansed_text.replace('&', '&amp;')
    return cleansed_text


def cleanse_spoilers(raw_text: str, replacement_text: str, html=False):
    r = re.compile("~!.*!~", flags=re.S)
    if html:
        replacement_text = f"<i>{replacement_text}</i>"
    cleansed_text = re.sub(r, replacement_text, raw_text)
    return cleansed_text


def graphql(query, operation_name, params):
    r = requests.post(GRAPHQL_URL, json={"query": query, "operationName": operation_name, "variables": params},
                      headers=HEADERS)
    r.raise_for_status()
    json = r.json()
    return json


def get_media_title(title):
    romaji, english = title["romaji"], title["english"]

    if romaji == english or english is None:
        title_str = romaji
    else:
        title_str = f"{english} ({romaji})"

    return title_str


class different_locale:
    def __init__(self, _locale):
        self.locale = _locale

    def __enter__(self):
        self.old_locale = locale.getlocale(locale.LC_TIME)
        try:
            locale.setlocale(locale.LC_TIME, self.locale)
        except locale.Error:
            pass

    def __exit__(self, *args):
        locale.setlocale(locale.LC_TIME, self.old_locale)


def get_fuzzy_date_str(fuzzy_date, ctx: Context):
    year = fuzzy_date["year"]
    month = fuzzy_date["month"]
    day = fuzzy_date["day"]

    with different_locale(ctx.locale):
        if day is not None:
            return f"{calendar.month_abbr[month]} {day}, {year}"
        elif month is not None:
            return f"{calendar.month_abbr[month]} {year}"
        elif year is not None:
            return str(year)
        else:
            return None


def format_media_description(description: Union[str, None], ctx: Context):
    if description is None:
        short = ctx.localize("no description")
        long = "<i>{}</i>".format(ctx.localize("No description provided."))
    else:
        long = cleanse_html(description)
        replacement_text = ctx.localize("(spoilers redacted)")
        short = cleanse_spoilers(long, replacement_text, html=False)
        long = cleanse_spoilers(long, replacement_text, html=True)

    short = textwrap.shorten(short, width=70, placeholder="…")
    long = textwrap.shorten(long, width=1024, placeholder="…")

    return long, short


def get_media_metadata(media, ctx: Context):
    mtype = media["type"]
    metadata = []

    if media["format"] is not None and media["format"] in MEDIA_FORMAT_STR:
        metadata.append(ctx.localize(MEDIA_FORMAT_STR[media["format"]]))

    if media["status"] is not None:
        if mtype == MEDIA_ANIME:
            status_str = ctx.localize(ANIME_MEDIA_STATUSES_STR.get(media["status"], media["status"]))
        elif mtype == MEDIA_MANGA:
            status_str = ctx.localize(MANGA_MEDIA_STATUSES_STR.get(media["status"], media["status"]))
        metadata.append(status_str)

    if mtype == MEDIA_ANIME and media["episodes"] is not None:
        episodes_str = ctx.localize("{} episodes").format(media["episodes"])
        metadata.append(episodes_str)

    if mtype == MEDIA_MANGA:
        if media["volumes"] is not None:
            volumes_str = ctx.localize("{} volumes").format(media['volumes'])
            metadata.append(volumes_str)

        if media["chapters"] is not None:
            chapters_str = ctx.localize("{} chapters").format(media['chapters'])
            metadata.append(chapters_str)

    if media["averageScore"] is not None:
        score = media["averageScore"] / 10
        score_str = ctx.localize("rating {:0.1f}/10").format(score)
        metadata.append(score_str)

    if media["startDate"] is not None:
        start_date_str = get_fuzzy_date_str(media["startDate"], ctx)
        if start_date_str is not None:
            metadata.append(start_date_str)

    return metadata


def anilist_search_media(query: str, offset: str, count: int, bot: OctoBot, ctx: Context, media_type=MEDIA_ANIME):
    res = []

    try:
        offset = int(offset)
    except ValueError:
        raise CatalogNotFound

    if offset < 0:
        raise CatalogCantGoBackwards

    if count > MAX_RESULTS:
        count = MAX_RESULTS

    resp = graphql(GRAPHQL_QUERY, "Media", {
        "query": query,
        "page": ceil((offset + 1) / count),
        "perPage": count,
        "type": media_type
    })

    page_data = resp["data"]["Page"]
    page_info = page_data["pageInfo"]
    media = page_data["media"]

    total = page_info["total"]

    if total == 0:
        if offset == 0:
            raise CatalogNotFound()
        else:
            raise CatalogCantGoDeeper()

    for item in media:
        item["title"] = get_media_title(item["title"])
        item["metadata"] = ", ".join(get_media_metadata(item, ctx))
        item["description"], short_description = format_media_description(item["description"], ctx)
        item["genres"] = ", ".join(item["genres"])

        text = ctx.localize(MEDIA_TEMPLATE_STR).format(**item)

        photos = [
            CatalogPhoto(url=item["coverImage"]["large"], width=0, height=0),
            CatalogPhoto(url=item["coverImage"]["medium"], width=0, height=0),
        ]

        res.append(CatalogKeyArticle(text=text,
                                     title=item["title"],
                                     description=short_description,
                                     photo=photos,
                                     parse_mode="HTML"))

    next_offset = offset + count
    if next_offset > total:
        next_offset = None

    return Catalog(
        results=res,
        max_count=total,
        previous_offset=offset - count,
        current_index=offset,
        next_offset=next_offset
    )


@CatalogHandler(command=["anilist", "anime"],
                description=localizable("Search anime on AniList"))
def anilist_search_anime(query, offset, count, bot, ctx):
    return anilist_search_media(query, offset, count, bot, ctx, media_type=MEDIA_ANIME)


@CatalogHandler(command=["anilist_manga", "manga"],
                description=localizable("Search manga on AniList"))
def anilist_search_manga(query, offset, count, bot, ctx):
    return anilist_search_media(query, offset, count, bot, ctx, media_type=MEDIA_MANGA)


@CatalogHandler(command=["anilist_character", "anichar", "character"],
                description=localizable("Search for character on AniList"))
def anilist_search_character(query: str, offset: str, count: int, bot: OctoBot, ctx: Context) -> Catalog:
    res = []

    try:
        offset = int(offset)
    except ValueError:
        raise CatalogNotFound

    if offset < 0:
        raise CatalogCantGoBackwards

    if count > MAX_RESULTS:
        count = MAX_RESULTS

    resp = graphql(GRAPHQL_QUERY, "Character", {
        "query": query,
        "page": ceil((offset + 1) / count),
        "perPage": count
    })

    page_data = resp["data"]["Page"]
    page_info = page_data["pageInfo"]
    characters = page_data["characters"]

    total = page_info["total"]

    if total == 0:
        raise CatalogNotFound

    for item in characters:
        item["full_name"] = item["name"]["full"]

        if len(item["name"]["alternative"]) > 0 and item["name"]["alternative"][0] != "":
            item["alternative_names"] = "aka " + ", ".join(item["name"]["alternative"]) + "\n"
        else:
            item["alternative_names"] = ""

        item["description"], short_description = format_media_description(item["description"], ctx)

        item["present_in"] = "\n".join(
            [f"<a href=\"{media['siteUrl']}\">{get_media_title(media['title'])}</a>" for media in
             item["media"]["nodes"]])

        text = ctx.localize(CHARACTER_TEMPLATE_STR).format(**item)

        photos = [
            CatalogPhoto(url=item["image"]["large"], width=0, height=0),
            CatalogPhoto(url=item["image"]["medium"], width=0, height=0),
        ]

        print(item["full_name"], end=' ')

        res.append(CatalogKeyArticle(text=text,
                                     title=item["full_name"],
                                     description=short_description,
                                     photo=photos,
                                     parse_mode="HTML"))

    next_offset = offset + count
    if next_offset > total:
        next_offset = None

    return Catalog(
        results=res,
        max_count=total,
        previous_offset=offset - count,
        current_index=offset + 1,
        next_offset=next_offset
    )
