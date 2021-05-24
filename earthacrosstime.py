import math
import os
import random
import sys
import time

import argparse

from configobj import ConfigObj

import logging
import logging.config
import traceback

import shapefile
import shapely.geometry

import requests

from moviepy.editor import *
from PIL import Image, ImageDraw, ImageFont
import numpy as np

import twitter

class Config:
    """
    Queen Daenerys Stormborn of the House Targaryen, the First of Her Name,
    Queen of the Andals, the Rhoynar and the First Men, Lady of the Seven
    Kingdoms and Protector of the Realm, Lady of Dragonstone, Queen of Meereen,
    Khaleesi of the Great Grass Sea, the Unburnt, Breaker of Chains, Mother of
    Dragons, Keeper of Configuration Variables.
    """

    def __init__(self, config):
        self.verbosity = config['GENERAL']['verbosity']
        self.logfile = config['GENERAL']['logfile']
        self.temp_dir = config['GENERAL']['temp_dir']

        self.timemachine_repository_url = config['TIMEMACHINE']['timemachine_repository_url']
        if self.timemachine_repository_url[-1] != "/":
            self.timemachine_repository_url += "/"
        self.attribution = config['TIMEMACHINE']['attribution']
        self.resize = config['TIMEMACHINE']['resize']

        self.shapefile = config['GEOGRAPHY']['shapefile']
        self.point = config['GEOGRAPHY']['point']
        self.max_meters_per_pixel = config['GEOGRAPHY']['max_meters_per_pixel']
        self.nominatim_url = config['GEOGRAPHY']['nominatim_url']
        if self.nominatim_url[-1] != "/":
            self.nominatim_url += "/"

        self.twitter_handle = config['TWITTER']['twitter_handle']
        self.consumer_key = config['TWITTER']['consumer_key']
        self.consumer_secret = config['TWITTER']['consumer_secret']
        self.access_token = config['TWITTER']['access_token']
        self.access_token_secret = config['TWITTER']['access_token_secret']
        self.tweet_text = config['TWITTER']['tweet_text']
        self.include_location_in_metadata = config['TWITTER']['include_location_in_metadata']

class Metadata:
    """The same but for metadata derived from tm.json and r.json."""

    def __init__(self, timemachine_repository_url, dataset, projection_bounds, capture_times, frames, fps, level_info, nlevels, width, height, tile_width, tile_height, video_width, video_height):
        self.timemachine_repository_url = timemachine_repository_url
        self.dataset = dataset
        self.projection_bounds = projection_bounds
        self.capture_times = capture_times
        self.frames = frames
        self.fps = fps
        self.level_info = level_info
        self.nlevels = nlevels
        self.width = width
        self.height = height
        self.tile_width = tile_width
        self.tile_height = tile_height
        self.video_width = video_width
        self.video_height = video_height

    def __repr__(self):
        return f"Metadata({self.timemachine_repository_url}, {self.dataset}, {self.projection_bounds}, {self.capture_times}, {self.frames}, {self.fps}, {self.level_info}, {self.nlevels}, {self.width}, {self.height}, {self.tile_width}, {self.tile_height}, {self.video_width}, {self.video_height})"

class MetadataFetcher:
    """
    Used to fetch and parse tm.json and r.json, yielding a Metadata object
    containing only the relevant metadata.
    """

    def __init__(self, timemachine_repository_url):
        self.timemachine_repository_url = timemachine_repository_url

    def __fetch_json(self, url):
        """
        Fetches and parses a JSON file. During fetching, a
        requests.RequestException may be raised, and during parsing, a
        json.JSONDecodeError may occur – neither of them is worth catching since
        this bot can't do anything useful without metadata, anyway.
        """

        raw = requests.get(url)
        json = raw.json()
        return json

    def __fetch_tmjson(self):
        tmjson_url = self.timemachine_repository_url + "tm.json"
        return self.__fetch_json(tmjson_url)

    def __fetch_rjson(self, dataset):

        # matches https://github.com/CMU-CREATE-Lab/timemachine-viewer/blob/fb920433fcb8b5a7a84279142c5e27e549a852aa/js/org/gigapan/timelapse/timelapse.js#L3826
        rjson_url = self.timemachine_repository_url + dataset + "/" + "r.json"
        return self.__fetch_json(rjson_url)

    def fetch(self):
        """This function does all the "heavy" lifting."""

        tmjson = self.__fetch_tmjson()
        datasets = tmjson['datasets']
        assert len(datasets) == 1
        dataset = datasets[0]['id']
        rjson = self.__fetch_rjson(dataset)

        assert rjson['leader'] == 0
        projection_bounds = tmjson['projection-bounds']
        capture_times = tmjson['capture-times']
        frames = rjson['frames']
        assert len(capture_times) == frames
        fps = rjson['fps']
        level_info = rjson['level_info']
        nlevels = rjson['nlevels']
        assert len(level_info) == nlevels
        width = rjson['width']
        height = rjson['height']
        tile_width = rjson['tile_width']
        tile_height = rjson['tile_height']
        video_width = rjson['video_width']
        video_height = rjson['video_height']
        assert 4 * tile_width == video_width
        assert 4 * tile_height == video_height

        return Metadata(self.timemachine_repository_url, dataset, projection_bounds, capture_times, frames, fps, level_info, nlevels, width, height, tile_width, tile_height, video_width, video_height)

