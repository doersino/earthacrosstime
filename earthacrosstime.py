import json
import math
import os
import sys

from configobj import ConfigObj
import requests
from moviepy.editor import *
from PIL import Image, ImageDraw, ImageFont
import numpy as np

M = None  # global for metadata

class Config:
    def __init__(self, config):
        self.temp_dir = config['GENERAL']['temp_dir']
        self.timemachine_repository_url = config['TIMEMACHINE']['timemachine_repository_url']

class Metadata:
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

class MetadataFetcher:
    def __init__(self, timemachine_repository_url):
        self.timemachine_repository_url = timemachine_repository_url

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

    def geopoint_to_point(self, geopoint):
        x = self.__interpolate(geopoint.lon, self.west, self.east, 0, self.width)
        y = self.__interpolate(
            self.__raw_project_lat(geopoint.lat),
            self.__raw_project_lat(self.north),
            self.__raw_project_lat(self.south),
            0,
            self.height
            )
        return Point(x, y)

    def point_to_geopoint(self, point):
        lon = self.__interpolate(point.x, 0, self.width, self.west, self.east);
        lat = self.__raw_unproject_lat(self.__interpolate(
            point.y,
            0,
            self.height,
            self.__raw_project_lat(self.north),
            self.__raw_project_lat(self.south)
            ))
        return GeoPoint(lat, lon)

class GeoPoint:
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

    # TODO based on https://github.com/CMU-CREATE-Lab/timemachine-viewer/blob/fb920433fcb8b5a7a84279142c5e27e549a852aa/js/org/gigapan/timelapse/scaleBar.js#L457
    # TODO cleanup
    def determine_level(self, nlevels, proj, max_meters_per_pixel):
        radian_per_degree = math.pi / 180
        earth_radius = 6371  # in kilometers
        c1 = radian_per_degree * earth_radius

        point = proj.geopoint_to_point(self)
        for level in reversed(range(nlevels)):
            scale = 2 ** (level - (nlevels - 1))
            one_pixel_off = proj.point_to_geopoint(Point((point.x + 1 / scale), point.y))
            degrees_per_pixel = abs(self.lon - one_pixel_off.lon)
            v1 = degrees_per_pixel * math.cos(self.lat * radian_per_degree)
            meters_per_pixel = c1 * v1 * 1000

            if meters_per_pixel > max_meters_per_pixel or level == 0:
                return ZoomLevel(level, meters_per_pixel)

class ZoomLevel:
    def __init__(self, level, meters_per_pixel):
        self.level = level
        self.meters_per_pixel = meters_per_pixel

# rel to width, height
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

class Tile:
    def __init__(self, level, col, row):
        self.level = level
        self.col = col
        self.row = row

    # TODO attribute this snippet: based on ...
    # TODO could move this as a init method into tile class, with static attributes for M.*
    @classmethod
    def from_point_and_level(cls, point, level):
        level_scale = math.pow(2, M.nlevels - 1 - level.level)

        col = round((point.x - (M.video_width * level_scale * 0.5)) / (M.tile_width * level_scale))
        col = max(col, 0)
        col = min(col, M.level_info[level.level]['cols'] - 1)

        row = round((point.y - (M.video_height * level_scale * 0.5)) / (M.tile_height * level_scale))
        row = max(row, 0)
        row = min(row, M.level_info[level.level]['rows'] - 1)

        return cls(level, col, row)

