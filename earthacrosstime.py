import json
import math
import os
import sys

from configobj import ConfigObj
import requests
from moviepy.editor import *
from PIL import Image, ImageDraw, ImageFont
import numpy as np

class Config:
    def __init__(self, config):
        self.temp_dir = config['GENERAL']['temp_dir']
        self.timemachine_repository_url = config['TIMEMACHINE']['timemachine_repository_url']
        if (self.timemachine_repository_url[-1] != "/"):
            self.timemachine_repository_url += "/"

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
# TODO rename
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
    @classmethod
    def from_point_and_level(cls, point, level, metadata):
        level_scale = math.pow(2, metadata.nlevels - 1 - level.level)

        col = round((point.x - (metadata.video_width * level_scale * 0.5)) / (metadata.tile_width * level_scale))
        col = max(col, 0)
        col = min(col, metadata.level_info[level.level]['cols'] - 1)

        row = round((point.y - (metadata.video_height * level_scale * 0.5)) / (metadata.tile_height * level_scale))
        row = max(row, 0)
        row = min(row, metadata.level_info[level.level]['rows'] - 1)

        return cls(level, col, row)

class RawVideo:
    def __init__(self, tile, base_url, temp_dir):
        self.tile = tile
        self.base_url = base_url
        self.temp_dir = temp_dir

        self.url = f"{base_url}/{tile.level.level}/{tile.row}/{tile.col}.mp4"
        self.path = os.path.join(temp_dir, f"{tile.level.level}-{tile.row}-{tile.col}-raw.mp4")

    def __write_to_temp(self, video_data):
        if not os.path.isdir(self.temp_dir):
            os.makedirs(self.temp_dir)
        with open(self.path, 'wb') as f:
            f.write(video_data)

    def download(self):
        r = requests.get(self.url)
        if r.status_code != 200:
            raise ValueError(f"Unable to download tile from {self.url}, status code {r.status_code}.")

        self.__write_to_temp(r.content)

    def check_against(self, metadata):
        clip = VideoFileClip(self.path)
        assert clip.w == metadata.video_width
        assert clip.h == metadata.video_height
        assert clip.fps == metadata.fps
        assert clip.duration == metadata.frames / metadata.fps