class MercatorProjection:
    """
    The particular flavor of Web Mercator Projection used by Time Machine. Based
    on the original JavaScript implementation found at:
    https://github.com/CMU-CREATE-Lab/timemachine-viewer/blob/fb920433fcb8b5a7a84279142c5e27e549a852aa/js/org/gigapan/timelapse/mercator.js
    """

    def __init__(self, projection_bounds, width, height):
        self.west = projection_bounds['west']
        self.north = projection_bounds['north']
        self.east = projection_bounds['east']
        self.south = projection_bounds['south']
        self.width = width
        self.height = height

    def __raw_project_lat(self, lat):
        return math.log((1 + math.sin(lat * math.pi / 180)) / math.cos(lat * math.pi / 180))

    def __raw_unproject_lat(self, y):
        return (2 * math.atan(math.exp(y)) - math.pi / 2) * 180 / math.pi

    def __interpolate(self, x, from_low, from_high, to_low, to_high):
        return (x - from_low) / (from_high - from_low) * (to_high - to_low) + to_low

    def geopoint_to_pixpoint(self, geopoint):
        x = self.__interpolate(geopoint.lon, self.west, self.east, 0, self.width)
        y = self.__interpolate(
            self.__raw_project_lat(geopoint.lat),
            self.__raw_project_lat(self.north),
            self.__raw_project_lat(self.south),
            0,
            self.height
            )
        return PixPoint(x, y)

    def pixpoint_to_geopoint(self, pixpoint):
        lon = self.__interpolate(pixpoint.x, 0, self.width, self.west, self.east)
        lat = self.__raw_unproject_lat(self.__interpolate(
            pixpoint.y,
            0,
            self.height,
            self.__raw_project_lat(self.north),
            self.__raw_project_lat(self.south)
            ))
        return GeoPoint(lat, lon)

class GeoPoint:
    """
    A latitude-longitude coordinate pair, in that order due to ISO 6709, see:
    https://stackoverflow.com/questions/7309121/preferred-order-of-writing-latitude-longitude-tuples
    """

    def __init__(self, lat, lon):
        assert -90 <= lat <= 90 and -180 <= lon <= 180

        self.lat = lat
        self.lon = lon

    def __repr__(self):
        return f"GeoPoint({self.lat}, {self.lon})"

    def fancy(self):
        """Stringifies the point in a more fancy way than __repr__, e.g.
        "44°35'27.6"N 100°21'53.1"W", i.e. with minutes and seconds."""

        # helper function as both latitude and longitude are stringified
        # basically the same way
        def fancy_coord(coord, pos, neg):
            coord_dir = pos if coord > 0 else neg
            coord_tmp = abs(coord)
            coord_deg = math.floor(coord_tmp)
            coord_tmp = (coord_tmp - math.floor(coord_tmp)) * 60
            coord_min = math.floor(coord_tmp)
            coord_sec = round((coord_tmp - math.floor(coord_tmp)) * 600) / 10
            coord = f"{coord_deg}°{coord_min}'{coord_sec}\"{coord_dir}"
            return coord

        lat = fancy_coord(self.lat, "N", "S")
        lon = fancy_coord(self.lon, "E", "W")

        return f"{lat} {lon}"

    @classmethod
    def random(cls, georect):
        """
        Generating a random point with regard to actual surface area is a bit
        tricky due to meridians being closer together at high latitudes (see
        https://en.wikipedia.org/wiki/Mercator_projection#Distortion_of_sizes),
        which is why this isn't just a matter of doing something like this:
        lat = random.uniform(georect.sw.lat, georect.ne.lat)
        lon = random.uniform(georect.sw.lon, georect.ne.lon)
        """

        # latitude
        north = math.radians(georect.ne.lat)
        south = math.radians(georect.sw.lat)
        lat = math.degrees(math.asin(random.random() * (math.sin(north) - math.sin(south)) + math.sin(south)))

        # longitude
        west = georect.sw.lon
        east = georect.ne.lon
        width = east - west
        if width < 0:
            width += 360
        lon = west + width * random.random()
        if lon > 180:
            lon -= 360
        elif lon < -180:
            lon += 360

        return cls(lat, lon)

    def to_shapely_point(self):
        """
        Conversion to a point as expected by shapely. Note that latitude and
        longitude are reversed here – this matches their order in shapefiles.
        """

        return shapely.geometry.Point(self.lon, self.lat)

    def determine_level(self, proj, nlevels, max_meters_per_pixel):
        """
        Computes the outermost (i.e. lowest) zoom level that still fulfills the
        constraint. Based on the original JavaScript implementation found at:
        https://github.com/CMU-CREATE-Lab/timemachine-viewer/blob/fb920433fcb8b5a7a84279142c5e27e549a852aa/js/org/gigapan/timelapse/scaleBar.js#L457
        """

        earth_radius = 6371  # in kilometers
        c1 = math.radians(earth_radius)

        point = proj.geopoint_to_pixpoint(self)
        for level in reversed(range(nlevels)):
            scale = 2 ** (level - (nlevels - 1))
            one_pixel_off = proj.pixpoint_to_geopoint(PixPoint((point.x + 1 / scale), point.y))
            degrees_per_pixel = abs(self.lon - one_pixel_off.lon)
            v1 = degrees_per_pixel * math.cos(math.radians(self.lat))
            meters_per_pixel = c1 * v1 * 1000

            if meters_per_pixel > max_meters_per_pixel or level == 0:
                return ZoomLevel(level, self.lat, meters_per_pixel)