# TODO split into timelapsevideo and videoprocessor/finishing/processing that would get a timelapsevideo and some metadata?
# TODO assertions at top of process() could go into verify method?
class TimelapseVideo:
    def __init__(self, tile, temp_dir):
        self.tile = tile
        self.temp_dir = temp_dir
        base_filename = f"earthacrosstime-{tile.level.level}-{tile.row}-{tile.col}"
        self.raw = os.path.join(self.temp_dir, base_filename + "-raw.mp4")
        self.processed = os.path.join(self.temp_dir, base_filename + "-processed.mp4")

    def __write_to_temp(self, video_data):
        if not os.path.isdir(self.temp_dir):
            os.makedirs(self.temp_dir)
        with open(self.raw, 'wb') as f:
            f.write(video_data)

    def download(self):
        url = f"{M.timemachine_repository_url}{M.dataset}/{self.tile.level.level}/{self.tile.row}/{self.tile.col}.mp4"

        r = requests.get(url)
        if r.status_code != 200:
            raise ValueError(f"Unable to download tile from {url}, status code {r.status_code}.")

        self.__write_to_temp(r.content)

    def verify(self):
        clip = VideoFileClip(self.raw)
        assert clip.w == M.video_width
        assert clip.h == M.video_height
        assert clip.fps == M.fps
        assert clip.duration == M.frames / M.fps

    def __draw_text(self, text, fontsize):
        fnt = ImageFont.truetype("Optician-Sans.otf", fontsize)  # TODO configurable

        # measure dimensions of text (this is an upper bound due to whitespace included above/below letters)
        dummy = Image.new("RGB", (0,0), (255,255,255))
        dd = ImageDraw.Draw(dummy)
        s = dd.textsize(text, font=fnt)
        #print(s)

        # draw on canvas of measured dimensions, must have transparent background
        txt = Image.new("RGBA", s, (255,255,255,0))
        d = ImageDraw.Draw(txt)
        d.text((0,0), text, font=fnt, fill=(255,255,255,255))

        # crop to actual dimensions now that we can measure those
        txt = txt.crop(txt.getbbox())

        return np.array(txt)

    def __draw_progress_pieslice(self, size, completion):

        # pieslice doesn't antialias, so work around that by drawing at 3x scale and then scaling down
        factor = 3

        size = factor * size
        pie = Image.new("RGBA", (size,size), (255,255,255,0))
        d = ImageDraw.Draw(pie)
        d.pieslice([0,0,size-1,size-1], 0, 360, fill=(255,255,255,128))
        d.pieslice([0,0,size-1,size-1], -90, completion * 360 - 90, fill=(255,255,255,255))
        pie = pie.resize((int(size / factor), int(size / factor)))
        return np.array(pie)

    def process(self, point):
        result_framerate = 24
        images_per_second = 3
        margin = int(M.video_height / 30)
        final_frame_persist = 1
        endcard_crossfade = 0.5

        clip = VideoFileClip(self.raw)

        #fast_clip = concatenate_videoclips([ImageClip(frame).set_duration(2 / result_framerate) for frame in clip.iter_frames()])

        # process each frame separately
        frames = []
        for n, frame in enumerate(clip.iter_frames()):
            print("processing frame " + str(n+1))
            clip = ImageClip(frame)

            pieslice_height = int(M.video_height / 13.5)  # manually dialled in to match font appearance
            pieslice = ImageClip(self.__draw_progress_pieslice(pieslice_height, n / (M.frames - 1)))
            pieslice = pieslice.set_position((clip.size[0] - pieslice.size[0] - margin, margin))
            year = ImageClip(self.__draw_text(M.capture_times[n], int(M.video_height / 7)))
            year = year.set_position((clip.size[0] - year.size[0] - pieslice.size[0] - margin - margin, margin))

            # all of the following could be done outside the loop, which seems like it would be faster, but it's way slower
            # TODO still, define them outside?
            geopoint = ImageClip(self.__draw_text(point.fancy(), int(M.video_height / 15)))
            geopoint = geopoint.set_position((margin, margin))
            area_w = round(self.tile.level.meters_per_pixel * M.video_width / 1000, 2)
            area_h = round(self.tile.level.meters_per_pixel * M.video_height / 1000, 2)
            area = ImageClip(self.__draw_text(f"{area_w} x {area_h} km", int(M.video_height / 22)))
            area = area.set_position((margin, margin + geopoint.size[1] + int(0.67 * area.size[1])))
            source = ImageClip(self.__draw_text("Source: Google Earth Timelapse (Google, Landsat, Copernicus)", int(M.video_height / 40)))
            source = source.set_position((clip.size[0] - source.size[0] - margin, clip.size[1] - source.size[1] - margin))

            clip = CompositeVideoClip([clip, pieslice, year, geopoint, area, source])
            frames.append(clip)

        # concatenate
        clips = [clip.set_duration(1 / images_per_second) for clip in frames]
        final_clip = clips[-1]
        clips[-1] = clips[-1].set_duration(1 / images_per_second + final_frame_persist)
        clip = concatenate_videoclips(clips)

        # add end card
        # https://commons.wikimedia.org/wiki/File:World_location_map_mono.svg
        # https://upload.wikimedia.org/wikipedia/commons/thumb/d/df/World_location_map_mono.svg/3840px-World_location_map_mono.svg.png
        background = ColorClip(clip.size, color=(62,62,62))
        worldmap = ImageClip("map.png")
        map_scale = background.size[0] / worldmap.size[0]
        worldmap = worldmap.resize((background.size[0], map_scale * worldmap.size[1]))
        pointer = ImageClip("pointer.png").resize(map_scale)
        pointer_x = worldmap.size[0]/2 * (1 + (point.lon / 180)) - pointer.size[0]/2
        pointer_y = worldmap.size[1]/2 * (1 - (point.lat / 90)) - pointer.size[1]/2 # "-" since x increased from top while lat increases from bottom
        pointer = ImageClip("pointer.png").resize(map_scale).set_position((pointer_x,pointer_y))
        geopoint = ImageClip(self.__draw_text(point.fancy(), int(1.3 * pointer.size[1])))
        geopoint_x = None
        geopoint_y = pointer_y + (pointer.size[1] - geopoint.size[1]) / 2
        if point.lon < 0:
            geopoint_x = pointer_x + 1.5 * pointer.size[0]
        else:
            geopoint_x = pointer_x - geopoint.size[0] - 0.5 * pointer.size[0]
        geopoint = geopoint.set_position((geopoint_x, geopoint_y))

        # TODO refine this
        url = f"{M.timemachine_repository_url}{M.dataset}/{self.tile.level.level}/{self.tile.row}/{self.tile.col}.mp4"
        credit_small_line2 = ImageClip(self.__draw_text(url, int(M.video_height / 40)))
        credit_small_line2 = credit_small_line2.set_position((clip.size[0]/2 - credit_small_line2.size[0]/2, clip.size[1] - credit_small_line2.size[1] - margin))
        credit_small_line1 = ImageClip(self.__draw_text("twitter.com/earthacrosstime • bot source code: github.com/doersino/earthacrosstime • typeface: optician sans", int(M.video_height / 40)))
        credit_small_line1 = credit_small_line1.set_position((clip.size[0]/2 - credit_small_line1.size[0]/2, clip.size[1] - credit_small_line1.size[1] - credit_small_line2.size[1] * 1.5 - margin))
        credit = ImageClip(self.__draw_text("@earthacrosstime", int(M.video_height / 22)))
        credit = credit.set_position((clip.size[0]/2 - credit.size[0]/2, clip.size[1] - credit.size[1] - credit_small_line1.size[1] * 1.5 - credit_small_line2.size[1] * 2 - margin))

        endcard = CompositeVideoClip([background, worldmap, pointer, geopoint, credit, credit_small_line1, credit_small_line2])
        endcard_fade = CompositeVideoClip([final_clip.set_duration(endcard_crossfade), endcard.set_duration(endcard_crossfade).crossfadein(endcard_crossfade)])
        endcard = endcard.set_duration(4)

        # finish!
        clip = concatenate_videoclips([clip, endcard_fade, endcard])
        clip = clip.set_fps(result_framerate)
        clip.write_videofile(self.processed)  # logger=None



def main():
    global M

    # load configuration either from config.ini or from a user-supplied file
    # (the latter option is handy if you want to run multiple instances of
    # ærialbot with different configurations)
    config_path = "config.ini"
    if (len(sys.argv) == 2):
        config_path = sys.argv[1]
    config = ConfigObj(config_path, unrepr=True)
    C = Config(config)

    # fetch metadata
    M = MetadataFetcher(C.timemachine_repository_url).fetch()

    #geopoint = GeoPoint(46.469029, 76.040519)
    geopoint = GeoPoint(48.781809, 9.180357)
    max_meters_per_pixel = 20

    proj = MercatorProjection(M.projection_bounds, M.width, M.height)
    point = proj.geopoint_to_point(geopoint)
    level = geopoint.determine_level(M.nlevels, proj, max_meters_per_pixel)
    tile = Tile.from_point_and_level(point, level)
    print(tile.__dict__)
    timelapse = TimelapseVideo(tile, C.temp_dir)
    timelapse.download()
    timelapse.verify()
    timelapse.process(geopoint)
    print(timelapse.processed)

if __name__ == "__main__":

    # log all exceptions  # TODO
    try:
        main()
    except Exception as e:
        #LOGGER.exception(e)
        raise e
