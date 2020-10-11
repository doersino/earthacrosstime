import json
import math
import os
import random
import sys

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
        if (self.timemachine_repository_url[-1] != "/"):
            self.timemachine_repository_url += "/"
        self.attribution = config['TIMEMACHINE']['attribution']

        self.shapefile = config['GEOGRAPHY']['shapefile']
        self.point = config['GEOGRAPHY']['point']
        self.max_meters_per_pixel = config['GEOGRAPHY']['max_meters_per_pixel']
        self.nominatim_url = config['GEOGRAPHY']['nominatim_url']
        if (self.nominatim_url[-1] != "/"):
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

    # TODO abstract: this and next
    def __fetch_tmjson(self):
        tmjson_url = self.timemachine_repository_url + "tm.json"
        tmjson_raw = requests.get(tmjson_url)  # can throw requests.RequestException
        tmjson = tmjson_raw.json()  # can throw json.JSONDecodeError
        return tmjson

    def __fetch_rjson(self, dataset):
        rjson_url = self.timemachine_repository_url + dataset + "/" + "r.json"  # https://github.com/CMU-CREATE-Lab/timemachine-viewer/blob/fb920433fcb8b5a7a84279142c5e27e549a852aa/js/org/gigapan/timelapse/timelapse.js#L3826
        rjson_raw = requests.get(rjson_url)  # can throw requests.RequestException
        rjson = rjson_raw.json()  # can throw json.JSONDecodeError
        return rjson

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

# TODO attribute: based on ...
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
        lon = self.__interpolate(pixpoint.x, 0, self.width, self.west, self.east);
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

        # TODO cleanup, convert to radians with built-in functions
        radian_per_degree = math.pi / 180
        earth_radius = 6371  # in kilometers
        c1 = radian_per_degree * earth_radius

        point = proj.geopoint_to_pixpoint(self)
        for level in reversed(range(nlevels)):
            scale = 2 ** (level - (nlevels - 1))
            one_pixel_off = proj.pixpoint_to_geopoint(PixPoint((point.x + 1 / scale), point.y))
            degrees_per_pixel = abs(self.lon - one_pixel_off.lon)
            v1 = degrees_per_pixel * math.cos(self.lat * radian_per_degree)
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

# rel to width, height
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

        # TODO same as in meters per pix function, dedup? move to level class?
        level_scale = math.pow(2, metadata.nlevels - 1 - level.index)

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

        # TODO note: the following two lines can throw exceptions but we don't handle them cause TODO also in other places
        raw = requests.get(url)  # can throw requests.RequestException
        json = raw.json()  # can throw json.JSONDecodeError

        try:
            self.attribution = json['licence']
        except KeyError as e:
            self.attribution = ""

        try:
            self.name = json['display_name']
        except KeyError as e:
            self.error = True