class GeoRect:
    """
    A rectangle between two (geo)points. The first point must be the
    southwestern corner, the second point the northeastern corner:
       +---+ ne
       |   |
    sw +---+
    """

    def __init__(self, sw, ne):
        assert sw.lat <= ne.lat
        # not assert sw.lon < ne.lon since it may stretch across the date line

        self.sw = sw
        self.ne = ne

    @classmethod
    def from_shapefile_bbox(cls, bbox):
        """
        Basically from [sw_lon, sw_lat, ne_lon, sw_lat], which is the order
        pyshp stores bounding boxes in.
        """

        sw = GeoPoint(bbox[1], bbox[0])
        ne = GeoPoint(bbox[3], bbox[2])
        return cls(sw, ne)


class GeoShape:
    """
    This class is where shapefiles (of the form detailed in the config example,
    i.e. containing one layer with one polygon shape with lon/lat coordinates)
    are loaded and queried. Note that shapefiles use (lon, lat) coordinates,
    which are sequestered to this class only.
    """

    def __init__(self, shapefile_path):

        sf = shapefile.Reader(shapefile_path)
        shapes = sf.shapes()

        assert len(shapes) == 1
        assert shapes[0].shapeTypeName == 'POLYGON'

        self.outline = shapes[0]

    def contains(self, geopoint):
        """Does the shape contain the point?"""

        point = geopoint.to_shapely_point()
        polygon = shapely.geometry.shape(self.outline)
        return polygon.contains(point)

    def random_geopoint(self):
        """
        A random geopoint, using rejection sampling to make sure it's
        contained within the shape.
        """

        bounds = GeoRect.from_shapefile_bbox(self.outline.bbox)
        geopoint = GeoPoint.random(bounds)

        i = 0
        while not self.contains(geopoint):
            i += 1
            if i > 250:
                raise ValueError("cannot seem to find a point in the shape's bounding box that's within the shape – is your data definitely okay (it may well be if it's a bunch of spread-out islands)? if you're sure, you'll need to raise the iteration limit in this function")
            geopoint = GeoPoint.random(bounds)

        return geopoint

class PixPoint:
    """
    A point relative to the "width" and "height" attributes of r.json, i.e. the
    total width and height of the world at the most detailed zoom level
    available for the selected Time Machine repository.
    """

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"PixPoint({self.x}, {self.y})"

class ZoomLevel:
    """A zoom level."""

    def __init__(self, index, lat, meters_per_pixel):
        self.index = index
        self.lat = lat
        self.meters_per_pixel = meters_per_pixel

    def __repr__(self):
        return f"ZoomLevel({self.index}, {self.lat}, {self.meters_per_pixel})"

    def kilometers(self, pixels):
        """How many kilometers correspond to a length of this many pixels?"""

        return self.meters_per_pixel * pixels / 1000

class Tile:
    """
    Represents a tile, i.e. the coordinates of a timelapse video. Video
    downloading and processing are handled elsewhere.
    """

    def __init__(self, level, col, row):
        self.level = level
        self.col = col
        self.row = row

    def __repr__(self):
        return f"Tile({self.level}, {self.col}, {self.row})"

    @classmethod
    def from_pixpoint_and_level(cls, pixpoint, level, metadata):
        """
        Based on the original JavaScript implementation found at:
        https://github.com/CMU-CREATE-Lab/timemachine-viewer/blob/fb920433fcb8b5a7a84279142c5e27e549a852aa/js/org/gigapan/timelapse/timelapse.js#L3367
        """

        level_scale = 2 ** (metadata.nlevels - 1 - level.index)

        col = round((pixpoint.x - (metadata.video_width * level_scale * 0.5)) / (metadata.tile_width * level_scale))
        col = max(col, 0)
        col = min(col, metadata.level_info[level.index]['cols'] - 1)

        row = round((pixpoint.y - (metadata.video_height * level_scale * 0.5)) / (metadata.tile_height * level_scale))
        row = max(row, 0)
        row = min(row, metadata.level_info[level.index]['rows'] - 1)

        return cls(level, col, row)