class VideoEditor:
    def __init__(self, video, geopoint, capture_times):
        self.video = video
        self.geopoint = geopoint
        self.capture_times = capture_times

        self.path = os.path.join(video.temp_dir, f"{video.tile.level.level}-{video.tile.row}-{video.tile.col}-processed.mp4")

    def __draw_text(self, text, fontsize):
        fnt = ImageFont.truetype("Optician-Sans.otf", fontsize)  # TODO configurable

        # measure dimensions of text (this is an upper bound due to whitespace included above/below letters)
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

        # pieslice doesn't antialias, so work around that by drawing at 3x scale and then scaling down
        factor = 3

        size = factor * size
        pie = Image.new("RGBA", (size,size), (255,255,255,0))
        d = ImageDraw.Draw(pie)
        d.pieslice([0,0,size-1,size-1], 0, 360, fill=(255,255,255,128))
        d.pieslice([0,0,size-1,size-1], -90, completion * 360 - 90, fill=(255,255,255,255))

        pie = pie.resize((int(size / factor), int(size / factor)))
        return ImageClip(np.array(pie))

    # TODO make paths absolute via os.path.realpath(__file__), maybe also use that for temp and shapefile?
    def process(self):
        tile = self.video.tile
        clip = VideoFileClip(self.video.path)

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
            print("processing frame " + str(n+1))
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
            area_w = round(tile.level.meters_per_pixel * width / 1000, 2)
            area_h = round(tile.level.meters_per_pixel * height / 1000, 2)
            area = self.__draw_text(f"{area_w} x {area_h} km", int(height / 22))
            area = area.set_position((margin, margin + geopoint.size[1] + int(0.67 * area.size[1])))
            source = self.__draw_text("Source: Google Earth Timelapse (Google, Landsat, Copernicus)", int(height / 40))
            source = source.set_position((width - source.size[0] - margin, height - source.size[1] - margin))

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
        pointer_x = worldmap.size[0]/2 * (1 + (self.geopoint.lon / 180)) - pointer.size[0]/2
        pointer_y = worldmap.size[1]/2 * (1 - (self.geopoint.lat / 90)) - pointer.size[1]/2 # "-" since x increased from top while lat increases from bottom
        pointer = ImageClip("pointer.png").resize(map_scale).set_position((pointer_x,pointer_y))
        geopoint = self.__draw_text(self.geopoint.fancy(), int(1.3 * pointer.size[1]))
        geopoint_x = None
        geopoint_y = pointer_y + (pointer.size[1] - geopoint.size[1]) / 2
        if self.geopoint.lon < 0:
            geopoint_x = pointer_x + 1.5 * pointer.size[0]
        else:
            geopoint_x = pointer_x - geopoint.size[0] - 0.5 * pointer.size[0]
        geopoint = geopoint.set_position((geopoint_x, geopoint_y))

        credit_small_line2 = self.__draw_text(self.video.url, int(height / 40))
        credit_small_line2 = credit_small_line2.set_position((width/2 - credit_small_line2.size[0]/2, height - credit_small_line2.size[1] - margin))
        credit_small_line1 = self.__draw_text("twitter.com/earthacrosstime • bot source code: github.com/doersino/earthacrosstime • typeface: optician sans", int(height / 40))
        credit_small_line1 = credit_small_line1.set_position((width/2 - credit_small_line1.size[0]/2, height - credit_small_line1.size[1] - credit_small_line2.size[1] * 1.5 - margin))
        credit = self.__draw_text("@earthacrosstime", int(height / 22))
        credit = credit.set_position((width/2 - credit.size[0]/2, height - credit.size[1] - credit_small_line1.size[1] * 1.5 - credit_small_line2.size[1] * 2 - margin))

        endcard = CompositeVideoClip([background, worldmap, pointer, geopoint, credit, credit_small_line1, credit_small_line2])
        endcard_fade = CompositeVideoClip([final_clip.set_duration(endcard_crossfade).fadeout(endcard_crossfade), endcard.set_duration(endcard_crossfade).crossfadein(endcard_crossfade)])
        endcard = endcard.set_duration(4)

        # finish!
        clip = concatenate_videoclips([clip, endcard_fade, endcard])
        clip = clip.set_fps(result_framerate)
        clip.write_videofile(self.path)  # logger=None



def main():
    # load configuration either from config.ini or from a user-supplied file
    # (the latter option is handy if you want to run multiple instances of
    # ærialbot with different configurations)
    config_path = "config.ini"
    if (len(sys.argv) == 2):
        config_path = sys.argv[1]
    config = ConfigObj(config_path, unrepr=True)
    c = Config(config)

    # fetch metadata
    metadata = MetadataFetcher(c.timemachine_repository_url).fetch()

    #geopoint = GeoPoint(46.469029, 76.040519)
    geopoint = GeoPoint(37.190449, 128.478394)
    max_meters_per_pixel = 20

    proj = MercatorProjection(metadata.projection_bounds, metadata.width, metadata.height)
    point = proj.geopoint_to_point(geopoint)
    level = geopoint.determine_level(metadata.nlevels, proj, max_meters_per_pixel)
    tile = Tile.from_point_and_level(point, level, metadata)
    print(tile.__dict__)
    video = RawVideo(tile, f"{metadata.timemachine_repository_url}{metadata.dataset}", c.temp_dir)
    video.download()
    video.check_against(metadata)
    editor = VideoEditor(video, geopoint, metadata.capture_times)
    editor.process()
    print(editor.path)

if __name__ == "__main__":

    # log all exceptions  # TODO
    try:
        main()
    except Exception as e:
        #LOGGER.exception(e)
        raise e
