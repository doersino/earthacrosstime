import json
import math
import os
import sys

from configobj import ConfigObj
import requests
from moviepy.editor import *
from PIL import Image, ImageDraw, ImageFont
import numpy as np

C = None  # global for config
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

    def point_to_latlon(self, point):
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
        self.lat = lat
        self.lon = lon

# rel to width, height
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

"""
def ZoomLevel:
    def __init__(self, level, meters_per_pixel):
        pass  # TODO use for a x b km overlay
"""

class TileProjection:
    def __init__(self):
        pass

    # TODO attribtute: based on aerialbot
    def determine_level(self, geopoint, max_meters_per_pixel):
        earth_circumference = 40075.016686 * 1000  # in meters, at the equator
        meters_per_pixel_at_level_0 = (earth_circumference / M.video_height) * math.cos(math.radians(geopoint.lat))

        for level in reversed(range(M.nlevels)):
            meters_per_pixel = meters_per_pixel_at_level_0 / (2 ** level)

            if meters_per_pixel > max_meters_per_pixel:
                return level  # TODO +1? nah?

        return 0

    # TODO attribtute: based on ...
    def point_and_level_to_tile(self, point, level):
        level_scale = math.pow(2, M.nlevels - 1 - level)

        col = round((point.x - (M.video_width * level_scale * 0.5)) / (M.tile_width * level_scale))
        col = max(col, 0)
        col = min(col, M.level_info[level]['cols'] - 1)

        row = round((point.y - (M.video_height * level_scale * 0.5)) / (M.tile_height * level_scale))
        row = max(row, 0)
        row = min(row, M.level_info[level]['rows'] - 1)

        return Tile(level, col, row)

class Tile:
    def __init__(self, level, col, row):
        self.level = level
        self.col = col
        self.row = row

class TimelapseVideo:
    def __init__(self, tile):
        self.tile = tile
        base_filename = f"earthacrosstime-{tile.level}-{tile.row}-{tile.col}"
        self.raw = os.path.join(C.temp_dir, base_filename + "-raw.mp4")
        self.raw_frames = os.path.join(C.temp_dir, base_filename + "-raw-frame%03d.png")
        self.processed = os.path.join(C.temp_dir, base_filename + "-processed.mp4")

    def __write_to_temp(self, video_data):
        if not os.path.isdir(C.temp_dir):
            os.makedirs(C.temp_dir)
        with open(self.raw, 'wb') as f:
            f.write(video_data)

    def download(self):
        url = f"{M.timemachine_repository_url}{M.dataset}/{self.tile.level}/{self.tile.row}/{self.tile.col}.mp4"

        r = requests.get(url)
        if r.status_code != 200:
            raise ValueError(f"Unable to download tile from {url}, status code {r.status_code}.")

        self.__write_to_temp(r.content)

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

    def __draw_progress_bar(self, width, height, completion):
        bar = Image.new("RGBA", (width,height), (255,255,255,0))
        d = ImageDraw.Draw(bar)
        d.rectangle([0,0,int(width * completion),height], fill=(255,255,255,255))
        return np.array(bar)

    def __draw_progress_pieslice(self, size, completion):
        # pieslice doesn't antialias, so work around that by drawing at 2x scale and then scaling down
        size = 3 * size
        pie = Image.new("RGBA", (size,size), (255,255,255,0))
        d = ImageDraw.Draw(pie)
        d.pieslice([0,0,size-1,size-1], 0, 360, fill=(255,255,255,128))
        d.pieslice([0,0,size-1,size-1], -90, completion * 360 - 90, fill=(255,255,255,255))
        pie = pie.resize((int(size / 3), int(size / 3)))
        return np.array(pie)

    def process(self):
        clip = VideoFileClip(self.raw)
        assert clip.w == M.video_width
        assert clip.h == M.video_height
        assert clip.fps == M.fps
        assert clip.duration == M.frames / M.fps

        margin = int(M.video_height / 30)

        # process each frame separately
        frames = []
        for n, frame in enumerate(clip.iter_frames()):
            print("processing frame " + str(n+1))
            clip = ImageClip(frame)
            # TODO can do everything except year afterwards since it's the same for each img
            geopoint = ImageClip(self.__draw_text("34°57'15.0\"N 128°18'23.8\"E", int(M.video_height / 15)))  # TODO actual text
            geopoint = geopoint.set_position((margin, margin))
            area = ImageClip(self.__draw_text("6.25 x 3.8 km", int(M.video_height / 22)))  # TODO actual text
            area = area.set_position((margin, margin + geopoint.size[1] + int(area.size[1] / 2)))
            year = ImageClip(self.__draw_text(M.capture_times[n], int(M.video_height / 8)))
            year = year.set_position((clip.size[0] - year.size[0] - margin, margin))
            source = ImageClip(self.__draw_text("Source: Google Earth Timelapse (Google, Landsat, Copernicus)", int(M.video_height / 40)))
            source = source.set_position((clip.size[0] - source.size[0] - margin, clip.size[1] - source.size[1] - margin))
            bar_height = int(M.video_height / 70)
            bar = ImageClip(self.__draw_progress_bar(M.video_width, bar_height, n / (M.frames - 1)))
            bar = bar.set_position((0, clip.size[1] - bar_height))

            pieslice_height = int(M.video_height / 10)
            pieslice = ImageClip(self.__draw_progress_pieslice(pieslice_height, n / (M.frames - 1)))
            pieslice = pieslice.set_position((200,200))

            clip = CompositeVideoClip([clip, geopoint, area, year, source, bar, pieslice])
            frames.append(clip)

        # concatenate
        images_per_second = 2
        clip = concatenate_videoclips([clip.set_duration(1 / images_per_second) for clip in frames])

        # add end card
        clip = clip.set_fps(24)
        clip.write_videofile(self.processed)

        #clip = clip.fx(vfx.speedx, 1 / M.fps * 2)
        #clip.write_images_sequence(self.raw_frames)

    def process_frame(self, frame):
        clip = ImageClip(frame)
        clip2 = ImageClip("test.png")
        clip = CompositeVideoClip([clip, clip2])

        return clip.get_frame(0)


def main():
    global C
    global M

    # load configuration either from config.ini or from a user-supplied file
    # (the latter option is handy if you want to run multiple instances of
    # ærialbot with different configurations)
    config_path = "config.ini"
    if (len(sys.argv) == 2):
        config_path = sys.argv[1]
    config = ConfigObj(config_path, unrepr=True)
    C = Config(config)

    M = MetadataFetcher(C.timemachine_repository_url).fetch()

    geopoint = GeoPoint(48.511865, 9.063931)
    max_meters_per_pixel = 10

    proj = MercatorProjection(M.projection_bounds, M.width, M.height)
    point = proj.geopoint_to_point(geopoint)
    tileproj = TileProjection()
    level = tileproj.determine_level(geopoint, max_meters_per_pixel)
    tile = tileproj.point_and_level_to_tile(point, level)
    print(tile.__dict__)
    timelapse = TimelapseVideo(tile)
    timelapse.download()
    timelapse.process()
    print(timelapse.processed)

if __name__ == "__main__":

    # log all exceptions  # TODO
    try:
        main()
    except Exception as e:
        #LOGGER.exception(e)
        raise e