class VideoEditor:
    """This is where the magic happens!"""

    def __init__(self, video, geopoint, reverse_geocode, capture_times, attribution, twitter_handle):
        """
        Ideally, this class would require fewer constructor parameters – which
        would be trivial if config and metadata were available globally. I tried
        that but it's just too yucky.
        """

        self.video = video
        self.geopoint = geopoint
        self.reverse_geocode = reverse_geocode
        self.capture_times = capture_times

        self.attribution = attribution
        self.twitter_handle = twitter_handle

        self.path = os.path.join(video.temp_dir, f"{video.tile.level.index}-{video.tile.row}-{video.tile.col}-processed.mp4")

    def __draw_text(self, text, fontsize):
        """Draws tightly-trimmed text, returning an ImageClip."""

        fnt = ImageFont.truetype("assets/Optician-Sans.otf", fontsize)

        # measure dimensions of text (this is an upper bound due to whitespace
        # included above/below letters), see
        # https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html#PIL.ImageDraw.ImageDraw.textsize
        dummy = Image.new("RGB", (0,0), (255,255,255))
        dd = ImageDraw.Draw(dummy)
        s = dd.textsize(text, font=fnt)

        # draw on canvas of measured dimensions, must have transparent background
        txt = Image.new("RGBA", s, (255,255,255,0))
        d = ImageDraw.Draw(txt)
        d.text((0,0), text, font=fnt, fill=(255,255,255,255))

        # crop to actual dimensions now that we can measure those
        txt = txt.crop(txt.getbbox())

        return ImageClip(np.array(txt))

    def __draw_progress_pieslice(self, size, completion):
        """
        Draws a pie-chart-esque progress meter at the given completion
        percentage, returning an ImageClip.
        """

        # pieslice doesn't antialias, so work around that by drawing at 3x scale
        # and then scaling down later
        # TODO refactor a bit
        factor = 3

        size = factor * size
        pie = Image.new("RGBA", (size,size), (255,255,255,0))
        d = ImageDraw.Draw(pie)
        d.pieslice([0,0,size-1,size-1], 0, 360, fill=(255,255,255,128))
        d.pieslice([0,0,size-1,size-1], -90, completion * 360 - 90, fill=(255,255,255,255))

        pie = pie.resize((int(size / factor), int(size / factor)))
        return ImageClip(np.array(pie))

    def process(self):
        """
        Do all the work of turning a downloaded raw video into one with location
        information, year meter, proper attribution, and a world map at the end.
        There are a bunch of long lines and magic numbers here, so tread
        lightly.
        """

        tile = self.video.tile
        clip = VideoFileClip(self.video.path)

        # TODO optionally resize to 1080p or 720p?

        width = clip.w
        height = clip.h

        result_framerate = 24
        images_per_second = 3
        margin = int(height / 30)
        final_frame_persist = 1
        endcard_crossfade = 0.67

        # process each frame separately
        frames = []
        for n, frame in enumerate(clip.iter_frames()):
            # TODO better name for clip
            clip = ImageClip(frame)

            pieslice_height = int(height / 13.5)  # manually dialled in to match font appearance
            pieslice = self.__draw_progress_pieslice(pieslice_height, n / (len(self.capture_times) - 1))
            pieslice = pieslice.set_position((width - pieslice.size[0] - margin, margin))
            year = self.__draw_text(self.capture_times[n], int(height / 7))
            year = year.set_position((width - year.size[0] - pieslice.size[0] - margin - margin, margin))

            # all of the following could be done outside the loop, which seems like it would be faster, but it's way slower
            # TODO still, define them outside?
            geopoint = self.__draw_text(self.geopoint.fancy(), int(height / 15))
            geopoint = geopoint.set_position((margin, margin))
            area_w = round(tile.level.kilometers(width), 2)
            area_h = round(tile.level.kilometers(height), 2)
            area = self.__draw_text(f"{area_w} x {area_h} km", int(height / 22))
            area = area.set_position((margin, margin + geopoint.size[1] + int(0.67 * area.size[1])))
            attribution = self.__draw_text(self.attribution, int(height / 40))
            attribution = attribution.set_position((width - attribution.size[0] - margin, height - attribution.size[1] - margin))

            clip = CompositeVideoClip([clip, pieslice, year, geopoint, area, attribution])
            frames.append(clip)

        # concatenate
        clips = [clip.set_duration(1 / images_per_second) for clip in frames]
        final_clip = clips[-1]
        clips[-1] = clips[-1].set_duration(1 / images_per_second + final_frame_persist)
        clip = concatenate_videoclips(clips)

        # add end card
        # https://commons.wikimedia.org/wiki/File:World_location_map_mono.svg
        # https://upload.wikimedia.org/wikipedia/commons/thumb/d/df/World_location_map_mono.svg/3840px-World_location_map_mono.svg.png
        background = ColorClip((width, height), color=(62,62,62))
        worldmap = ImageClip("assets/map.png")
        map_scale = background.size[0] / worldmap.size[0]
        worldmap = worldmap.resize((background.size[0], map_scale * worldmap.size[1]))
        pointer = ImageClip("assets/pointer.png").resize(map_scale)
        pointer_x = worldmap.size[0]/2 * (1 + (self.geopoint.lon / 180)) - pointer.size[0]/2
        pointer_y = worldmap.size[1]/2 * (1 - (self.geopoint.lat / 90)) - pointer.size[1]/2 # "-" since x increased from top while lat increases from bottom
        pointer = pointer.set_position((pointer_x,pointer_y))
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
            credit_lines.append("reverse geocoding: " + self.reverse_geocode.attribution.replace("©", "(c)"))
        credit = []
        fontsize = int(height / 45)
        accumulated_height = -fontsize
        for n, text in enumerate(reversed(credit_lines)):
            if n == len(credit_lines) - 1:
                fontsize = int(height / 30)

            line = self.__draw_text(text, fontsize)
            line_x = width/2 - line.size[0]/2
            line_y = height - line.size[1] - accumulated_height - fontsize - margin
            line = line.set_position((line_x, line_y))

            accumulated_height += fontsize
            credit.append(line)

        endcard_components = [background, worldmap, pointer, geopoint]
        if not self.reverse_geocode.error:
            endcard_components.append(geolocation)
        endcard = CompositeVideoClip(endcard_components + credit)
        endcard_fade = CompositeVideoClip([final_clip.set_duration(endcard_crossfade).fadeout(endcard_crossfade), endcard.set_duration(endcard_crossfade).crossfadein(endcard_crossfade)])
        endcard = endcard.set_duration(4)

        # finish!
        clip = concatenate_videoclips([clip, endcard_fade, endcard])
        clip = clip.set_fps(result_framerate)
        #clip.write_videofile(self.path)
        clip.write_videofile(self.path, logger=None)


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

    # load configuration either from config.ini or from a user-supplied file
    # (the latter option is handy if you want to run multiple instances of
    # this bot with different configurations)
    config_path = "config.ini"
    if (len(sys.argv) == 2):
        config_path = sys.argv[1]
    config = ConfigObj(config_path, unrepr=True)
    c = Config(config)

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
        # TODO fix ß, umlauts, accents, etc. at some point?
        if not reverse_geocode.error:
            logger.debug(reverse_geocode.name)

        logger.info("Editing video (this may take a minute or so)...")
        editor = VideoEditor(video, geopoint, reverse_geocode, metadata.capture_times, c.attribution, c.twitter_handle)
        editor.process()
        logger.debug(editor.path)

        tweeting = all(x is not None for x in [c.consumer_key, c.consumer_secret, c.access_token, c.access_token_secret])
        if tweeting:
            logger.info("Connecting to Twitter...")
            tweeter = Tweeter(c.consumer_key, c.consumer_secret, c.access_token, c.access_token_secret)

            osm_url = f"https://www.openstreetmap.org/#map={level.index}/{geopoint.lat}/{geopoint.lon}"
            googlemaps_url = f"https://www.google.com/maps/@{geopoint.lat},{geopoint.lon},{level.index}z"

            logger.info("Uploading video to Twitter...")
            media = tweeter.upload(editor.path)

            logger.info("Sending tweet...")
            # TODO more variables: area name from revgeocoder, like aerialbot? a x b km?
            tweet_text = c.tweet_text.format(
                latitude=geopoint.lat,
                longitude=geopoint.lon,
                point_fancy=geopoint.fancy(),
                osm_url=osm_url,
                googlemaps_url=googlemaps_url
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