class RawVideo:
    """Responsible for downloading and storing timelapse videos."""

    def __init__(self, tile, base_url, temp_dir):
        self.tile = tile
        self.base_url = base_url
        self.temp_dir = temp_dir

        self.url = f"{base_url}/{tile.level.index}/{tile.row}/{tile.col}.mp4"
        self.path = os.path.join(temp_dir, f"{tile.level.index}-{tile.row}-{tile.col}-raw.mp4")

    def __write_to_temp(self, video_data):
        if not os.path.isdir(self.temp_dir):
            os.makedirs(self.temp_dir)
        with open(self.path, 'wb') as f:
            f.write(video_data)

    def download(self):
        try:
            r = requests.get(self.url)
        except requests.exceptions.RequestException:  # retry once on errors
            time.sleep(10)
            r = requests.get(self.url)
        if r.status_code != 200:
            raise ValueError(f"unable to download tile from {self.url}, status code {r.status_code}")
        self.__write_to_temp(r.content)

    def check_against(self, metadata):
        """Verifies that the video matches the provided metadata."""

        clip = VideoFileClip(self.path)
        assert clip.w == metadata.video_width
        assert clip.h == metadata.video_height
        assert clip.fps == metadata.fps
        assert clip.duration == metadata.frames / metadata.fps

class ReverseGeocoder:
    """
    Allows getting an approximate address or region (depending on zoom level)
    for a geopoint, basically a thin wrapper around the Nominatim API.
    """

    def __init__(self, nominatim_url, geopoint, level=12):
        self.nominatim_url = nominatim_url
        self.geopoint = geopoint
        self.level = level

        self.error = False
        self.attribution = None
        self.name = None

    def fetch(self):
        url = f"{self.nominatim_url}reverse.php?lat={self.geopoint.lat}&lon={self.geopoint.lon}&zoom={self.level.index}&accept-language=en&format=jsonv2"

        # note that the following two lines can throw exceptions – they aren't
        # caught since I rather be aware that something's wrong via the emails
        # the cron daemon sends me on errors
        raw = requests.get(url)
        json = raw.json()

        try:
            self.attribution = json['licence']
        except KeyError:
            self.attribution = ""

        try:
            self.name = json['display_name']
        except KeyError:
            self.name = ""
            self.error = True

class VideoEditor:
    """This is where the magic happens!"""

    def __init__(self, video, resize, geopoint, area_size, capture_times, attribution, reverse_geocode, twitter_handle):
        """
        Ideally, this class would require fewer constructor parameters – which
        would be trivial if config and metadata were available globally. I tried
        that but it's just too yucky.
        """

        self.video = video
        self.resize = resize
        self.geopoint = geopoint
        self.area_size = area_size
        self.capture_times = capture_times
        self.attribution = attribution
        self.reverse_geocode = reverse_geocode
        self.twitter_handle = twitter_handle

        self.path = os.path.join(video.temp_dir, f"{video.tile.level.index}-{video.tile.row}-{video.tile.col}-processed.mp4")

        self.clip = None

    def __draw_text(self, text, fontsize):
        """
        Draws tightly-trimmed text, returning an ImageClip. Characters not
        available in Optician Sans will be replaced with similar characters or
        question marks, which is a terrible hack I feel bad about.
        """

        # load font
        fnt_path = "assets/optician-sans.otf"
        fnt = ImageFont.truetype(fnt_path, fontsize)

        # replace characters that have some sort of available equivalent
        replacements = {
            "©": "(c)",
            "ß": "ss",
            "À": "A",
            "Á": "A",
            "Â": "A",
            "Ã": "A",
            "Ä": "AE",
            "Ç": "C",
            "È": "E",
            "É": "E",
            "Ê": "E",
            "Ë": "E",
            "Ì": "I",
            "Í": "I",
            "Î": "I",
            "Ï": "I",
            "Ñ": "N",
            "Ò": "O",
            "Ó": "O",
            "Ô": "O",
            "Õ": "O",
            "Ö": "OE",
            "Ù": "U",
            "Ú": "U",
            "Û": "U",
            "Ü": "UE",
            "Ý": "Y",
            "à": "a",
            "á": "a",
            "â": "a",
            "ã": "a",
            "ä": "ae",
            "ç": "c",
            "è": "e",
            "é": "e",
            "ê": "e",
            "ë": "e",
            "ì": "i",
            "í": "i",
            "î": "i",
            "ï": "i",
            "ñ": "n",
            "ò": "o",
            "ó": "o",
            "ô": "o",
            "õ": "o",
            "ö": "oe",
            "ù": "u",
            "ú": "u",
            "û": "u",
            "ü": "ue",
            "ý": "y",
            "ÿ": "y",
            "Ĩ": "I",
            "ĩ": "i",
            "ı": "i",
            "Ĵ": "j",
            "ĵ": "j",
            "Ł": "L",
            "ł": "l",
            "Ń": "N",
            "ń": "n",
            "Œ": "OE",
            "œ": "oe",
            "Ŕ": "R",
            "Ŗ": "R",
            "ŗ": "r",
            "Ř": "R",
            "ř": "r",
            "Š": "S",
            "š": "s",
            "Ÿ": "Y",
            "Ž": "Z",
            "ž": "z"
            }
        for r in replacements:
            text = text.replace(r, replacements[r])

        # replace characters that don't have available equivalents with "?";
        # list generated by dropping the font into Wakamai Fondue (see
        # https://wakamaifondue.com/), unchecking "Layout features", and running
        # the following JavaScript snippet in the console:
        # Array.from(document.querySelectorAll(".character-set .label")).map(n => "\\u" + n.innerText.padStart(4, 0)).join("")
        available_chars = list("\u0000\u000D\u0020\u0021\u0022\u0023\u0025\u0026\u0027\u0028\u0029\u002A\u002B\u002C\u002D\u002E\u002F\u0030\u0031\u0032\u0033\u0034\u0035\u0036\u0037\u0038\u0039\u003A\u003B\u003D\u003F\u0040\u0041\u0042\u0043\u0044\u0045\u0046\u0047\u0048\u0049\u004A\u004B\u004C\u004D\u004E\u004F\u0050\u0051\u0052\u0053\u0054\u0055\u0056\u0057\u0058\u0059\u005A\u005B\u005C\u005D\u005F\u0061\u0062\u0063\u0064\u0065\u0066\u0067\u0068\u0069\u006A\u006B\u006C\u006D\u006E\u006F\u0070\u0071\u0072\u0073\u0074\u0075\u0076\u0077\u0078\u0079\u007A\u007B\u007C\u007D\u00B0\u00C5\u00C6\u00D7\u00D8\u00E5\u00E6\u00F7\u00F8\u2013\u2014\u2018\u2019\u201A\u201C\u201D\u201E\u2022\u20AC\u2212")
        def rp(c):
            if c not in available_chars:
                return "?"
            return c
        text = map(rp, list(text))
        text = "".join(text)

        # measure dimensions of text (this is an upper bound due to whitespace
        # included above/below letters), see
        # https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html#PIL.ImageDraw.ImageDraw.textsize
        dummy = Image.new("RGB", (0, 0), (255, 255, 255))
        dummy_draw = ImageDraw.Draw(dummy)
        size = dummy_draw.textsize(text, font=fnt)

        # draw on canvas of measured dimensions, must have transparent background
        txt = Image.new("RGBA", size, (255, 255, 255, 0))
        txt_draw = ImageDraw.Draw(txt)
        txt_draw.text((0, 0), text, font=fnt, fill=(255, 255, 255, 255))

        # crop to actual dimensions now that we can measure those (this
        # sometimes cuts off a bit too much, but there doesn't seem to be a way
        # of fixing that without rolling my own algorithm)
        cropped_txt = txt.crop(txt.getbbox())

        return ImageClip(np.array(cropped_txt))

    def __draw_progress_pieslice(self, size, completion):
        """
        Draws a pie-chart-esque progress meter at the given completion
        percentage, returning an ImageClip.
        """

        # ImageDraw.Draw.pieslice doesn't antialias, so work around that by
        # drawing at 3x scale and then scaling down later
        draw_size = 3 * size

        pie = Image.new("RGBA", (draw_size,draw_size), (255, 255, 255, 0))
        pie_draw = ImageDraw.Draw(pie)
        pie_draw.pieslice([0, 0, draw_size-1, draw_size-1], 0, 360, fill=(255, 255, 255, 128))
        pie_draw.pieslice([0, 0, draw_size-1, draw_size-1], -90, completion * 360 - 90, fill=(255, 255, 255, 255))

        pie_actual_size = pie.resize((size,size))
        return ImageClip(np.array(pie_actual_size))

    def edit(self):
        """
        Do all the work of turning a downloaded raw video into one with location
        information, year meter, proper attribution, and a world map at the end.
        There are a bunch of long lines and magic numbers here, so tread
        lightly.
        """

        raw_video = VideoFileClip(self.video.path)

        resized_video = raw_video
        if self.resize is not None:
            resized_video = raw_video.resize(self.resize)

        width = resized_video.w
        height = resized_video.h

        result_framerate = 24
        images_per_second = 3
        margin = int(height / 30)
        final_frame_persist = 1
        endcard_crossfade = 0.67
        endcard_persist = 4

        # define overlays that will be constant across all frames
        geopoint = self.__draw_text(self.geopoint.fancy(), int(height / 15))
        geopoint = geopoint.set_position((margin, margin))
        area = self.__draw_text(self.area_size, int(height / 22))
        area = area.set_position((margin, margin + geopoint.size[1] + int(0.67 * area.size[1])))
        attribution = self.__draw_text(self.attribution, int(height / 40))
        attribution = attribution.set_position((width - attribution.size[0] - margin, height - attribution.size[1] - margin))

        # process each frame separately
        frames = []
        for n, frame in enumerate(resized_video.iter_frames()):
            frame = ImageClip(frame)

            pieslice_height = int(height / 13.5)  # manually dialled in to match font appearance
            pieslice = self.__draw_progress_pieslice(pieslice_height, n / (len(self.capture_times) - 1))
            pieslice = pieslice.set_position((width - pieslice.size[0] - margin, margin))
            year = self.__draw_text(self.capture_times[n], int(height / 7))
            year = year.set_position((width - year.size[0] - pieslice.size[0] - margin - margin, margin))

            # composite with overlays defined outside this loop
            frame = CompositeVideoClip([frame, pieslice, year, geopoint, area, attribution])

            # significantly (by a factor of 5!) reduce ram consumption by
            # hackily rendering the current frame into an image ahead of the
            # actual rendering step (CompositeVideoClip seems to just devour
            # memory for some reason)
            frame = ImageClip(list(frame.set_duration(1).iter_frames(fps=1))[0])
            frames.append(frame)

        # concatenate
        frames_list = [frame.set_duration(1 / images_per_second) for frame in frames]
        final_frame = frames_list[-1]
        frames_list[-1] = final_frame.set_duration(1 / images_per_second + final_frame_persist)
        frames = concatenate_videoclips(frames_list)

        # add end card (color matches land color of map)
        background = ColorClip((width, height), color=(62, 62, 62))

        # map via https://commons.wikimedia.org/wiki/File:World_location_map_mono.svg
        worldmap = ImageClip("assets/map.png")
        map_scale = background.size[0] / worldmap.size[0]
        worldmap = worldmap.resize((background.size[0], map_scale * worldmap.size[1]))

        pointer = ImageClip("assets/pointer.png").resize(map_scale)
        pointer_x = worldmap.size[0] / 2 * (1 + (self.geopoint.lon / 180)) - pointer.size[0] / 2
        pointer_y = worldmap.size[1] / 2 * (1 - (self.geopoint.lat / 90)) - pointer.size[1] / 2 # "-" since x increased from top while lat increases from bottom
        pointer = pointer.set_position((pointer_x, pointer_y))
        geopoint = self.__draw_text(self.geopoint.fancy(), int(1.33 * pointer.size[1]))
        geopoint_x = None
        geopoint_y = pointer_y + (pointer.size[1] - geopoint.size[1]) / 2
        if self.geopoint.lon < 0:
            geopoint_x = pointer_x + 1.5 * pointer.size[0]
        else:
            geopoint_x = pointer_x - geopoint.size[0] - 0.5 * pointer.size[0]
        geopoint = geopoint.set_position((geopoint_x, geopoint_y))
        geolocation = None
        if not self.reverse_geocode.error:
            geolocation = self.__draw_text(self.reverse_geocode.name, int(1.1 * pointer.size[1]))
            geolocation_x = None
            geolocation_y = pointer_y + (pointer.size[1] - geolocation.size[1]) / 2 + geopoint.size[1] * 1.5
            if self.geopoint.lon < 0:
                geolocation_x = pointer_x + 1.5 * pointer.size[0]
            else:
                geolocation_x = pointer_x - geolocation.size[0] - 0.5 * pointer.size[0]
            geolocation = geolocation.set_position((geolocation_x, geolocation_y))

        credit_lines = [
            "@" + self.twitter_handle,
            "https://twitter.com/" + self.twitter_handle + ", bot source code: https://github.com/doersino/earthacrosstime, typeface: optician sans",
            "video url: " + self.video.url
        ]
        if not self.reverse_geocode.error:
            credit_lines.append("reverse geocoding: " + self.reverse_geocode.attribution)
        credit = []
        fontsize = int(height / 45)
        accumulated_height = -fontsize
        for n, text in enumerate(reversed(credit_lines)):

            # increase font size of topmost line
            if n == len(credit_lines) - 1:
                fontsize = int(height / 30)

            line = self.__draw_text(text, fontsize)
            line_x = width / 2 - line.size[0] / 2
            line_y = height - line.size[1] - accumulated_height - fontsize - margin
            line = line.set_position((line_x, line_y))

            accumulated_height += fontsize
            credit.append(line)

        endcard_components = [background, worldmap, pointer, geopoint]
        if not self.reverse_geocode.error:
            endcard_components.append(geolocation)
        endcard = CompositeVideoClip(endcard_components + credit)
        endcard_fade = CompositeVideoClip([final_frame.set_duration(endcard_crossfade).fadeout(endcard_crossfade), endcard.set_duration(endcard_crossfade).crossfadein(endcard_crossfade)])
        endcard = endcard.set_duration(endcard_persist)

        # finish
        clip = concatenate_videoclips([frames, endcard_fade, endcard])
        clip = clip.set_fps(result_framerate)
        self.clip = clip

    def render(self):
        """Render the assembled video."""

        #self.clip.write_videofile(self.path)
        self.clip.write_videofile(self.path, logger=None)


class Log:
    """
    A simplifying wrapper around the parts of the logging module that are
    relevant here, plus some minor extensions. Goal: Logging of warnings
    (depending on verbosity level), errors and exceptions on stderr, other
    messages (modulo verbosity) on stdout, and everything (independent of
    verbosity) in a logfile.
    """

    def __init__(self, logfile, verbosity):

        # name and initialize logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)

        # via https://stackoverflow.com/a/36338212
        class LevelFilter(logging.Filter):
            def __init__(self, low, high):
                self.low = low
                self.high = high
                logging.Filter.__init__(self)
            def filter(self, record):
                return self.low <= record.levelno <= self.high

        # log errors (and warnings if a higher verbosity level is dialed in) on
        # stderr
        eh = logging.StreamHandler()
        if verbosity == "quiet":
            eh.setLevel(logging.ERROR)
        else:
            eh.setLevel(logging.WARNING)
        eh.addFilter(LevelFilter(logging.WARNING, logging.CRITICAL))
        stream_formatter = logging.Formatter('%(message)s')
        eh.setFormatter(stream_formatter)
        self.logger.addHandler(eh)

        # log other messages on stdout if verbosity not set to quiet
        if verbosity != "quiet":
            oh = logging.StreamHandler(stream=sys.stdout)
            if verbosity == "verbose":
                oh.setLevel(logging.DEBUG)
            else:
                oh.setLevel(logging.INFO)
            oh.addFilter(LevelFilter(logging.DEBUG, logging.INFO))
            stream_formatter = logging.Formatter('%(message)s')
            oh.setFormatter(stream_formatter)
            self.logger.addHandler(oh)

        # log everything to file independent of verbosity
        if logfile is not None:
            fh = logging.FileHandler(logfile)
            fh.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
            fh.setFormatter(file_formatter)
            self.logger.addHandler(fh)

    def debug(self, s): self.logger.debug(s)
    def info(self, s): self.logger.info(s)
    def warning(self, s): self.logger.warning(s)
    def error(self, s): self.logger.error(s)
    def critical(self, s): self.logger.critical(s)

    def exception(self, e):
        """
        Logging of game-breaking exceptions, based on:
        https://stackoverflow.com/a/40428650
        """

        e_traceback = traceback.format_exception(e.__class__, e, e.__traceback__)
        traceback_lines = []
        for line in [line.rstrip('\n') for line in e_traceback]:
            traceback_lines.extend(line.splitlines())
        for line in traceback_lines:
            self.critical(line)
        sys.exit(1)

class Tweeter:
    """
    Basic class for tweeting videos, a simple wrapper around the relevant
    methods provided by the python-twitter library.
    """

    def __init__(self, consumer_key, consumer_secret, access_token, access_token_secret):
        self.api = twitter.Api(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            access_token_key=access_token,
            access_token_secret=access_token_secret,
            input_encoding="utf-8"
            )

    def upload(self, path):
        """Uploads a video to Twitter."""

        return self.api.UploadMediaChunked(path)

    def tweet(self, text, media, geopoint=None):
        """Dispatches a tweet with a video attachment."""

        if geopoint:
            self.api.PostUpdate(
                text,
                media=media,
                latitude=geopoint.lat,
                longitude=geopoint.lon,
                display_coordinates=True
            )
        else:
            self.api.PostUpdate(text, media=media.media_id)

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument('config_path', metavar='CONFIG_PATH', type=str, nargs='?', default="config.ini", help='config file to use instead of looking for config.ini in the current working directory')
    parser.add_argument('-p', '--point', dest='point', metavar='LAT,LON', type=str, help='a point, e.g. \'37.453896,126.446829\', that will override your configuration')
    parser.add_argument('-m', '--max-meters-per-pixel', dest='max_meters_per_pixel', metavar='N', type=float, help='a maximum meters per pixel constraint that will override your configuration')
    args = parser.parse_args()

    # load configuration either from config.ini or from a user-supplied file
    # (the latter option is handy if you want to run multiple instances of
    # this bot with different configurations)
    config = ConfigObj(args.config_path, unrepr=True)
    c = Config(config)

    # override configured point and/or meters per pixel constraint if supplied
    # via the cli
    if args.point:
        c.point = tuple(map(float, args.point.split(",")))
    if args.max_meters_per_pixel:
        c.max_meters_per_pixel = args.max_meters_per_pixel

    logger = Log(c.logfile, c.verbosity)

    try:

        logger.info("Fetching and parsing metadata...")
        metadata = MetadataFetcher(c.timemachine_repository_url).fetch()
        logger.debug(metadata)

        # compute point
        geopoint = None
        if c.shapefile is None and c.point is None:
            raise RuntimeError("neither shapefile path nor point configured")
        elif c.point is not None:
            logger.info("Using configured point instead of shapefile...")
            geopoint = GeoPoint(c.point[0], c.point[1])
        elif c.point is None:
            logger.info("Loading shapefile...")
            logger.debug(c.shapefile)
            shape = GeoShape(c.shapefile)
            logger.info("Generating random point within shape...")
            geopoint = shape.random_geopoint()
        logger.debug(geopoint)

        # determine maximum allowable meters per pixel
        max_meters_per_pixel = None
        if isinstance(c.max_meters_per_pixel, tuple):
            logger.info("Randomizing meters-per-pixel constraint in the configured range...")
            max_meters_per_pixel = random.randrange(c.max_meters_per_pixel[0], c.max_meters_per_pixel[1] + 1)
        else:
            logger.info("Using configured meters-per-pixel constraint...")
            max_meters_per_pixel = c.max_meters_per_pixel
        logger.debug(max_meters_per_pixel)

        logger.info("Setting up Mercator projection based on metadata...")
        proj = MercatorProjection(metadata.projection_bounds, metadata.width, metadata.height)

        logger.info("Computing pixel point from point...")
        pixpoint = proj.geopoint_to_pixpoint(geopoint)
        logger.debug(pixpoint)

        logger.info("Determining zoom level based on meters-per-pixel constraint...")
        level = geopoint.determine_level(proj, metadata.nlevels, max_meters_per_pixel)
        logger.debug(level)

        logger.info("Initializing timelapse tile at computed pixel point and level...")
        tile = Tile.from_pixpoint_and_level(pixpoint, level, metadata)
        logger.debug(tile)

        logger.info("Downloading video for tile...")
        video = RawVideo(tile, f"{metadata.timemachine_repository_url}{metadata.dataset}", c.temp_dir)
        video.download()
        logger.debug(video.url)

        logger.info("Verifying against metadata...")
        video.check_against(metadata)

        logger.info("Looking up the name of wherever the point is located...")
        reverse_geocode = ReverseGeocoder(c.nominatim_url, geopoint, level)
        reverse_geocode.fetch()
        if not reverse_geocode.error:
            logger.debug(reverse_geocode.name)

        logger.info("Determining how large the area covered is...")
        area_w = round(tile.level.kilometers(metadata.video_width), 2)
        area_h = round(tile.level.kilometers(metadata.video_height), 2)
        area_size = f"{area_w} × {area_h} km"
        logger.debug(area_size)

        logger.info("Editing video...")
        editor = VideoEditor(video, c.resize, geopoint, area_size, metadata.capture_times, c.attribution, reverse_geocode, c.twitter_handle)
        editor.edit()

        logger.info("Rendering video (this may take a minute or so)...")
        editor.render()
        logger.debug(editor.path)

        tweeting = all(x is not None for x in [c.consumer_key, c.consumer_secret, c.access_token, c.access_token_secret])
        if tweeting:
            logger.info("Connecting to Twitter...")
            tweeter = Tweeter(c.consumer_key, c.consumer_secret, c.access_token, c.access_token_secret)

            # time machine levels aren't quite the same as the zoom levels used
            # by these mapping services, but they're close enough the be useful
            osm_url = f"https://www.openstreetmap.org/#map={level.index}/{geopoint.lat}/{geopoint.lon}"
            googlemaps_url = f"https://www.google.com/maps/@{geopoint.lat},{geopoint.lon},{level.index}z"

            year_range = f"{metadata.capture_times[0]} – {metadata.capture_times[-1]}"

            logger.info("Uploading video to Twitter...")
            media = tweeter.upload(editor.path)

            logger.info("Sending tweet...")
            tweet_text = c.tweet_text.format(
                latitude=geopoint.lat,
                longitude=geopoint.lon,
                point_fancy=geopoint.fancy(),
                area_size=area_size,
                osm_url=osm_url,
                googlemaps_url=googlemaps_url,
                location=reverse_geocode.name,
                year_range=year_range
            )
            logger.debug(tweet_text)
            if c.include_location_in_metadata:
                tweeter.tweet(tweet_text, media, geopoint)
            else:
                tweeter.tweet(tweet_text, media)

        logger.info("All done!")

    except Exception as e:
        logger.exception(e)
        raise e

if __name__ == "__main__":
    main()
